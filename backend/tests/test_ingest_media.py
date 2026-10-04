from pathlib import Path
import shutil
import subprocess

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest import media
from app.ingest.errors import IngestError
from app.models import Asset, Base, MediaVariant, Video, VideoPart
from app.storage.service import ingest_file, read_asset_bytes


SDR = {"codec_name": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080,
       "color_transfer": "bt709", "color_primaries": "bt709"}
AUDIO = {"codec_name": "aac", "profile": "LC", "channels": 2}


@pytest.fixture
def original(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = tmp_path / "archive.mp4"
        source.write_bytes(b"original-media")
        asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
        video = Video(bvid="BV1234567890", title="test")
        db.add(video); db.flush()
        part = VideoPart(video_id=video.id, cid="1", position=1, duration=10)
        db.add(part); db.flush()
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="original", quality="1080p")
        db.add(variant); db.commit()
        yield db, part, variant
    engine.dispose()


def test_compatible_source_aliases_asset_without_transcoding(original, monkeypatch):
    db, part, archive = original
    monkeypatch.setattr(media, "_probe", lambda path: (SDR, AUDIO, 10))
    monkeypatch.setattr(media, "_run_ffmpeg", lambda *args: pytest.fail("already compatible"))
    playback, reused = media.ensure_playback_variant(db, part, archive, {})
    db.commit()
    assert reused and playback.kind == "playback" and playback.asset_id == archive.asset_id
    monkeypatch.setattr(media, "_probe", lambda path: pytest.fail("already verified playback"))
    again, reused = media.ensure_playback_variant(db, part, archive, {})
    assert again.id == playback.id and reused
    assert db.scalar(select(func.count()).select_from(Asset)) == 1


def test_hevc_conversion_preserves_original_and_reuses_verified_copy(original, monkeypatch):
    db, part, archive = original
    invocations = []
    source = {**SDR, "codec_name": "hevc", "pix_fmt": "yuv420p10le"}
    monkeypatch.setattr(media, "_probe", lambda path: (SDR if path.name == "playback.mp4" else source, AUDIO, 10))
    def convert(args, output, maximum, timeout, guard):
        guard()
        invocations.append((args, maximum, timeout))
        output.write_bytes(b"h264-aac-media")
    monkeypatch.setattr(media, "_run_ffmpeg", convert)
    playback, reused = media.ensure_playback_variant(db, part, archive, {"max_download_bytes": 100, "transcode_timeout_seconds": 60})
    db.commit()
    assert not reused and playback.asset_id != archive.asset_id
    assert playback.video_codec == "h264" and playback.audio_codec == "aac"
    assert read_asset_bytes(db, archive.asset_id) == b"original-media"
    assert read_asset_bytes(db, playback.asset_id) == b"h264-aac-media"
    assert invocations[0][1:] == (100, 60)
    assert "libx264" in invocations[0][0] and "aac_low" in invocations[0][0]
    again, reused = media.ensure_playback_variant(db, part, archive, {})
    assert again.id == playback.id and reused and len(invocations) == 1
    metadata = db.get(Video, part.video_id).metadata_json["media_properties"][archive.id]
    assert metadata["codec_name"] == "hevc" and metadata["pix_fmt"] == "yuv420p10le"
    assert metadata["compatibility"] == "ready"


@pytest.mark.parametrize("properties", [
    {"color_transfer": "smpte2084"}, {"color_transfer": "arib-std-b67"},
    {"color_primaries": "bt2020"}, {"side_data_list": [{"side_data_type": "DOVI configuration record"}]},
])
def test_hdr_is_explicitly_unsupported_and_never_relabelled_sdr(original, monkeypatch, properties):
    db, part, archive = original
    monkeypatch.setattr(media, "_probe", lambda path: ({**SDR, **properties}, AUDIO, 10))
    monkeypatch.setattr(media, "_run_ffmpeg", lambda *args: pytest.fail("HDR needs explicit tone mapping"))
    with pytest.raises(IngestError) as error:
        media.ensure_playback_variant(db, part, archive, {})
    assert error.value.code == "hdr_conversion_unsupported" and not error.value.retryable
    assert db.scalar(select(func.count()).select_from(MediaVariant)) == 1
    assert read_asset_bytes(db, archive.asset_id) == b"original-media"
    metadata = db.get(Video, part.video_id).metadata_json["media_properties"][archive.id]
    assert metadata["compatibility"] == "hdr_conversion_unsupported"
    assert metadata["hdr"] or metadata["wide_gamut"]


