"""The offline reporter exercises real decoders without any application/database setup."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("audio_diagnose", Path(__file__).resolve().parents[1] / "scripts" / "audio_diagnose.py")
diagnose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnose)


@pytest.fixture
def ffmpeg():
    value = shutil.which("ffmpeg")
    if value:
        return value
    for name in ("ffmpeg.exe", "ffmpeg"):
        bundled = ROOT / "node_modules" / "ffmpeg-static" / name
        if bundled.is_file():
            return str(bundled)
    pytest.skip("FFmpeg integration tool unavailable")


def encode(ffmpeg, *arguments):
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-v", "error", *map(str, arguments)],
                   check=True, capture_output=True, timeout=30)


def test_two_tracks_float_peak_and_privacy(ffmpeg, tmp_path, capsys):
    source = tmp_path / "private-user-secret.mka"
    encode(ffmpeg, "-f", "lavfi", "-i", "aevalsrc=1.25*sin(2*PI*440*t):s=48000:d=0.25",
           "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=0.25",
           "-map", "0:a", "-map", "1:a", "-c:a", "pcm_f64le",
           "-metadata", "title=https://user:secret@example.invalid/private?token=do-not-leak", source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    assert diagnose.main([str(source), "--ffmpeg", ffmpeg, "--sha256"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)["inputs"][0]
    assert report["status"] == "complete"
    assert report["sha256"] == before == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(report["audio_tracks"]) == 2
    first, second = report["audio_tracks"]
    assert first["sample_peak_linear"] == pytest.approx(1.25, abs=0.0001)
    assert first["sample_peak_above_full_scale"]
    assert first["decoded_samples_per_channel"] == 12000
    assert first["decoded_sample_duration_seconds"] == pytest.approx(0.25)
    assert second["sample_peak_linear"] < 1
    for private in ("private-user", "do-not-leak", "example.invalid", "secret", str(tmp_path)):
        assert private not in output


def test_truncated_pcm_is_not_reported_clean(ffmpeg, tmp_path):
    source = tmp_path / "broken.wav"
    encode(ffmpeg, "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=0.25", "-c:a", "pcm_s16le", source)
    source.write_bytes(source.read_bytes()[:-1001])
    report = diagnose.diagnose(source, ffmpeg=ffmpeg)
    assert report["status"] in {"probe_failed", "decode_failed"}
    if report["audio_tracks"]:
        assert report["audio_tracks"][0]["strict_decode"] == "failed"
        assert report["audio_tracks"][0]["measurement_scope"] == "partial_or_unavailable"


def test_corrupt_aac_is_rejected_even_if_ffmpeg_exits_zero(ffmpeg, tmp_path):
    source = tmp_path / "corrupt.aac"
    encode(ffmpeg, "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=5", "-c:a", "aac", "-f", "adts", source)
    raw = bytearray(source.read_bytes())
    position = 0
    for _ in range(100):
        position += ((raw[position + 3] & 3) << 11) | (raw[position + 4] << 3) | (raw[position + 5] >> 5)
    size = ((raw[position + 3] & 3) << 11) | (raw[position + 4] << 3) | (raw[position + 5] >> 5)
    raw[position + 7:position + size] = b"\xff" * (size - 7)
    source.write_bytes(raw)
    report = diagnose.diagnose(source, ffmpeg=ffmpeg)
    assert report["status"] in {"probe_failed", "decode_failed"}, report
    for track in report["audio_tracks"]:
        assert track["strict_decode"] == "failed"
        assert track["diagnostics"]["error_level_messages"] > 0


@pytest.mark.parametrize("error,nans,infs", [("[aac @ 1234] [error] Unknown future decoder failure.\n", 0, 0),
                                              ("", 1, 0), ("", 0, 1)])
def test_zero_exit_and_samples_cannot_hide_errors_or_nonfinite_values(tmp_path, monkeypatch, error, nans, infs):
    source = tmp_path / "source.m4a"
    source.write_bytes(b"fixture")
    probe = "[info] Input #0, mov, from 'private':\n[info] Stream #0:0: Audio: aac, 48000 Hz, stereo, fltp\n"
    stats = ("[Parsed_astats_1 @ 1234] [info] Peak level dB: -1.0\n"
             "[Parsed_astats_1 @ 1234] [info] Number of samples: 48000\n"
             f"[Parsed_astats_1 @ 1234] [info] Number of NaNs: {nans}\n"
             f"[Parsed_astats_1 @ 1234] [info] Number of Infs: {infs}\n")
    results = iter([probe, error + stats])
    monkeypatch.setattr(diagnose, "run_ffmpeg", lambda *a, **k: {
        "returncode": 0, "stop_reason": None, "stdout": "", "stderr": next(results)})
    report = diagnose.diagnose(source)
    assert report["status"] == "decode_failed"
    assert report["audio_tracks"][0]["strict_decode"] == "failed"


def test_local_hls_and_remote_uri_rejection(ffmpeg, tmp_path):
    playlist = tmp_path / "local.m3u8"
    encode(ffmpeg, "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=0.7", "-c:a", "aac",
           "-f", "hls", "-hls_time", "0.2", "-hls_list_size", "0", playlist)
    report = diagnose.diagnose(playlist, ffmpeg=ffmpeg, sha256=True)
    assert report["status"] == "complete", report
    assert report["input_kind"] == "hls_playlist"
    assert report["sha256_scope"] == "input_file_only"
    assert report["audio_tracks"][0]["codec"] == "aac"
    playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttps://user:secret@example.invalid/clip.ts?token=private\n#EXT-X-ENDLIST\n")
    rejected = diagnose.diagnose(playlist, ffmpeg=ffmpeg)
    assert rejected["status"] != "complete"
    assert any(event["code"] == "protocol_denied" for event in rejected["probe"]["events"])
    serialized = json.dumps(rejected)
    assert "example.invalid" not in serialized and "secret" not in serialized


def test_runner_bounds_output_and_time():
    noisy = diagnose.run_ffmpeg([sys.executable, "-c", "import sys; sys.stderr.write('x' * 20000)"],
                               timeout=10, limit=512)
    assert noisy["stop_reason"] == "diagnostic_output_limit"
    assert len(noisy["stderr"]) <= 512
    waiting = diagnose.run_ffmpeg([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.03)
    assert waiting["stop_reason"] == "timeout"


def test_silent_track_uses_json_null_instead_of_infinity(ffmpeg, tmp_path, capsys):
    source = tmp_path / "silence.wav"
    encode(ffmpeg, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.1", source)
    assert diagnose.main([str(source), "--ffmpeg", ffmpeg]) == 0
    output = capsys.readouterr().out
    assert "Infinity" not in output and "NaN" not in output
    track = json.loads(output)["inputs"][0]["audio_tracks"][0]
    assert track["silent"] and track["sample_peak_linear"] == 0
    assert track["sample_peak_dbfs"] is None and track["strict_decode"] == "passed"


def test_missing_source_and_tool_do_not_expose_paths(tmp_path):
    report = diagnose.diagnose(tmp_path / "secret-user-name.mp4")
    assert report == {"status": "input_unavailable", "audio_tracks": []}
    media = tmp_path / "source.wav"
    media.write_bytes(b"placeholder")
    report = diagnose.diagnose(media, ffmpeg=str(tmp_path / "private-tool-location"))
    assert report["status"] == "tool_unavailable"
    assert "private-tool-location" not in json.dumps(report)
