"""Audio corruption, downmix headroom and publication boundaries."""
from array import array
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yt_dlp
from sqlalchemy import func, select

from app.config import settings
from app.ingest import media
from app.ingest.errors import IngestError
from app.models import Asset, MediaVariant, Video, VideoPart
from app.storage.service import ingest_file, read_asset_bytes
from test_ingest_media import original, RunningProcess  # noqa: F401
from test_ingest_runner import db  # noqa: F401


def test_zero_exit_with_decoder_error_is_rejected_without_exposing_diagnostics():
    with pytest.raises(IngestError) as error:
        media._run_ffmpeg([sys.executable, "-c", "import sys; sys.stderr.write('private-path: damaged AAC frame')"],
                          None, 0, 30, lambda: None)
    assert error.value.code == "conversion_failed"
    assert "private-path" not in str(error.value)


@pytest.mark.parametrize("already_exited", [False, True])
def test_diagnostic_overflow_is_checked_during_run_and_after_exit(monkeypatch, already_exited):
    process = RunningProcess()
    if already_exited:
        process.poll = lambda: 0
    def start(*args, **kwargs):
        os.write(kwargs["stderr"].fileno(), b"e" * 33)
        return process
    monkeypatch.setattr(media, "MAX_TOOL_OUTPUT", 32)
    monkeypatch.setattr(media.subprocess, "Popen", start)
    with pytest.raises(IngestError) as error:
        media._run_ffmpeg(["ffmpeg"], None, 0, 30, lambda: None)
    assert error.value.code == "conversion_failed"
    assert already_exited or process.stopped


def test_audio_verification_decodes_every_audio_track_to_eof_without_video(monkeypatch):
    calls = []
    monkeypatch.setattr(media, "_run_ffmpeg", lambda *args: calls.append(args))
    media._verify_audio_decode(Path("archive.mp4"), 7200, lambda: None)
    command, output, _, timeout, _ = calls[0]
    assert command[command.index("-map") + 1] == "0:a"
    assert "-vn" in command and "-t" not in command
    assert command[-3:] == ["-f", "null", "-"] and output is None and timeout == 7200


@pytest.mark.parametrize("has_audio", [False, True])
def test_new_archive_never_publishes_failed_audio_and_does_not_invent_silent_audio_evidence(db, monkeypatch, has_audio):
    class Downloader:
        def __init__(self, options):
            self.params = {**options, "outtmpl": {"default": options["outtmpl"]}}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def build_format_selector(self, expression): return lambda context: []
        def extract_info(self, *args, **kwargs):
            return {"id": "BV1234567890", "format_id": "80", "ext": "mp4", "vcodec": "avc1",
                    "acodec": "mp4a" if has_audio else "none"}
        def process_info(self, info):
            Path(self.params["outtmpl"]["default"].replace("%(ext)s", "mp4")).write_bytes(b"fixture")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    monkeypatch.setattr(media, "_probe", lambda _: ({"codec_name": "h264"}, {"codec_name": "aac"} if has_audio else {}, 10))
    def reject(*args):
        assert has_audio, "silent video must not attempt an audio decode"
        raise IngestError("audio corrupt", code="audio_decode_failed")
    monkeypatch.setattr(media, "_verify_audio_decode", reject)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="11", position=1, duration=10)
    db.add(part); db.flush()
    client = SimpleNamespace(cookies=[], view=lambda _: {"pages": [{"cid": "11", "page": 1}]})
    if has_audio:
        with pytest.raises(IngestError, match="audio corrupt"):
            media.archive_media(db, client, video, part, {})
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(func.count()).select_from(MediaVariant)) == 0
        assert list(settings.scratch_dir.glob("downloads/*/*/media.mp4")), "retain unpublished source for diagnosis"
    else:
        variant, _ = media.archive_media(db, client, video, part, {})
        assert variant.metadata_json["audio_decode_verified"] is False


