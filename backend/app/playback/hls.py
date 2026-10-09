from __future__ import annotations

import json
import math
import re
import threading
from collections import OrderedDict
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.config import settings
from app.models import Asset, AssetRef, MediaVariant
from app.storage.service import ingest_file, ingest_workspace, read_asset_bytes
from .source import source_scope
from .tools import PlaybackError, probe_media, run_tool
from .ec3 import ec3_configuration, preserve_ec3_configuration
from .fragments import fragment_movie_timescale, segment_mux_options, finalize_fragmented_hls

_INDEX_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LOCK = threading.Lock()


def dovi_configuration(stream):
    for side in stream.get("side_data_list", []):
        if str(side.get("side_data_type", "")).lower() == "dovi configuration record":
            return {key: side[key] for key in ("dv_version_major", "dv_version_minor", "dv_profile", "dv_level",
                "rpu_present_flag", "el_present_flag", "bl_present_flag", "dv_bl_signal_compatibility_id", "dv_md_compression") if key in side}
    return {}


def packet_digest(path, kind, *, check_active=None):
    output, _ = run_tool([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-protocol_whitelist", "file,crypto,data",
        "-i", str(path), "-map", "0:v:0" if kind == "video" else "0:a:0", "-c", "copy", "-f", "hash", "-hash", "sha256", "-"],
        check_active=check_active, capture=True, reject_errors=True)
    value = output.decode("ascii").strip()
    if not re.fullmatch(r"SHA256=[0-9a-f]{64}", value):
        raise PlaybackError("Media packet verification did not produce a valid digest")
    return value.split("=", 1)[1]


def flac_streaminfo_digest(path, *, check_active=None):
    """FFprobe exposes the MP4 dfLa STREAMINFO as exactly 34 extradata bytes."""
    raw, _ = run_tool([str(settings.ffprobe_path), "-v", "error", "-protocol_whitelist", "file,crypto,data",
        "-select_streams", "a:0", "-show_entries", "stream=codec_name,extradata_size,extradata_hash",
        "-show_data_hash", "sha256", "-of", "json", str(path)], check_active=check_active, timeout=120, capture=True, reject_errors=True)
    try:
        streams = json.loads(raw)["streams"]
        stream = streams[0]
        digest = stream["extradata_hash"]
        if (len(streams) != 1 or stream["codec_name"] != "flac" or stream["extradata_size"] != 34
                or not re.fullmatch(r"SHA256:[0-9a-f]{64}", digest)):
            raise ValueError()
        return digest.split(":", 1)[1]
    except (ValueError, KeyError, IndexError, TypeError):
        raise PlaybackError("FLAC STREAMINFO configuration could not be verified") from None


def stream_copy_digests(path, *, check_active=None, include_audio=True):
    """Hash selected compressed streams in one read, without decoding the media."""
    maps = ["-map", "0:v:0"] + (["-map", "0:a:0"] if include_audio else [])
    raw, _ = run_tool([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-protocol_whitelist", "file,crypto,data",
        "-i", str(path), *maps, "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"],
        check_active=check_active, capture=True, reject_errors=True)
    try:
        lines = raw.decode("ascii").strip().splitlines()
        expected = [(0, "v", "video")] + ([(1, "a", "audio")] if include_audio else [])
        if len(lines) != len(expected):
            raise ValueError()
        result = {}
        for line, (number, kind, name) in zip(lines, expected):
            match = re.fullmatch(rf"{number},{kind},SHA256=([0-9a-f]{{64}})", line)
            if not match:
                raise ValueError()
            result[name] = match.group(1)
        return result
    except (UnicodeError, ValueError):
        raise PlaybackError("Media stream verification did not produce valid digests") from None


def _asset_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise PlaybackError("Invalid HLS asset reference") from None


