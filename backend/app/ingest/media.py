import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models import Asset, MediaVariant, Video
from app.storage.service import ingest_file, materialize_asset, resolve_asset
from .client import UA
from .errors import IngestError


class _SilentLogger:
    def debug(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


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
    expression = "bestvideo+bestaudio/best"
    if policy.get("prefer_h264"):
        expression = ("bestvideo[vcodec~='^(avc1|h264)']+bestaudio[acodec~='^(mp4a|aac)']/"
                      "best[vcodec~='^(avc1|h264)'][acodec~='^(mp4a|aac)']/" + expression)
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
        if duration <= 0:
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


def archive_media(db, client, video, part, policy, *, guard=lambda: None):
    """Called under the runner's cross-worker video lock; recheck before download."""
    try:
        import yt_dlp
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
        def hook(state):
            guard()
            if time.monotonic() - started > int(policy.get("download_timeout_seconds", 21600)):
                raise IngestError("媒体下载超过本轮时间预算", code="download_timeout")
            if int(state.get("downloaded_bytes") or 0) > maximum:
                raise IngestError("媒体超过本次大小预算", code="media_budget", retryable=False)

        format_selector(policy)  # Validate policy before touching the source.
        ffmpeg_location = shutil.which(str(settings.ffmpeg_path))
        if ffmpeg_location is None and Path(settings.ffmpeg_path).is_file():
            ffmpeg_location = str(Path(settings.ffmpeg_path).resolve())
        options = {
            "quiet": True, "no_warnings": True, "logger": _SilentLogger(),
            "cookiefile": str(cookie_path), "noplaylist": True,
            "format": "bestvideo+bestaudio/best", "outtmpl": str(folder / "media.%(ext)s"),
            "merge_output_format": "mp4", "ffmpeg_location": ffmpeg_location,
            "socket_timeout": 30, "retries": 2, "fragment_retries": 2,
            "concurrent_fragment_downloads": 1, "max_filesize": maximum,
            "http_headers": {"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
            "progress_hooks": [hook], "continuedl": True,
            "postprocessor_args": {"ffmpeg": ["-movflags", "+faststart"]},
        }
        if policy.get("download_rate_bytes"):
            options["ratelimit"] = max(1, int(policy["download_rate_bytes"]))
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.format_selector = format_selector(policy, builder=downloader.build_format_selector)
                guard()
                current = client.view(video.bvid)
                matching = [p for p in current.get("pages", []) if str(p.get("cid")) == part.cid]
                if len(matching) != 1:
                    raise IngestError("分P来源已变化，请创建新归档轮次", code="source_changed", retryable=False)
                position = int(matching[0].get("page") or part.position)
                info = downloader.extract_info(f"https://www.bilibili.com/video/{video.bvid}/?p={position}", download=False)
                if not isinstance(info, dict) or info.get("_type") in ("playlist", "multi_video"):
                    raise IngestError("下载器未返回指定分P", code="invalid_media")
                checked = client.view(video.bvid).get("pages", [])
                if not any(str(p.get("cid")) == part.cid and int(p.get("page") or 1) == position for p in checked):
                    raise IngestError("提取期间分P顺序发生变化，请重试新轮次", code="source_changed", retryable=False)
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
        files = [p for p in folder.glob("media.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".flv")]
        if len(files) != 1 or files[0].stat().st_size <= 0:
            raise IngestError("下载未生成完整媒体文件", code="download_incomplete")
        path = files[0]
        if path.stat().st_size > maximum:
            raise IngestError("媒体超过大小预算", code="media_budget", retryable=False)
        guard()
        vstream, astream, duration = _probe(path)
        # Metadata duration is rounded to seconds. A percentage tolerance would
        # incorrectly accept long previews or truncated long-form recordings.
        if part.duration and abs(duration - part.duration) > 2:
            raise IngestError("媒体时长与分P元数据不符", code="duration_mismatch")
        if policy.get("verify_decode", False):
            try:
                subprocess.run([str(settings.ffmpeg_path), "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"], capture_output=True, timeout=int(policy.get("verify_timeout_seconds", 7200)), check=True)
            except (OSError, subprocess.SubprocessError):
                raise IngestError("媒体完整解码验证失败", code="decode_failed") from None
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
        _record_source_properties(db, part, variant, vstream)
        return variant, False


def _colour_properties(stream):
    transfer = str(stream.get("color_transfer") or "unknown").lower()
    side_types = [str(item.get("side_data_type") or "").lower() for item in stream.get("side_data_list", [])]
    dolby = any("dovi" in name or "dolby vision" in name for name in side_types)
    hdr = dolby or transfer in {"smpte2084", "arib-std-b67"} or any("mastering display" in name for name in side_types)
    return {"hdr": hdr, "wide_gamut": stream.get("color_primaries") == "bt2020",
            "dolby_vision": dolby, **{key: stream.get(key) for key in
                ("codec_name", "pix_fmt", "profile", "color_transfer", "color_primaries", "color_space", "color_range")}}


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
            if output.exists() and output.stat().st_size > maximum:
                raise IngestError("播放副本超过媒体大小预算", code="media_budget", retryable=False)
            status = process.poll()
            if status is not None:
                if status != 0:
                    raise IngestError("播放副本转换失败，原始归档已保留", code="conversion_failed")
                return
            time.sleep(0.2)
    finally:
        _stop_process(process)


def ensure_playback_variant(db, part, archive, policy, *, guard=lambda: None):
    """Preserve the archive; alias compatible bytes or create a verified SDR copy."""
    guard()
    key = "h264-aac-sdr-v1:" + archive.asset_id
    existing = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id,
        MediaVariant.kind == "playback", MediaVariant.format_key == key))
    if existing:
        try:
            resolve_asset(db, existing.asset_id)
        except Exception:
            pass
        else:
            return existing, True
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
        if colors["hdr"] or colors["wide_gamut"]:
            _record_source_properties(db, part, archive, vstream, compatibility="hdr_conversion_unsupported")
            raise IngestError("HDR/广色域原档已保留；尚不支持经验证的 SDR 色彩转换", code="hdr_conversion_unsupported", retryable=False)
        compatible = (original_asset.mime_type == "video/mp4"
            and vstream.get("codec_name") == "h264"
            and vstream.get("pix_fmt") in {"yuv420p", "yuvj420p"}
            and (not astream or (astream.get("codec_name") == "aac"
                and astream.get("profile") in {None, "LC"}
                and int(astream.get("channels") or 2) <= 2)))
        if compatible:
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
        db.add(variant)
        db.flush()
        _record_source_properties(db, part, archive, source_stream, compatibility="ready", playback_variant_id=variant.id)
        return variant, compatible