@pytest.mark.parametrize("audio_only", [False, True])
def test_legacy_playback_copies_are_preserved_but_never_reused(original, monkeypatch, audio_only):
    from test_audio_compatibility import mock_conversion
    from test_ingest_media import SDR, AUDIO
    db, part, archive = original
    legacy = MediaVariant(part_id=part.id, asset_id=archive.asset_id, kind="playback",
        format_key=("video-copy-aac-v1:" if audio_only else "h264-aac-sdr-v1:") + archive.asset_id)
    db.add(legacy); db.flush()
    if audio_only:
        archive.audio_codec = "eac3"
        mock_conversion(monkeypatch)
    else:
        monkeypatch.setattr(media, "_probe", lambda path: (SDR if path.name == "playback.mp4" else {**SDR, "codec_name": "hevc"}, AUDIO, 10))
        monkeypatch.setattr(media, "_run_ffmpeg", lambda args, output, *rest: output.write_bytes(b"new-safe-copy"))
        monkeypatch.setattr(media, "_verify_audio_decode", lambda *a: None)
    copied, reused = media.ensure_playback_variant(db, part, archive, {})
    assert not reused and copied.id != legacy.id and "-v2:" in copied.format_key
    assert copied.metadata_json["source_variant_id"] == archive.id
    assert copied.metadata_json["audio_mix_revision"] == 2
    assert db.get(MediaVariant, legacy.id).asset_id == archive.asset_id
    assert read_asset_bytes(db, archive.asset_id) == b"original-media"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="real ffmpeg required")
def test_real_corrupt_aac_frame_is_rejected_even_if_decoder_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ffmpeg_path", Path(shutil.which("ffmpeg")))
    clean = tmp_path / "clean.aac"
    subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-f", "lavfi",
        "-i", "sine=frequency=1000:sample_rate=48000", "-t", "3", "-c:a", "aac",
        "-ac", "2", "-b:a", "192k", "-f", "adts", str(clean)], check=True, capture_output=True, timeout=30)
    raw = bytearray(clean.read_bytes())
    cursor, frames = 0, []
    while cursor < len(raw):
        size = ((raw[cursor + 3] & 3) << 11) | (raw[cursor + 4] << 3) | (raw[cursor + 5] >> 5)
        assert size > 7
        frames.append((cursor, size)); cursor += size
    start, size = frames[80]
    raw[start + 7:start + size] = b"\xff" * (size - 7)
    corrupt = tmp_path / "corrupt.aac"
    corrupt.write_bytes(raw)
    media._verify_audio_decode(clean, 30, lambda: None)
    with pytest.raises(IngestError) as error:
        media._verify_audio_decode(corrupt, 30, lambda: None)
    assert error.value.code == "audio_decode_failed"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="real media tools required")
@pytest.mark.parametrize("video_codec", ["libx264", "mpeg4"])
def test_both_aac_paths_keep_correlated_surround_audio_below_full_scale(original, tmp_path, video_codec):
    db, part, archive = original
    source = tmp_path / "surround.mp4"
    wave = "0.7*sin(2*PI*1000*t)"
    subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=25",
        "-f", "lavfi", "-i", f"aevalsrc={wave}|{wave}|{wave}|0|{wave}|{wave}:s=48000:c=5.1",
        "-t", "3", "-c:v", video_codec, "-c:a", "eac3", "-b:a", "640k", str(source)],
        capture_output=True, check=True, timeout=30)
    original_bytes = source.read_bytes()
    asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
    archive.asset_id, archive.audio_codec = asset.id, "eac3"
    copied, reused = media.ensure_playback_variant(db, part, archive, {"transcode_timeout_seconds": 60})
    output = tmp_path / "copy.mp4"
    output.write_bytes(read_asset_bytes(db, copied.asset_id))
    decoded = subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-i", str(output),
        "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "f32le", "-"], capture_output=True, check=True, timeout=30)
    samples = array("f", decoded.stdout)
    if sys.byteorder != "little": samples.byteswap()
    assert samples and all(math.isfinite(sample) for sample in samples)
    assert max(abs(sample) for sample in samples) < 1.0
    assert not reused and copied.metadata_json["audio_decode_verified"] is True
    assert copied.metadata_json["audio_mix_revision"] == 2
    assert copied.format_key.startswith("video-copy-aac-v2:" if video_codec == "libx264" else "h264-aac-sdr-v2:")
    assert source.read_bytes() == original_bytes
