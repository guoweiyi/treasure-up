import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import threading
import math
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
from http.cookiejar import Cookie as JarCookie
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from app.config import settings
from app.models import Asset, MediaVariant, Video
from app.playback.ec3 import ec3_configuration, elementary_joc_complexity, preserve_ec3_configuration
from app.playback.tools import PlaybackError, probe_media
from app.storage.service import ingest_file, materialize_asset, resolve_asset
from .client import UA, retry_after
from .errors import IngestError


class _SilentLogger:
    def debug(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def _positive(value, scale=1):
    try:
        result = float(value) * scale
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def record_available_formats(video, part, info):
    """Capabilities actually returned for this account; never persist URLs."""
    formats = []
    for source in (info.get("formats") or info.get("requested_formats") or [info])[:500]:
        width, height = _positive(source.get("width")), _positive(source.get("height"))
        video_only, audio_only = source.get("acodec") == "none", source.get("vcodec") == "none"
        formats.append({"format_id": str(source.get("format_id") or ""),
            "quality": str(source.get("format_note") or source.get("format") or
                (f"{int(min(width, height))}p" if width and height else source.get("quality") or "unknown")),
            "width": int(width) if width else None, "height": int(height) if height else None,
            "fps": _positive(source.get("fps")), "video_codec": source.get("vcodec"),
            "audio_codec": source.get("acodec"), "dynamic_range": source.get("dynamic_range"),
            "video_bitrate_bps": _positive(source.get("vbr") or (source.get("tbr") if video_only else None), 1000),
            "audio_bitrate_bps": _positive(source.get("abr") or (source.get("tbr") if audio_only else None), 1000),
            "total_bitrate_bps": _positive(source.get("tbr"), 1000),
            "filesize_estimate": _positive(source.get("filesize") or source.get("filesize_approx"))})
    videos = [item for item in formats if item["video_codec"] != "none" and item["width"] and item["height"]]
    audios = [item for item in formats if item["audio_codec"] not in (None, "none")]
    metadata = deepcopy(video.metadata_json or {})
    quality = metadata.setdefault("source_quality", {})
    quality.update(observed_at=datetime.now(timezone.utc).isoformat(), scope="current_account")
    quality.setdefault("parts", {})[part.id] = {"cid": part.cid, "observed_at": quality["observed_at"], "status": "available" if formats else "unavailable",
        "formats": formats,
        "features": {"dolby_vision_available": any(f["dynamic_range"] == "DV" for f in videos),
            "hdr10_available": any(f["dynamic_range"] == "HDR10" for f in videos),
            "dolby_atmos_candidate": any(str(f["audio_codec"]).startswith(("ec-3", "eac3")) for f in audios)},
        "maximum": max(videos, key=lambda f: (min(f["width"], f["height"]), f["fps"] or 0, f["total_bitrate_bps"] or 0), default=None),
        "maximum_audio": max(audios, key=lambda f: f["audio_bitrate_bps"] or 0, default=None)}
    video.metadata_json = metadata


def record_access(video, source):
    video.metadata_json = {**(video.metadata_json or {}), "access": {
        "upower_exclusive": source.get("is_upower_exclusive") if type(source.get("is_upower_exclusive")) is bool else None,
        "can_play": source.get("is_upower_play") if type(source.get("is_upower_play")) is bool else None,
        "is_preview": source.get("is_upower_preview") if type(source.get("is_upower_preview")) is bool else None,
        "source": "view", "observed_at": datetime.now(timezone.utc).isoformat()}}


def validate_supporter_playinfo(source, playinfo, duration):
    if source.get("is_upower_play") is not True or source.get("is_upower_preview") is not False:
        raise IngestError("当前账号没有该充电专属视频的完整观看权限", code="supporter_access_required", retryable=False)
    if not isinstance(playinfo, dict) or playinfo.get("is_drm") or playinfo.get("drm_tech_type"):
        raise IngestError("充电视频未提供可归档的完整码流", code="unsupported_media", retryable=False)
    labels = playinfo.get("accept_description") or []
    if any("试看" in str(label) or "preview" in str(label).lower() for label in labels):
        raise IngestError("源站仅返回试看格式，未下载媒体", code="preview_only", retryable=False)
    lengths = [_positive(playinfo.get("timelength"), .001), _positive((playinfo.get("dash") or {}).get("duration"))]
    if not lengths[0] or not duration or any(value is not None and abs(value - duration) > 2 for value in lengths):
        raise IngestError("充电视频播放源与分P完整时长不符", code="preview_only", retryable=False)


def _measured_properties(video, audio, duration, size):
    try:
        fps = _positive(Fraction(str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0")))
    except (ValueError, ZeroDivisionError):
        fps = None
    return {"video_bitrate_bps": _positive(video.get("bit_rate")), "audio_bitrate_bps": _positive(audio.get("bit_rate")),
            "total_bitrate_bps": size * 8 / duration if duration > 0 else None,
            "total_bitrate_basis": "file_size_over_duration", "size_bytes": size, "duration_seconds": duration,
            "fps": fps, "measured_at": datetime.now(timezone.utc).isoformat()}


def selected_fingerprint(info, generation=0):
    formats = info.get("requested_formats") or info.get("requested_downloads") or [info]
    # signed URLs, titles and counts must not influence the stable demand key.
    selected = [{k: item.get(k) for k in ("format_id", "quality", "vcodec", "acodec", "width", "height", "fps", "language", "audio_channels", "dynamic_range")} for item in formats]
    return hashlib.sha256(json.dumps({"formats": selected, "generation": generation}, sort_keys=True).encode()).hexdigest()


def format_selector(policy, *, builder=None):
    quality = str(policy.get("quality", "best")).lower()
    heights = {"best": None, "4320p": 4320, "8k": 4320, "2160p": 2160, "4k": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
    if quality not in heights:
        raise IngestError("不支持的画质策略", code="invalid_policy", retryable=False)
    maximum_short_side = heights[quality]
    videos, audios = ["bestvideo"], ["bestaudio"]
    if policy.get("prefer_h264"):
        videos.insert(0, "bestvideo[vcodec~='^(avc1|h264)']")
    elif policy.get("prefer_dolby_vision", True):
        videos.insert(0, "bestvideo[dynamic_range=DV]")
    if policy.get("prefer_dolby_atmos", True):
        # E-AC-3 is only a candidate at extraction time. JOC must be verified
        # from the downloaded elementary stream before recording Atmos=True.
        audios.insert(0, "bestaudio[acodec~='^(ec-3|eac3)']")
    expression = "/".join(f"{video}+{audio}" for video in videos for audio in audios) + "/best"
    delegate = builder(expression) if builder else None

    def choose(context):
        if delegate is None:
            raise IngestError("格式选择器尚未绑定下载器", code="invalid_policy", retryable=False)
        def allowed(item):
            if maximum_short_side is None or item.get("vcodec") == "none":
                return True
            # yt-dlp has no short-side filter field. Use dimensions from actual
            # extracted formats, retaining its sorting and official merge code.
            try:
                width, height = float(item["width"]), float(item["height"])
                return width > 0 and height > 0 and min(width, height) <= maximum_short_side
            except (KeyError, ValueError, TypeError):
                return False
        formats = [item for item in context["formats"] if allowed(item)]
        return delegate({**context, "formats": formats,
            "has_merged_format": any("none" not in (f.get("acodec"), f.get("vcodec")) for f in formats),
            "incomplete_formats": all(f.get("vcodec") == "none" for f in formats) or all(f.get("acodec") == "none" for f in formats)})
    return choose


def _probe(path):
    try:
        result = subprocess.run([str(settings.ffprobe_path), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, timeout=120, check=True)
        data = json.loads(result.stdout)
        video = next(s for s in data["streams"] if s.get("codec_type") == "video")
        audio = next((s for s in data["streams"] if s.get("codec_type") == "audio"), {})
        duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
        return video, audio, duration
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
        raise IngestError("媒体探测失败，文件未发布", code="invalid_media") from None


def cleanup_media_scratch(part_id, fingerprint):
    if not re.fullmatch(r"[a-f0-9\-]{36}", str(part_id)) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        return
    target = settings.scratch_dir / "downloads" / str(part_id) / fingerprint
    if target.exists() and not target.is_symlink() and target.resolve().is_relative_to(settings.scratch_dir.resolve()):
        shutil.rmtree(target)


def archive_media(db, client, video, part, policy, *, guard=lambda: None, authorize_media=None):
    """Called under the runner's cross-worker video lock; recheck before download."""
    try:
        import yt_dlp
        from yt_dlp.networking.exceptions import HTTPError
        from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP
    except ImportError:
        raise IngestError("媒体下载组件未安装", code="missing_dependency", retryable=False) from None
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="treasure-media-", dir=settings.scratch_dir) as temp:
        folder = Path(temp)
        cookie_path = folder / "cookies.txt"
        lines = ["# Netscape HTTP Cookie File"]
        for cookie in client.cookies:
            lines.append("\t".join((cookie.domain, "TRUE" if cookie.include_subdomains else "FALSE", cookie.path, "TRUE" if cookie.secure else "FALSE", str(cookie.expires), cookie.name, cookie.value)))
        cookie_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(cookie_path, 0o600)
        except OSError:
            pass
        maximum = int(policy.get("max_download_bytes", policy.get("max_media_bytes", 20 * 1024**3)))
        if maximum < 1:
            raise IngestError("媒体大小预算无效", code="invalid_policy", retryable=False)

        started = time.monotonic()
        merge_evidence = {}
        downloaded_streams = {}
        download_progress_lock = threading.Lock()
        def hook(state):
            guard()
            if time.monotonic() - started > int(policy.get("download_timeout_seconds", 21600)):
                raise IngestError("媒体下载超过本轮时间预算", code="download_timeout")
            with download_progress_lock:
                identity = state.get("filename") or state.get("tmpfilename") or "media"
                downloaded_streams[identity] = max(downloaded_streams.get(identity, 0), int(state.get("downloaded_bytes") or 0))
                if sum(downloaded_streams.values()) > maximum:
                    raise IngestError("媒体超过本次大小预算", code="media_budget", retryable=False)
            if getattr(client, "media_progress", None):
                client.media_progress({"phase": "download", "part_id": part.id, "scope": "current_stream",
                    "downloaded_bytes": state.get("downloaded_bytes"),
                    "total_bytes": state.get("total_bytes") or state.get("total_bytes_estimate"),
                    "speed_bytes_per_second": state.get("speed"), "eta_seconds": state.get("eta")})

        format_selector(policy)  # Validate policy before touching the source.
        ffmpeg_location = shutil.which(str(settings.ffmpeg_path))
        if ffmpeg_location is None and Path(settings.ffmpeg_path).is_file():
            ffmpeg_location = str(Path(settings.ffmpeg_path).resolve())
        class ControlledMerger(FFmpegMergerPP):
            def run(self, info):
                destination = Path(info["filepath"])
                temporary = Path(str(destination) + ".merge")
                command = [str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-y"]
                for source in info["__files_to_merge"]:
                    command.extend(["-i", str(source)])
                for index, selected in enumerate(info["requested_formats"]):
                    if selected.get("vcodec") != "none":
                        command.extend(["-map", f"{index}:v:0"])
                    if selected.get("acodec") != "none":
                        command.extend(["-map", f"{index}:a:0"])
                command.extend(["-c", "copy", *_merge_options(info), "-f", "mp4", str(temporary)])
                _run_ffmpeg(command, temporary, maximum, int(policy.get("download_timeout_seconds", 21600)), guard)
                for source, selected in zip(info["__files_to_merge"], info["requested_formats"], strict=True):
                    if str(selected.get("acodec", "")).startswith(("ec-3", "eac3")):
                        reference = ec3_configuration(Path(source))
                        repaired = preserve_ec3_configuration(temporary, reference, check_active=guard)
                        merge_evidence["ec3_configuration_restored"] = repaired
                if any(item.get("dynamic_range") == "DV" or str(item.get("acodec", "")).startswith(("ec-3", "eac3")) for item in info["requested_formats"]):
                    hashes = {}
                    for source, selected in zip(info["__files_to_merge"], info["requested_formats"], strict=True):
                        for kind, codec_key in (("video", "vcodec"), ("audio", "acodec")):
                            if selected.get(codec_key) == "none":
                                continue
                            before = _packet_hash(Path(source), kind, guard)
                            after = _packet_hash(temporary, kind, guard)
                            if before != after:
                                raise IngestError("封装前后媒体码流校验不一致", code="payload_mismatch", retryable=False)
                            hashes[kind] = before
                    merge_evidence.update(payload_hashes=hashes, payloads_verified=True)
                temporary.replace(destination)
                return info["__files_to_merge"], info

        class PacedDownloader(yt_dlp.YoutubeDL):
            _treasure_cookies = tuple(client.cookies)

            def urlopen(self, request):
                guard()
                url = request if isinstance(request, str) else getattr(request, "url", "")
                host = urlsplit(url).hostname or ""
                source_request = host == "bilibili.com" or host.endswith(".bilibili.com")
                scope = client.request_context(url) if source_request and getattr(client, "request_context", None) else nullcontext()
                with scope:
                    if source_request and getattr(client, "before_request", None):
                        client.before_request(url)
                        # request_context refreshes rotated credentials under
                        # the account lock; replace the extractor's old jar too.
                        if tuple(client.cookies) != self._treasure_cookies:
                            self.cookiejar.clear()
                            for c in client.cookies:
                                self.cookiejar.set_cookie(JarCookie(0, c.name, c.value, None, False, c.domain,
                                    c.include_subdomains, c.domain.startswith("."), c.path, True, c.secure,
                                    c.expires or None, not bool(c.expires), None, None, {}))
                            self._treasure_cookies = tuple(client.cookies)
                    try:
                        return super().urlopen(request)
                    except HTTPError as error:
                        if error.status in (403, 412, 429):
                            delay = retry_after(error.response.headers.get("retry-after"))
                            error.close()
                            raise IngestError("媒体源站限流，暂停并等待冷却", code="rate_limited", retry_after_seconds=delay) from None
                        raise

            def run_pp(self, pp, info):
                if isinstance(pp, FFmpegMergerPP) and not isinstance(pp, ControlledMerger):
                    pp = ControlledMerger(self)
                return super().run_pp(pp, info)

        options = {
            "quiet": True, "no_warnings": True, "logger": _SilentLogger(),
            "cookiefile": str(cookie_path), "noplaylist": True,
            "format": "bestvideo+bestaudio/best", "outtmpl": str(folder / "media.%(ext)s"),
            "merge_output_format": "mp4", "ffmpeg_location": ffmpeg_location,
            "socket_timeout": 30, "retries": 2, "fragment_retries": 2,
            "concurrent_fragment_downloads": max(1, min(3, int(policy.get("fragment_concurrency", 1)))), "max_filesize": maximum,
            "http_headers": {"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
            "progress_hooks": [hook], "continuedl": True, "keepvideo": True,
            "skip_unavailable_fragments": False,
            "postprocessor_args": {"ffmpeg": ["-strict", "unofficial", "-movflags", "+faststart+write_colr"]},
        }
        if policy.get("download_rate_bytes"):
            options["ratelimit"] = max(1, int(policy["download_rate_bytes"]))
        try:
            with PacedDownloader(options) as downloader:
                downloader.format_selector = format_selector(policy, builder=downloader.build_format_selector)
                guard()
                if getattr(client, "before_video", None):
                    client.before_video()
                current = client.view(video.bvid)
                record_access(video, current)
                if authorize_media is not None:
                    authorize_media()
                matching = [p for p in current.get("pages", []) if str(p.get("cid")) == part.cid]
                if len(matching) != 1:
                    raise IngestError("分P来源已变化，请创建新归档轮次", code="source_changed", retryable=False)
                position = int(matching[0].get("page") or part.position)
                if current.get("is_upower_exclusive") is True:
                    if current.get("is_upower_play") is not True or current.get("is_upower_preview") is not False:
                        raise IngestError("当前账号没有该充电视频完整观看权限", code="supporter_access_required", retryable=False)
                    playinfo = client.playurl(video.bvid, part.cid)
                    validate_supporter_playinfo(current, playinfo, part.duration)
                    from yt_dlp.extractor.bilibili import BiliBiliIE
                    formats = BiliBiliIE(downloader).extract_formats(playinfo)
                    info = downloader.process_ie_result({"id": f"{video.bvid}_{part.cid}", "title": video.title,
                        "duration": playinfo["timelength"] / 1000, "formats": formats,
                        "webpage_url": f"https://www.bilibili.com/video/{video.bvid}/"}, download=False)
                else:
                    info = downloader.extract_info(f"https://www.bilibili.com/video/{video.bvid}/?p={position}", download=False)
                if not isinstance(info, dict) or info.get("_type") in ("playlist", "multi_video"):
                    raise IngestError("下载器未返回指定分P", code="invalid_media")
                checked = client.view(video.bvid).get("pages", [])
                if not any(str(p.get("cid")) == part.cid and int(p.get("page") or 1) == position for p in checked):
                    raise IngestError("提取期间分P顺序发生变化，请重试新轮次", code="source_changed", retryable=False)
                record_available_formats(video, part, info)
                db.commit()  # The available specification survives a later download failure.
                part.position = position
                fingerprint = selected_fingerprint(info, policy.get("acquisition_generation", 0))
                existing = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id, MediaVariant.format_key == fingerprint, MediaVariant.kind == "archive"))
                if existing:
                    try:
                        resolve_asset(db, existing.asset_id)
                    except Exception:
                        pass
                    else:
                        return existing, True
                requested = info.get("requested_formats") or [info]
                if sum(int(f.get("filesize") or f.get("filesize_approx") or 0) for f in requested) > maximum:
                    raise IngestError("媒体预计大小超过预算", code="media_budget", retryable=False)
                # A stable scratch path retains .part state on interrupted runs.
                # Its demand fingerprint excludes temporary source URLs.
                folder = settings.scratch_dir / "downloads" / str(part.id) / fingerprint
                if folder.is_symlink() or not folder.resolve().is_relative_to(settings.scratch_dir.resolve()):
                    raise IngestError("媒体临时目录越界", code="invalid_path", retryable=False)
                folder.mkdir(parents=True, exist_ok=True)
                downloader.params["outtmpl"]["default"] = str(folder / "media.%(ext)s")
                guard()
                downloader.process_info(info)
        except IngestError:
            raise
        except Exception as error:
            if any(code in str(error) for code in ("HTTP Error 412", "HTTP Error 429", "code -352", "code -401")):
                raise IngestError("媒体源站限流或风控，请稍后恢复", code="rate_limited") from None
            raise IngestError("媒体提取或下载失败；请检查账号权限及媒体工具", code="download_failed") from None
        # keepvideo retains the source streams for verification; only the final
        # merged basename may be published as the archive.
        files = [p for p in folder.glob("media.*") if p.name in ("media.mp4", "media.mkv", "media.webm", "media.flv")]
        if len(files) != 1 or files[0].stat().st_size <= 0:
            raise IngestError("下载未生成完整媒体文件", code="download_incomplete")
        path = files[0]
        if path.stat().st_size > maximum:
            raise IngestError("媒体超过大小预算", code="media_budget", retryable=False)
        guard()
        vstream, astream, duration = _probe(path)
        selected = info.get("requested_formats") or [info]
        audio_candidate = verify_selected_audio(selected, astream)
        # yt-dlp can reuse a complete but unpublished scratch merge after an
        # earlier verification failure, without invoking ControlledMerger.
        dolby = inspect_dolby(path, vstream, astream, guard=guard, repair_scratch=True)
        dolby["source_atmos_candidate"] = audio_candidate
        dolby["source_dv_candidate"] = any(item.get("dynamic_range") == "DV" for item in selected)
        if dolby["source_dv_candidate"] and not dolby["dolby_vision"]:
            raise IngestError("候选杜比视界文件缺少有效 DOVI/RPU 配置，未发布归档", code="dolby_metadata_missing", retryable=False)
        # Metadata duration is rounded to seconds. A percentage tolerance would
        # incorrectly accept long previews or truncated long-form recordings.
        if part.duration and abs(duration - part.duration) > 2:
            raise IngestError("媒体时长与分P元数据不符", code="duration_mismatch")
        if policy.get("verify_decode", False):
            _verify_decode(path, int(policy.get("verify_timeout_seconds", 7200)), guard)
        mime = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm", ".flv": "video/x-flv"}[path.suffix.lower()]
        asset = ingest_file(db, path, kind="media", mime_type=mime, profile_id=policy.get("storage_profile_id"))
        variant = existing or MediaVariant(part_id=part.id, kind="archive", format_key=fingerprint)
        variant.asset_id = asset.id
        variant.quality = str(info.get("format_note") or info.get("quality") or info.get("height") or "source")
        variant.width, variant.height = vstream.get("width"), vstream.get("height")
        variant.video_codec, variant.audio_codec = vstream.get("codec_name"), astream.get("codec_name")
        variant.duration = duration
        db.add(variant)
        db.flush()
        _record_source_properties(db, part, variant, vstream, **dolby, **merge_evidence,
            **_measured_properties(vstream, astream, duration, path.stat().st_size), archive_stream_copy=True)
        return variant, False


def _merge_options(info):
    formats = info.get("requested_formats") or [info]
    options = ["-strict", "unofficial", "-movflags", "+faststart+write_colr"]
    if any(str(item.get("vcodec", "")).startswith(("hvc", "hev", "dvh", "dvhe")) for item in formats):
        options.extend(["-tag:v", "hvc1"])
    if any(str(item.get("acodec", "")).startswith(("ec-3", "eac3")) for item in formats):
        options.extend(["-tag:a", "ec-3"])
    return options


def verify_selected_audio(selected, audio):
    candidate = any(str(item.get("acodec", "")).startswith(("ec-3", "eac3")) for item in selected)
    if candidate and audio.get("codec_name") != "eac3":
        raise IngestError("已选 EC-3 音轨与下载结果不符，未发布归档", code="audio_codec_mismatch", retryable=False)
    return candidate


def _colour_properties(stream):
    transfer = str(stream.get("color_transfer") or "unknown").lower()
    side_types = [str(item.get("side_data_type") or "").lower() for item in stream.get("side_data_list", [])]
    dovi = _dovi_configuration(stream)
    dolby = bool(dovi and dovi.get("rpu_present_flag") == 1 and (dovi.get("dv_profile") or 0) > 0)
    hdr = bool(dovi) or any("dovi" in name or "dolby vision" in name for name in side_types) or transfer in {"smpte2084", "arib-std-b67"} or any("mastering display" in name for name in side_types)
    return {"hdr": hdr, "wide_gamut": stream.get("color_primaries") == "bt2020",
            "dolby_vision": dolby, **{key: stream.get(key) for key in
                ("codec_name", "pix_fmt", "profile", "color_transfer", "color_primaries", "color_space", "color_range")}}


def _dovi_configuration(stream):
    for item in stream.get("side_data_list", []):
        if str(item.get("side_data_type", "")).lower() == "dovi configuration record":
            return {key: item[key] for key in ("dv_version_major", "dv_version_minor", "dv_profile", "dv_level",
                    "rpu_present_flag", "el_present_flag", "bl_present_flag", "dv_bl_signal_compatibility_id")
                    if type(item.get(key)) is int}
    return {}


def _packet_hash(path, kind, guard):
    with tempfile.TemporaryDirectory(prefix="treasure-payload-", dir=settings.scratch_dir) as directory:
        output = Path(directory) / "payload.sha256"
        command = [str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-i", str(path), "-map",
                   "0:v:0" if kind == "video" else "0:a:0", "-c", "copy", "-f", "hash", "-hash", "sha256", str(output)]
        _run_ffmpeg(command, output, 4096, 7200, guard)
        digest = output.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"SHA256=[0-9a-f]{64}", digest):
            raise IngestError("媒体码流摘要格式无效", code="payload_verification_failed")
        return digest.split("=", 1)[1]


def inspect_dolby(path, video, audio, *, guard=lambda: None, repair_scratch=False):
    """Evidence from files, not HEVC/E-AC-3 codec names or source advertisements."""
    config = _dovi_configuration(video)
    evidence = {"dolby_vision": bool(config and config.get("rpu_present_flag") == 1 and (config.get("dv_profile") or 0) > 0),
                "dovi": config, "dolby_atmos": False, "audio_codec": audio.get("codec_name"),
                "audio_profile": audio.get("profile"), "audio_channels": audio.get("channels"),
                "audio_sample_rate": _positive(audio.get("sample_rate")),
                "atmos_evidence": "not_eac3", "spatial_audio_output_verified": False}
    if audio.get("codec_name") != "eac3":
        return evidence
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="treasure-joc-", dir=settings.scratch_dir) as directory:
        sample = Path(directory) / "sample.eac3"
        # Probe an elementary sample independent of MP4 dec3/container labels.
        _run_ffmpeg([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
                     "-t", "10", "-c:a", "copy", "-f", "eac3", str(sample)], sample, 16 * 1024**2, 120, guard)
        try:
            guard()
            result = subprocess.run([str(settings.ffprobe_path), "-v", "error", "-show_streams", "-of", "json", str(sample)],
                                    capture_output=True, check=True, timeout=120)
            streams = json.loads(result.stdout)["streams"]
            raw = next(stream for stream in streams if stream.get("codec_type") == "audio")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
            raise IngestError("音频 JOC 原码流检测失败", code="dolby_verification_failed") from None
        joc = raw.get("codec_name") == "eac3" and raw.get("profile") == "Dolby Digital Plus + Dolby Atmos"
        evidence.update(dolby_atmos=joc, atmos_evidence="eac3_joc_bitstream_profile" if joc else "joc_not_reported",
                        raw_audio_profile=raw.get("profile"), audio_verification_sample_seconds=10)
        evidence["ec3"] = ec3_configuration(path)
        if joc and evidence["ec3"] and not evidence["ec3"]["joc"]:
            if not repair_scratch:
                raise IngestError("EC-3 初始化信息缺少已检测到的 JOC 配置", code="dolby_metadata_missing", retryable=False)
            # Only archive_media's unpublished download scratch may be repaired.
            # Source dec3 can itself lack JOC (AC-3 + dependent EC-3 sources).
            # Read the real complexity from the already bounded raw sample,
            # retaining the muxer's measured substream layout. Never guess 16.
            if path.is_symlink() or not path.resolve().is_relative_to((settings.scratch_dir / "downloads").resolve()):
                raise IngestError("拒绝修改已发布媒体或非下载临时文件", code="invalid_path", retryable=False)
            try:
                complexity = elementary_joc_complexity(sample.read_bytes())
                if complexity is None:
                    raise PlaybackError("JOC complexity was not present in the verified sample")
                reference = {**evidence["ec3"], "joc": True, "complexity_index_type_a": complexity}
                kinds = ("video", "audio") if video.get("codec_name") else ("audio",)
                before = {kind: _packet_hash(path, kind, guard) for kind in kinds}
                restored = preserve_ec3_configuration(path, reference, check_active=guard)
                if any(_packet_hash(path, kind, guard) != digest for kind, digest in before.items()):
                    raise PlaybackError("EC-3 repair changed encoded media")
            except PlaybackError:
                raise IngestError("无法安全恢复原码流中的 JOC 配置，未发布归档", code="dolby_metadata_missing", retryable=False) from None
            evidence.update(ec3=ec3_configuration(path), ec3_scratch_configuration_restored=restored,
                            ec3_repair_payload_hashes=before, ec3_configuration_evidence="eac3_addbsi_sample")
    return evidence


def _record_source_properties(db, part, variant, stream, **extra):
    video = db.get(Video, part.video_id)
    metadata = deepcopy(video.metadata_json or {})
    properties = metadata.setdefault("media_properties", {})
    properties[variant.id] = {**properties.get(variant.id, {}), "part_id": part.id,
                              **_colour_properties(stream), **extra}
    video.metadata_json = metadata


def _stop_process(process):
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run_ffmpeg(arguments, output, maximum, timeout, guard):
    """Poll the real process so cancellation, lease expiry and budgets interrupt it."""
    guard()
    try:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        raise IngestError("播放副本转换工具无法启动", code="missing_dependency", retryable=False) from None
    started = time.monotonic()
    try:
        while True:
            guard()
            if time.monotonic() - started > timeout:
                raise IngestError("播放副本转换超过时间预算", code="conversion_timeout")
            if output is not None and output.exists() and output.stat().st_size > maximum:
                raise IngestError("播放副本超过媒体大小预算", code="media_budget", retryable=False)
            status = process.poll()
            if status is not None:
                if status != 0:
                    raise IngestError("播放副本转换失败，原始归档已保留", code="conversion_failed")
                return
            time.sleep(0.2)
    finally:
        _stop_process(process)


def _verify_decode(path, timeout, guard):
    # Validation can run for hours on long media; use the same lease/cancel
    # polling as conversion and discard subprocess output instead of buffering.
    command = [str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-xerror", "-i", str(path),
               "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"]
    try:
        _run_ffmpeg(command, None, 0, timeout, guard)
    except IngestError as error:
        if error.code in {"conversion_failed", "conversion_timeout"}:
            raise IngestError("媒体完整解码验证失败或超过时间预算", code="decode_failed") from None
        raise


def _audio_compatible_copy(source, output, video, audio, duration, maximum, timeout, guard):
    """Encode only EC-3 audio; fail closed if the copied picture/signaling changes."""
    full = probe_media(source, check_active=guard, extended=True)
    videos = [s for s in full["streams"] if s.get("codec_type") == "video"]
    audios = [s for s in full["streams"] if s.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise IngestError("音频兼容副本要求单视频、单音轨，原档已保留", code="unsupported_media", retryable=False)
    dovi = _dovi_configuration(video)
    if any("dovi" in str(s.get("side_data_type", "")).lower() or "dolby vision" in str(s.get("side_data_type", "")).lower()
           for s in video.get("side_data_list", [])) and not dovi:
        raise IngestError("未知杜比视界配置，未生成兼容副本", code="dolby_metadata_missing", retryable=False)
    arguments = [str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-n", "-xerror", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0", "-sn", "-dn", "-map_metadata", "0", "-c:v", "copy",
        "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ac", "2", "-threads", "2",
        "-strict", "unofficial", "-movflags", "+faststart+write_colr"]
    if video.get("codec_name") == "hevc":
        arguments += ["-tag:v", "hvc1"]
    _run_ffmpeg([*arguments, str(output)], output, maximum, timeout, guard)
    if not output.is_file() or not 0 < output.stat().st_size <= maximum:
        raise IngestError("音频兼容副本文件不完整或超过预算", code="conversion_failed")
    guard()
    converted_video, converted_audio, converted_duration = _probe(output)
    fields = ("codec_name", "profile", "pix_fmt", "width", "height", "color_transfer", "color_primaries", "color_space", "color_range")
    if (any(video.get(field) is not None and converted_video.get(field) != video[field] for field in fields)
            or _dovi_configuration(converted_video) != dovi
            or converted_audio.get("codec_name") != "aac" or converted_audio.get("profile") != "LC"
            or converted_audio.get("channels") != 2 or converted_audio.get("channel_layout") != "stereo"
            or abs(converted_duration - duration) > 2):
        raise IngestError("音频兼容副本的视频信令、音轨或时长验证失败", code="invalid_playback")
    digest = _packet_hash(source, "video", guard)
    if _packet_hash(output, "video", guard) != digest:
        raise IngestError("音频兼容副本改变了视频码流，未发布", code="payload_verification_failed")
    evidence = {**_colour_properties(converted_video), "compatibility_mode": "audio_only",
        "video_stream_copy": True, "audio_transcoded": True, "source_audio_codec": audio["codec_name"],
        "audio_codec": "aac", "audio_profile": "LC", "audio_channels": 2, "audio_channel_layout": "stereo",
        "dolby_atmos": False, "ec3": {}, "dovi": dovi, "spatial_audio_output_verified": False,
        "video_payload_hash": digest, "video_payload_verified": True}
    return converted_video, converted_audio, converted_duration, evidence


def ensure_playback_variant(db, part, archive, policy, *, guard=lambda: None):
    """Preserve the archive; prefer picture-preserving AAC for EC-3 audio."""
    guard()
    audio_key = "video-copy-aac-v1:" + archive.asset_id
    key = "h264-aac-sdr-v1:" + archive.asset_id
    existing = None
    for candidate_key in ([audio_key] if archive.audio_codec == "eac3" else [audio_key, key]):
        candidate = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id,
            MediaVariant.kind == "playback", MediaVariant.format_key == candidate_key))
        if candidate:
            try:
                resolve_asset(db, candidate.asset_id)
            except Exception:
                pass
            else:
                return candidate, True
    guard()
    original_asset = db.get(Asset, archive.asset_id)
    if not original_asset:
        raise IngestError("原始媒体资产不存在", code="missing_archive", retryable=False)
    maximum = int(policy.get("max_download_bytes", 20 * 1024**3))
    timeout = int(policy.get("transcode_timeout_seconds", 7200))
    if maximum < 1 or timeout < 1:
        raise IngestError("播放副本预算无效", code="invalid_policy", retryable=False)
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="treasure-playback-", dir=settings.scratch_dir) as temporary:
        folder = Path(temporary)
        try:
            source = materialize_asset(db, archive.asset_id, folder / "source.media")
        except Exception:
            raise IngestError("原始媒体无法读取或校验失败", code="archive_unavailable") from None
        guard()
        vstream, astream, duration = _probe(source)
        source_stream = vstream
        colors = _colour_properties(vstream)
        _record_source_properties(db, part, archive, vstream)
        audio_only = astream.get("codec_name") == "eac3" and vstream.get("codec_name") in {"h264", "hevc", "av1"}
        evidence = {}
        if audio_only:
            key = audio_key
        existing = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id,
            MediaVariant.kind == "playback", MediaVariant.format_key == key))
        if not audio_only and (colors["hdr"] or colors["wide_gamut"]):
            _record_source_properties(db, part, archive, vstream, compatibility="hdr_conversion_unsupported")
            raise IngestError("HDR/广色域原档已保留；尚不支持经验证的 SDR 色彩转换", code="hdr_conversion_unsupported", retryable=False)
        compatible = (original_asset.mime_type == "video/mp4"
            and vstream.get("codec_name") == "h264"
            and vstream.get("pix_fmt") in {"yuv420p", "yuvj420p"}
            and (not astream or (astream.get("codec_name") == "aac"
                and astream.get("profile") in {None, "LC"}
                and int(astream.get("channels") or 2) <= 2)))
        if audio_only:
            output = folder / "playback.mp4"
            vstream, astream, duration, evidence = _audio_compatible_copy(
                source, output, vstream, astream, duration, maximum, timeout, guard)
            evidence.update(source_variant_id=archive.id, source_asset_id=archive.asset_id)
            asset = ingest_file(db, output, kind="media", mime_type="video/mp4", profile_id=policy.get("storage_profile_id"))
        elif compatible:
            asset = original_asset
        else:
            output = folder / "playback.mp4"
            arguments = [str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-n", "-i", str(source),
                "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-c:v", "libx264", "-preset", "medium",
                "-crf", "20", "-threads", "2", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ac", "2",
                "-movflags", "+faststart", str(output)]
            _run_ffmpeg(arguments, output, maximum, timeout, guard)
            if not output.is_file() or not 0 < output.stat().st_size <= maximum:
                raise IngestError("播放副本文件不完整或超过预算", code="conversion_failed")
            guard()
            converted_video, converted_audio, converted_duration = _probe(output)
            converted_color = _colour_properties(converted_video)
            if (converted_video.get("codec_name") != "h264" or converted_video.get("pix_fmt") != "yuv420p"
                or (astream and converted_audio.get("codec_name") != "aac")
                or abs(converted_duration - duration) > 2
                or converted_color["hdr"] or converted_color["wide_gamut"]):
                raise IngestError("播放副本编解码、时长或色彩验证失败", code="invalid_playback")
            asset = ingest_file(db, output, kind="media", mime_type="video/mp4", profile_id=policy.get("storage_profile_id"))
            vstream, astream, duration = converted_video, converted_audio, converted_duration
        variant = existing or MediaVariant(part_id=part.id, kind="playback", format_key=key)
        variant.asset_id, variant.quality = asset.id, archive.quality
        variant.width, variant.height = vstream.get("width", 0), vstream.get("height", 0)
        variant.video_codec, variant.audio_codec = vstream.get("codec_name", ""), astream.get("codec_name", "")
        variant.duration = duration
        variant.metadata_json = evidence
        db.add(variant)
        db.flush()
        _record_source_properties(db, part, archive, source_stream, compatibility="ready", playback_variant_id=variant.id)
        _record_source_properties(db, part, variant, vstream,
            **_measured_properties(vstream, astream, duration, asset.size), **evidence, compatibility="ready")
        return variant, compatible