def parse_generated_playlist(text):
    """Accept only the local, finite, unencrypted playlist emitted by this packager."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U" or lines[-1] != "#EXT-X-ENDLIST":
        raise PlaybackError("HLS package is incomplete")
    segments, pending, initialization = [], None, None
    for line in lines[1:]:
        if line.startswith('#EXT-X-MAP:'):
            if line != '#EXT-X-MAP:URI="init.mp4"' or initialization:
                raise PlaybackError("Unexpected HLS initialization reference")
            initialization = "init.mp4"
        elif line.startswith("#EXTINF:"):
            if pending is not None:
                raise PlaybackError("Missing HLS segment")
            try:
                pending = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                raise PlaybackError("Invalid HLS duration") from None
            if not math.isfinite(pending) or pending <= 0:
                raise PlaybackError("Invalid HLS duration")
        elif not line.startswith("#"):
            if pending is None or not re.fullmatch(r"segment_[0-9]{6,12}\.m4s", line):
                raise PlaybackError("Unexpected HLS segment reference")
            segments.append({"filename": line, "duration": pending})
            pending = None
        elif line.startswith(("#EXT-X-KEY", "#EXT-X-BYTERANGE", "#EXT-X-DISCONTINUITY", "#EXT-X-STREAM-INF")):
            raise PlaybackError("Unsupported HLS structure")
    if not initialization or not segments or pending is not None or len({s['filename'] for s in segments}) != len(segments):
        raise PlaybackError("HLS package is incomplete")
    return initialization, segments


def validate_index(index):
    if not isinstance(index, dict) or index.get("schema") != "treasure.hls.v1" or index.get("stream_copy") is not True:
        raise PlaybackError("Invalid HLS package index")
    _asset_id(index.get("source_asset_id"))
    _asset_id(index.get("init_asset_id"))
    segments = index.get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= 100000:
        raise PlaybackError("Invalid HLS segment count")
    for sequence, segment in enumerate(segments):
        if not isinstance(segment, dict) or segment.get("sequence") != sequence:
            raise PlaybackError("Invalid HLS sequence")
        _asset_id(segment.get("asset_id"))
        duration = segment.get("duration")
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise PlaybackError("Invalid HLS duration")
    target = index.get("target_duration")
    if not isinstance(target, int) or target < math.ceil(max(s["duration"] for s in segments)):
        raise PlaybackError("Invalid HLS target duration")
    return index


def load_hls_index(db, index_asset_id):
    return load_hls_package(db, index_asset_id)[0]


def load_hls_package(db, index_asset_id):
    """Cache validation and authorization membership with the immutable index."""
    global _CACHE_BYTES
    asset = db.get(Asset, index_asset_id)
    if not asset:
        raise PlaybackError("HLS index asset is missing")
    with _CACHE_LOCK:
        cached = _INDEX_CACHE.get(asset.sha256)
        if cached is not None:
            _INDEX_CACHE.move_to_end(asset.sha256)
            return cached[0], cached[2]
    try:
        index = validate_index(json.loads(read_asset_bytes(db, index_asset_id, max_bytes=32 * 1024**2)))
        # Only immutable content is cached. Authentication and session routes never are.
        allowed = frozenset([index["init_asset_id"], *(s["asset_id"] for s in index["segments"])])
        with _CACHE_LOCK:
            # Parallel cache misses may finish in either order; count content once.
            cached = _INDEX_CACHE.get(asset.sha256)
            if cached is not None:
                _INDEX_CACHE.move_to_end(asset.sha256)
                return cached[0], cached[2]
            _INDEX_CACHE[asset.sha256] = (index, asset.size, allowed)
            _CACHE_BYTES += asset.size
            while len(_INDEX_CACHE) > 100 or _CACHE_BYTES > 32 * 1024**2:
                _, (_, size, _) = _INDEX_CACHE.popitem(last=False)
                _CACHE_BYTES -= size
        return index, allowed
    except (ValueError, UnicodeError):
        raise PlaybackError("Invalid HLS package index") from None


def package_asset_ids(index):
    validate_index(index)
    return list(dict.fromkeys([index["init_asset_id"], *(s["asset_id"] for s in index["segments"])]))


def render_manifest(index, url_for_asset, *, allow_external=False):
    """URLs are generated for the authenticated session, never stored in the index."""
    validate_index(index)
    def uri(asset_id):
        value = str(url_for_asset(asset_id))
        if allow_external and value.startswith(("https://", "http://")):
            from app.storage.delivery import validate_delivery_url
            return validate_delivery_url(value)
        if not value.startswith("/") or value.startswith("//") or any(c in value for c in '\r\n"\\'):
            raise PlaybackError("Manifest routes must be same-origin API paths")
        return value
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", f'#EXT-X-TARGETDURATION:{index["target_duration"]}',
             "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", f'#EXT-X-MAP:URI="{uri(index["init_asset_id"])}"']
    for segment in index["segments"]:
        lines.extend([f'#EXTINF:{segment["duration"]:.6f},', uri(segment["asset_id"])])
    return "\n".join([*lines, "#EXT-X-ENDLIST", ""])


def package_variant(db, variant_id, *, profile_id=None, segment_seconds=6, check_active=None, source_provider=None):
    """Remux one selected video/audio stream to fMP4 without encoding either stream."""
    guard = check_active or (lambda: None)
    source_variant = db.get(MediaVariant, variant_id)
    if not source_variant or source_variant.kind == "hls":
        raise PlaybackError("A source media variant is required")
    if not isinstance(segment_seconds, int) or not 2 <= segment_seconds <= 30:
        raise PlaybackError("HLS segment target must be between 2 and 30 seconds")
    format_key = f"hls-copy-v3:{segment_seconds}:" + source_variant.asset_id
    existing = db.scalar(select(MediaVariant).where(MediaVariant.part_id == source_variant.part_id,
        MediaVariant.kind == "hls", MediaVariant.format_key == format_key))
    if existing:
        load_hls_index(db, existing.asset_id)
        return existing
    with (ingest_workspace(db, profile_id=profile_id, prefix="hls-") as workspace,
          source_scope(db, source_provider=source_provider, check_active=guard) as provider):
        folder = workspace.path
        guard()
        source = provider(source_variant.asset_id)
        probe = probe_media(source, check_active=guard)
        videos = [s for s in probe["streams"] if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
        audios = [s for s in probe["streams"] if s.get("codec_type") == "audio"]
        # Silently dropping additional audio tracks would violate the preservation promise.
        if len(videos) != 1 or len(audios) > 1:
            raise PlaybackError("HLS packaging currently requires one video and at most one audio track; use the original file")
        dovi = dovi_configuration(videos[0])
        if any("dovi" in str(s.get("side_data_type", "")).lower() or "dolby vision" in str(s.get("side_data_type", "")).lower()
               for s in videos[0].get("side_data_list", [])) and not dovi:
            raise PlaybackError("Unknown Dolby Vision configuration cannot be safely packaged; use the original file")
        if videos[0].get("codec_name") not in {"h264", "hevc", "av1"} or (audios and audios[0].get("codec_name") not in {"aac", "ac3", "eac3", "flac"}):
            raise PlaybackError("This codec cannot use the available stream-copy HLS profile; use the original file")
        audio_evidence = {"dolby_atmos": False, "ec3": {}}
        if audios and audios[0].get("codec_name") == "eac3":
            from app.ingest.media import inspect_dolby
            audio_evidence = inspect_dolby(source, videos[0], audios[0], guard=guard)
        arguments = [str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-n", "-protocol_whitelist", "file,crypto,data",
            "-i", str(source), "-map", f'0:{videos[0]["index"]}']
        if audios:
            arguments += ["-map", f'0:{audios[0]["index"]}']
        arguments += ["-c", "copy", "-map_metadata", "0", "-strict", "unofficial"]
        if videos[0].get("codec_name") == "hevc":
            arguments += ["-tag:v", "hvc1"]
        if audios and audios[0].get("codec_name") == "eac3":
            arguments += ["-tag:a", "ec-3"]
        # A GOP-aligned HLS segment can still be read progressively: retain one
        # MOV context and put ~1-second moof/mdat pairs inside each segment.
        # Do not pass frag_duration to hlsenc: it can put media inside init.mp4.
        arguments += segment_mux_options(folder, segment_seconds=segment_seconds,
            movie_timescale=fragment_movie_timescale([*videos, *audios]))
        run_tool(arguments, check_active=guard, reject_errors=True)
        finalize_fragmented_hls(folder, check_active=guard)
        init_name, segments = parse_generated_playlist((folder / "index.m3u8").read_text(encoding="utf-8"))
        if audio_evidence.get("ec3"):
            restored = preserve_ec3_configuration(folder / init_name, audio_evidence["ec3"], check_active=guard)
            audio_evidence["ec3_configuration_restored"] = restored
        # Probe the completed local package to catch mismatched streams/truncation before publication.
        packaged = probe_media(folder / "index.m3u8", check_active=guard)
        packaged_audios = [s for s in packaged["streams"] if s.get("codec_type") == "audio"]
        if (audios and audios[0].get("codec_name") == "eac3" and len(packaged_audios) == 1
                and any(audios[0].get(field) is not None and packaged_audios[0].get(field) != audios[0][field]
                        for field in ("channels", "channel_layout"))):
            # The initial probe can see only the AC-3 core when a high-bitrate
            # video fragment uses its byte budget. Re-probe with finite limits;
            # every stream property, JOC/DOVI and full packet hash still must match.
            packaged = probe_media(folder / "index.m3u8", check_active=guard, extended=True)
            audio_evidence["hls_audio_probe"] = {"probesize_bytes": 33554432, "analyzeduration_us": 10000000}
        streams = packaged["streams"]
        if audios and audios[0].get("codec_name") == "eac3":
            init_ec3 = ec3_configuration(folder / init_name)
            if not init_ec3 or (audio_evidence["ec3"] and init_ec3 != audio_evidence["ec3"]):
                raise PlaybackError("EC-3 audio configuration was not preserved in HLS initialization")
            if audio_evidence["dolby_atmos"] and not init_ec3["joc"]:
                raise PlaybackError("Atmos JOC signaling was not preserved in HLS initialization")
            audio_evidence.update(ec3=init_ec3, ec3_configuration_verified=True)
        for original in [*videos, *audios]:
            candidates = [s for s in streams if s.get("codec_type") == original.get("codec_type")]
            if len(candidates) != 1:
                raise PlaybackError("HLS stream verification failed")
            for field in ("codec_name", "profile", "pix_fmt", "width", "height", "sample_rate", "channels", "channel_layout", "color_transfer", "color_primaries", "color_space"):
                # The HLS demuxer may omit AAC profile although identical ASC and
                # packets are present. A reported different profile is rejected.
                if field == "profile" and candidates[0].get(field) is None:
                    continue
                if original.get(field) is not None and candidates[0].get(field) != original[field]:
                    raise PlaybackError(f"HLS stream property changed unexpectedly: {field}")
            if original.get("codec_name") == "flac":
                for field in ("bits_per_raw_sample", "sample_fmt"):
                    if original.get(field) is None or candidates[0].get(field) != original[field]:
                        raise PlaybackError(f"FLAC stream property could not be preserved: {field}")
        if dovi:
            init_probe = probe_media(folder / init_name, check_active=guard)
            init_video = next((s for s in init_probe["streams"] if s.get("codec_type") == "video"), {})
            playlist_video = next((s for s in streams if s.get("codec_type") == "video"), {})
            if dovi_configuration(init_video) != dovi or dovi_configuration(playlist_video) != dovi:
                raise PlaybackError("Dolby Vision configuration was not preserved in HLS initialization")
        if audios and audios[0].get("codec_name") == "flac":
            streaminfo = flac_streaminfo_digest(source, check_active=guard)
            if flac_streaminfo_digest(folder / init_name, check_active=guard) != streaminfo:
                raise PlaybackError("FLAC STREAMINFO configuration was not preserved")
            audio_evidence["flac"] = {"streaminfo_sha256": streaminfo, "streaminfo_verified": True,
                "bits_per_sample": int(audios[0]["bits_per_raw_sample"]), "sample_rate": int(audios[0]["sample_rate"]),
                "channels": audios[0]["channels"], "payload_verified": True}
        # Ordinary AAC/AC-3 needs the same preservation check as FLAC/EC-3.
        # Hash audio and video together: one read of each input, even for DOVI.
        payload_hashes = stream_copy_digests(source, check_active=guard, include_audio=bool(audios))
        if stream_copy_digests(folder / "index.m3u8", check_active=guard, include_audio=bool(audios)) != payload_hashes:
            raise PlaybackError("HLS stream-copy payload verification failed")
        duration = sum(segment["duration"] for segment in segments)
        original_duration = float(probe.get("format", {}).get("duration") or source_variant.duration)
        if abs(duration - original_duration) > 2:
            raise PlaybackError("HLS package duration does not match the source")
        guard()
        provider(source_variant.asset_id)  # Recheck the protected source before publication.
        initialization = ingest_file(db, folder / init_name, kind="hls_init", mime_type="video/mp4", profile_id=profile_id, workspace=workspace, check_active=guard)
        rows = []
        for sequence, segment in enumerate(segments):
            guard()
            asset = ingest_file(db, folder / segment["filename"], kind="hls_segment", mime_type="video/iso.segment", profile_id=profile_id, workspace=workspace, check_active=guard)
            rows.append({"sequence": sequence, "asset_id": asset.id, "duration": segment["duration"]})
        index = {"schema": "treasure.hls.v1", "source_asset_id": source_variant.asset_id, "stream_copy": True,
            "init_asset_id": initialization.id, "segments": rows, "target_duration": math.ceil(max(s["duration"] for s in rows)),
            "duration": duration, "video_codec": videos[0]["codec_name"], "audio_codec": audios[0]["codec_name"] if audios else "",
            "segment_target_seconds": segment_seconds, "segment_boundary": "source_keyframes",
            "fragment_target_seconds": 1, "packaging_version": 3}
        index_path = folder / "index.json"
        index_path.write_text(json.dumps(index, ensure_ascii=True, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        index_asset = ingest_file(db, index_path, kind="hls_index", mime_type="application/json", profile_id=profile_id, workspace=workspace, check_active=guard)
        for asset_id, purpose in [(source_variant.asset_id, "hls_source"), (initialization.id, "hls_init"), *[(row["asset_id"], "hls_segment") for row in rows]]:
            if not db.scalar(select(AssetRef).where(AssetRef.asset_id == asset_id, AssetRef.entity_type == "asset", AssetRef.entity_id == index_asset.id, AssetRef.purpose == purpose)):
                db.add(AssetRef(asset_id=asset_id, entity_type="asset", entity_id=index_asset.id, purpose=purpose))
        from app.ingest.media import _measured_properties
        from app.media_properties import merge_media_properties
        # Reuse the already completed source probe. HLS is stream-copy, so these
        # measurements describe its actual source, never a sibling compatibility file.
        measurements = _measured_properties(videos[0], audios[0] if audios else {}, original_duration, source.stat().st_size)
        result = MediaVariant(part_id=source_variant.part_id, asset_id=index_asset.id, kind="hls", format_key=format_key,
            quality=source_variant.quality, width=source_variant.width, height=source_variant.height,
            video_codec=source_variant.video_codec, audio_codec=source_variant.audio_codec, duration=duration,
            metadata_json=merge_media_properties(source_variant.metadata_json, measurements,
                {**audio_evidence, "source_variant_id": source_variant.id,
                "source_asset_id": source_variant.asset_id, "stream_copy": True,
                "source_kind": source_variant.kind, "segment_target_seconds": segment_seconds,
                "fragment_target_seconds": 1, "packaging_version": 3,
                "dovi": dovi, "dolby_vision": bool(dovi.get("dv_profile", 0) > 0 and dovi.get("rpu_present_flag") == 1),
                "payload_hashes": payload_hashes,
                "payloads_verified": bool(payload_hashes), "spatial_audio_output_verified": False}))
        db.add(result)
        guard()
        db.flush()
        return result
