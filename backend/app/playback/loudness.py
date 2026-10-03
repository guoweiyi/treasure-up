from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path

from app.config import settings
from app.models import MediaVariant
from app.storage.service import materialize_asset
from .tools import PlaybackError, probe_media, run_tool


def loudness_result(input_lufs, true_peak_dbfs, *, target_lufs=-18.0):
    base = {"status": "ready", "gain_db": 0.0, "gain_linear": 1.0, "target_lufs": target_lufs,
            "input_lufs": None, "true_peak_dbfs": None, "atmos_bypass": False,
            "reason": None, "analysis_only": True}
    try:
        loudness, peak = float(input_lufs), float(true_peak_dbfs)
    except (ValueError, TypeError):
        raise PlaybackError("Invalid loudness analysis") from None
    if loudness == -math.inf and peak == -math.inf:
        return {**base, "status": "silent", "reason": "silent_audio"}
    if not math.isfinite(loudness) or not math.isfinite(peak) or not math.isfinite(target_lufs):
        raise PlaybackError("Invalid loudness analysis")
    # Attenuate only. The peak ceiling is conservative; quiet originals are never boosted.
    gain = min(0.0, target_lufs - loudness, -1.0 - peak)
    return {**base, "input_lufs": loudness, "true_peak_dbfs": peak,
            "gain_db": round(gain, 4), "gain_linear": 10 ** (gain / 20)}


def bypass_reason(audio):
    if not audio:
        return "no_audio", False
    details = json.dumps({key: audio.get(key) for key in ("codec_name", "profile", "side_data_list", "tags")}).lower()
    # FFprobe does not reliably report every Atmos variant. Conservatively bypass
    # both potential carriers, without claiming that every E-AC-3/TrueHD is Atmos.
    potential_spatial = audio.get("codec_name") in {"eac3", "truehd"} or any(marker in details for marker in ("atmos", "joc", "dolby truehd"))
    if potential_spatial:
        return "spatial_audio_or_potential_atmos_carrier", True
    if int(audio.get("channels") or 0) > 2:
        return "multichannel_audio", False
    return None, False


def analyze_variant(db, variant_id, *, check_active=None):
    """Decode to a null sink for measurements; no normalized audio is ever stored."""
    variant = db.get(MediaVariant, variant_id)
    if not variant:
        raise PlaybackError("Media variant does not exist")
    source_asset_id = (variant.metadata_json or {}).get("source_asset_id") if variant.kind == "hls" else variant.asset_id
    if not source_asset_id:
        raise PlaybackError("Source media reference is missing")
    previous = (variant.metadata_json or {}).get("loudness")
    if previous and previous.get("source_asset_id") == source_asset_id and previous.get("analysis_version") == 1:
        return previous
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="loudness-", dir=settings.scratch_dir) as directory:
        source = materialize_asset(db, source_asset_id, Path(directory) / "source.media")
        probe = probe_media(source, check_active=check_active)
        audios = [s for s in probe["streams"] if s.get("codec_type") == "audio"]
        audio = audios[0] if audios else None
        reason, atmos_bypass = bypass_reason(audio)
        if len(audios) > 1:
            reason = "multiple_audio_tracks"
        if reason:
            result = {"status": "bypassed", "gain_db": 0.0, "gain_linear": 1.0, "target_lufs": -18.0,
                "input_lufs": None, "true_peak_dbfs": None, "atmos_bypass": atmos_bypass,
                "reason": reason, "analysis_only": True}
        else:
            _, errors = run_tool([str(settings.ffmpeg_path), "-nostdin", "-hide_banner", "-v", "info",
                "-protocol_whitelist", "file,crypto,data", "-i", str(source), "-map", f'0:{audio["index"]}',
                "-vn", "-sn", "-dn", "-af", "loudnorm=I=-18:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
                check_active=check_active)
            matches = re.findall(rb'\{[^{}]*"input_i"[^{}]*\}', errors)
            try:
                measurement = json.loads(matches[-1])
                result = loudness_result(measurement["input_i"], measurement["input_tp"])
            except (IndexError, ValueError, KeyError):
                raise PlaybackError("Loudness measurements were not produced") from None
    result.update(source_asset_id=source_asset_id, analysis_version=1)
    if check_active:
        check_active()
    variant.metadata_json = {**(variant.metadata_json or {}), "loudness": result}
    db.flush()
    return result
