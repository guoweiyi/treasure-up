"""Read-only, offline audio diagnostics. Requires FFmpeg, not the application or DB."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time


LOG_LIMIT = 1024 * 1024  # Per subprocess pipe; raw logs are never published.
MAX_AUDIO_TRACKS = 64
LIMITATIONS = [
    "Successful decoding does not rule out audible clicks, noise, or playback-device faults.",
    "Peak is the decoded floating-point sample peak, not oversampled true peak or a clipping diagnosis.",
    "Timing includes container declarations, decoded sample counts, and FFmpeg warnings; it is not a packet continuity audit.",
    "For HLS, an optional SHA-256 covers only the supplied playlist, not its referenced files.",
    "Diagnostics are allowlisted categories; raw FFmpeg messages, metadata, paths, and URLs are withheld.",
]
DIAGNOSTICS = {
    "protocol_denied": r"Protocol .* not on whitelist|Protocol not found",
    "non_monotonic_timestamp": r"non[- ]monoton(?:ic|ically)|DTS .* out of order",
    "invalid_timestamp": r"invalid.*timestamp|timestamp.*invalid|timestamps are unset",
    "corrupt_packet": r"corrupt.*packet|packet.*corrupt|invalid.*data|invalid.*frame|invalid.*header",
    "decode_error": r"error (?:while )?decod|error submitting packet|error sending packet|decode.*fail",
    "input_read_error": r"error opening|failed to open|no such file|input/output error|end of file",
}
ERROR_LEVEL = re.compile(r"^(?:\[[^\]\r\n]+\]\s+)?\[(error|fatal|panic)\]", re.MULTILINE)


def run_ffmpeg(arguments, *, timeout, limit=LOG_LIMIT):
    """Bound memory and run time even when malformed media emits unbounded stderr."""
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    try:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return {"returncode": None, "stop_reason": "tool_unavailable", "stdout": "", "stderr": ""}

    def drain(name, stream):
        with stream:
            while block := stream.read(8192):
                available = limit - len(chunks[name])
                chunks[name].extend(block[:max(0, available)])
                if len(block) > available:
                    overflow.set()

    readers = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
               for name in chunks]
    for reader in readers:
        reader.start()
    started, reason = time.monotonic(), None
    try:
        while process.poll() is None:
            if overflow.is_set():
                reason = "diagnostic_output_limit"
                break
            if time.monotonic() - started > timeout:
                reason = "timeout"
                break
            time.sleep(0.02)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for reader in readers:
            reader.join()
    if overflow.is_set():
        reason = "diagnostic_output_limit"
    return {"returncode": process.returncode, "stop_reason": reason,
            **{name: bytes(value).decode("utf-8", errors="replace") for name, value in chunks.items()}}


def diagnostics(result):
    counts = Counter()
    for line in result["stderr"].splitlines():
        for code, pattern in DIAGNOSTICS.items():
            if re.search(pattern, line, re.IGNORECASE):
                counts[code] += 1
                break
    if result["returncode"] not in (0, None) and not counts:
        counts["unclassified_ffmpeg_failure"] = 1
    return {"events": [{"code": code, "count": count} for code, count in sorted(counts.items())],
            "error_level_messages": len(ERROR_LEVEL.findall(result["stderr"])),
            "log_limit_bytes_per_pipe": LOG_LIMIT,
            "truncated": result["stop_reason"] == "diagnostic_output_limit",
            "stop_reason": result["stop_reason"], "returncode": result["returncode"]}


def parse_input(log):
    # Parse only FFmpeg's input section, never output stream descriptions.
    section = re.split(r"Stream mapping:|Output #", log, maxsplit=1)[0]
    declared = re.search(r"Duration: (\d+):(\d+):([\d.]+)", section)
    start = re.search(r"Duration: [^\r\n]+?start: (-?[\d.]+)", section)
    tracks = {}
    for match in re.finditer(r"Stream #0:(\d+)(?:\[[^\]]*\])?(?:\([^)]*\))?: Audio: ([a-zA-Z0-9_]+)([^\r\n]*)", section):
        index, codec, detail = match.groups()
        rate = re.search(r"\b(\d+) Hz\b", detail)
        layout = re.search(r", (mono|stereo|\d+\.\d+(?:\([A-Za-z]+\))?|\d+ channels)(?:,|$)", detail)
        tracks[int(index)] = {"stream_index": int(index), "codec": codec,
                              "sample_rate_hz": int(rate[1]) if rate else None,
                              "channel_layout": layout[1] if layout else None}
    return {"declared_duration_seconds": (int(declared[1]) * 3600 + int(declared[2]) * 60 + float(declared[3])) if declared else None,
            "declared_start_seconds": float(start[1]) if start else None,
            "audio_tracks": list(tracks.values())}


def measurements(result, sample_rate):
    def number(label):
        found = re.findall(r"\[Parsed_astats_\d+[^\]]*\] (?:\[info\] )?" + re.escape(label) + r": ([^\s]+)", result["stderr"])
        if not found:
            return None
        try:
            return float(found[-1])
        except ValueError:
            return None

    peak = number("Peak level dB")
    samples = number("Number of samples")
    nans, infs = number("Number of NaNs"), number("Number of Infs")
    finite_peak = peak is not None and math.isfinite(peak)
    return {"sample_peak_dbfs": peak if finite_peak else None,
            "sample_peak_linear": 10 ** (peak / 20) if finite_peak else (0.0 if peak == -math.inf else None),
            "sample_peak_above_full_scale": peak > 0 if finite_peak else None,
            "silent": peak == -math.inf,
            "decoded_samples_per_channel": int(samples) if samples is not None and math.isfinite(samples) else None,
            "decoded_sample_duration_seconds": samples / sample_rate if sample_rate and samples is not None and math.isfinite(samples) else None,
            "nan_samples": nans if nans is not None and math.isfinite(nans) else None,
            "infinite_samples": infs if infs is not None and math.isfinite(infs) else None}


def diagnose(source, *, ffmpeg="ffmpeg", timeout=7200, sha256=False):
    """Never return source names or raw subprocess output, including on failure."""
    try:
        path = Path(source).resolve(strict=True)
        if not path.is_file():
            raise OSError()
        size = path.stat().st_size
        with path.open("rb") as stream:
            hls = stream.read(16).lstrip(b"\xef\xbb\xbf\r\n ").startswith(b"#EXTM3U")
            digest = None
            if sha256:
                stream.seek(0)
                value = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    value.update(block)
                digest = value.hexdigest()
    except (OSError, ValueError):
        return {"status": "input_unavailable", "audio_tracks": []}

    base = [str(ffmpeg), "-nostdin", "-hide_banner", "-nostats", "-v", "level+info"]
    input_args = ["-protocol_whitelist", "file,crypto,data", "-i", str(path)]
    probe = run_ffmpeg([*base, *input_args, "-map", "0:a?", "-c:a", "copy", "-t", "0", "-f", "null", "-"],
                       timeout=min(timeout, 120))
    metadata = parse_input(probe["stderr"])
    tracks = metadata.pop("audio_tracks")
    report = {"status": "complete", "input_kind": "hls_playlist" if hls else "media_file", "input_bytes": size,
              "sha256": digest, "sha256_scope": "input_file_only" if sha256 else None,
              **metadata, "probe": diagnostics(probe), "audio_tracks": []}
    if probe["stop_reason"] or not tracks or len(tracks) > MAX_AUDIO_TRACKS:
        report["status"] = (probe["stop_reason"] or ("audio_track_limit" if len(tracks) > MAX_AUDIO_TRACKS else
                              "no_audio" if re.search(r"Input #0,", probe["stderr"]) else "probe_failed"))
        return report
    if probe["returncode"] or report["probe"]["error_level_messages"]:
        report["status"] = "probe_failed"
        return report
    for track in tracks:
        result = run_ffmpeg([*base, "-xerror", "-err_detect", "explode", *input_args,
                            "-map", f"0:{track['stream_index']}", "-vn", "-sn", "-dn",
                            "-af", "aformat=sample_fmts=dbl,astats=measure_perchannel=none:measure_overall=Peak_level+Number_of_samples+Number_of_NaNs+Number_of_Infs",
                            "-c:a", "pcm_f64le", "-f", "null", "-"],
                            timeout=timeout)
        stats = measurements(result, track["sample_rate_hz"])
        diagnostic = diagnostics(result)
        # FFmpeg versions can emit decoder errors and still exit zero, even with
        # -xerror. Reject every error-level log, including uncategorized errors.
        passed = (result["returncode"] == 0 and not result["stop_reason"]
                  and not diagnostic["error_level_messages"] and bool(stats["decoded_samples_per_channel"])
                  and stats["nan_samples"] == 0 and stats["infinite_samples"] == 0
                  and (stats["sample_peak_dbfs"] is not None or stats["silent"]))
        report["audio_tracks"].append({**track, "strict_decode": "passed" if passed else "failed",
                                      "measurement_scope": "full_track" if passed else "partial_or_unavailable",
                                      **stats, "diagnostics": diagnostic})
        if not passed:
            report["status"] = "decode_failed"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline, read-only audio diagnostics; JSON on stdout, no database access.")
    parser.add_argument("inputs", nargs="+", help="Local media files or local HLS playlists; report order matches input order")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable (FFprobe is not required)")
    parser.add_argument("--timeout", type=float, default=7200, help="Maximum seconds per track (default: 7200)")
    parser.add_argument("--sha256", action="store_true", help="Hash the supplied file (for HLS, only the playlist)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    reports = []
    for index, source in enumerate(args.inputs, 1):
        reports.append({"input_id": f"input_{index}", **diagnose(source, ffmpeg=args.ffmpeg,
                        timeout=args.timeout, sha256=args.sha256)})
    print(json.dumps({"schema": "treasure.audio-diagnostics.v1", "created_at": datetime.now(timezone.utc).isoformat(),
                      "read_only": True, "offline": True, "limitations": LIMITATIONS, "inputs": reports},
                     ensure_ascii=False, allow_nan=False, indent=2))
    return 0 if all(item["status"] == "complete" for item in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