def test_invalid_converted_media_is_not_published(original, monkeypatch):
    db, part, archive = original
    monkeypatch.setattr(media, "_probe", lambda path: ({**SDR, "codec_name": "av1"}, AUDIO, 10))
    monkeypatch.setattr(media, "_run_ffmpeg", lambda args, output, *rest: output.write_bytes(b"invalid-output"))
    with pytest.raises(IngestError) as error:
        media.ensure_playback_variant(db, part, archive, {})
    assert error.value.code == "invalid_playback"
    assert db.scalar(select(func.count()).select_from(Asset)) == 1
    assert not list(settings.scratch_dir.glob("treasure-playback-*"))


class RunningProcess:
    def __init__(self): self.stopped = False
    def poll(self): return -15 if self.stopped else None
    def terminate(self): self.stopped = True
    def wait(self, timeout): return -15
    def kill(self): self.stopped = True


@pytest.mark.parametrize("failure", ["cancel", "timeout", "size"])
def test_ffmpeg_is_terminated_on_guard_timeout_or_size_limit(tmp_path, monkeypatch, failure):
    process = RunningProcess()
    monkeypatch.setattr(media.subprocess, "Popen", lambda *args, **kwargs: process)
    output = tmp_path / "output.mp4"
    calls = 0
    def guard():
        nonlocal calls
        calls += 1
        if failure == "cancel" and calls > 1:
            raise IngestError("任务已停止", code="job_stopped", retryable=False)
    if failure == "timeout":
        clock = iter([0, 11])
        monkeypatch.setattr(media.time, "monotonic", lambda: next(clock))
    if failure == "size":
        output.write_bytes(b"too-big")
    with pytest.raises(IngestError) as error:
        media._run_ffmpeg(["ffmpeg"], output, 1, 10, guard)
    assert error.value.code == {"cancel": "job_stopped", "timeout": "conversion_timeout", "size": "media_budget"}[failure]
    assert process.stopped


@pytest.mark.parametrize("cancelled", [False, True])
def test_full_decode_verification_releases_process_on_cancel_or_lease_loss(tmp_path, monkeypatch, cancelled):
    from app.jobs import LeaseLost
    process = RunningProcess()
    commands = []
    def start(arguments, **options):
        commands.append(arguments)
        assert options["stdin"] is subprocess.DEVNULL
        assert options["stderr"] is subprocess.DEVNULL
        return process
    monkeypatch.setattr(media.subprocess, "Popen", start)
    calls = [0]
    def guard():
        calls[0] += 1
        if calls[0] > 1:
            if cancelled:
                raise IngestError("任务已停止", code="job_stopped", retryable=False)
            raise LeaseLost("expired")
    with pytest.raises(IngestError if cancelled else LeaseLost):
        media._verify_decode(tmp_path / "long-video.mp4", 7200, guard)
    assert process.stopped
    assert "-xerror" in commands[0] and commands[0][-3:] == ["-f", "null", "-"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="real media tools unavailable")
def test_real_ffmpeg_converts_synthetic_hevc_to_h264_aac(original, tmp_path):
    db, part, archive = original
    source = tmp_path / "synthetic-hevc.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx265",
        "-x265-params", "pools=1:frame-threads=1:log-level=error", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source)], capture_output=True, check=True, timeout=30)
    assert media._probe(source)[0]["codec_name"] == "hevc"
    asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
    archive.asset_id = asset.id
    db.flush()
    playback, reused = media.ensure_playback_variant(db, part, archive, {"max_download_bytes": 10 * 1024**2, "transcode_timeout_seconds": 30})
    db.commit()
    assert not reused and playback.asset_id != archive.asset_id
    assert playback.video_codec == "h264" and playback.audio_codec == "aac"
    assert abs(playback.duration - 1) < 0.2
    result = tmp_path / "verified-playback.mp4"
    result.write_bytes(read_asset_bytes(db, playback.asset_id))
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(result), "-f", "null", "-"], capture_output=True, check=True, timeout=30)
    assert read_asset_bytes(db, archive.asset_id) == source.read_bytes()
