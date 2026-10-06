"""AAC fallback preserves the picture, without advertising converted audio as Atmos."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.ingest import media
from app.ingest.errors import IngestError
from app.models import Asset, MediaVariant, Video
from app.playback import hls
from app.playback.ec3 import ec3_configuration
from app.storage.base import file_digest
from app.storage.service import ingest_file, read_asset_bytes
from test_ingest_media import original  # noqa: F401


@pytest.mark.parametrize("duration", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_duration_is_not_accepted(monkeypatch, duration):
    from types import SimpleNamespace
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=(
        '{"streams":[{"codec_type":"video"}],"format":{"duration":"' + duration + '"}}').encode()))
    with pytest.raises(IngestError):
        media._probe(Path("fixture.mp4"))


DOVI = {"side_data_type": "DOVI configuration record", "dv_profile": 8,
        "rpu_present_flag": 1, "el_present_flag": 0, "bl_present_flag": 1,
        "dv_bl_signal_compatibility_id": 1}
VIDEO = {"codec_type": "video", "codec_name": "hevc", "profile": "Main 10", "pix_fmt": "yuv420p10le",
         "width": 3840, "height": 2160, "color_transfer": "smpte2084", "color_primaries": "bt2020",
         "color_space": "bt2020nc", "color_range": "tv", "side_data_list": [DOVI]}
EC3 = {"codec_type": "audio", "codec_name": "eac3", "channels": 8, "channel_layout": "7.1"}
AAC = {"codec_type": "audio", "codec_name": "aac", "profile": "LC", "channels": 2, "channel_layout": "stereo"}


def mock_conversion(monkeypatch, *, fault=None):
    commands = []
    def probe(path):
        video, audio = dict(VIDEO), dict(AAC if path.name == "playback.mp4" else EC3)
        if path.name == "playback.mp4":
            if fault == "dovi": video["side_data_list"] = []
            if fault == "color": video["color_transfer"] = "bt709"
            if fault == "audio": audio["channels"] = 6
        return video, audio, 10
    def convert(args, output, *rest):
        commands.append(args)
        output.write_bytes(b"preserved-video-aac-audio")
    monkeypatch.setattr(media, "_probe", probe)
    monkeypatch.setattr(media, "probe_media", lambda *a, **k: {"streams": [VIDEO, EC3, *([EC3] if fault == "tracks" else [])]})
    monkeypatch.setattr(media, "_run_ffmpeg", convert)
    monkeypatch.setattr(media, "_verify_audio_decode", lambda *a: None)
    monkeypatch.setattr(media, "_packet_hash", lambda path, *a: "changed" if fault == "packets" and path.name == "playback.mp4" else "original-packets")
    return commands


def test_hdr_ec3_gets_audio_only_copy_before_sdr_rejection_and_reuses_it(original, monkeypatch):
    db, part, archive = original
    archive.audio_codec = "eac3"
    # A previous full SDR conversion must not hide the preserving rendition.
    db.add(MediaVariant(part_id=part.id, asset_id=archive.asset_id, kind="playback",
                        format_key="h264-aac-sdr-v1:" + archive.asset_id))
    db.flush()
    commands = mock_conversion(monkeypatch)
    fallback, reused = media.ensure_playback_variant(db, part, archive, {})
    assert not reused and fallback.asset_id != archive.asset_id
    assert fallback.format_key == "video-copy-aac-v2:" + archive.asset_id
    data = fallback.metadata_json
    assert data["source_variant_id"] == archive.id and data["compatibility_mode"] == "audio_only"
    assert data["video_stream_copy"] and data["audio_transcoded"] and data["video_payload_verified"]
    assert data["dolby_vision"] and data["hdr"] and data["dovi"]["rpu_present_flag"] == 1
    assert data["audio_channels"] == 2 and data["dolby_atmos"] is False and data["ec3"] == {}
    assert fallback.video_codec == "hevc" and fallback.audio_codec == "aac"
    assert read_asset_bytes(db, archive.asset_id) == b"original-media"
    properties = db.get(Video, part.video_id).metadata_json["media_properties"][fallback.id]
    assert properties["compatibility_mode"] == "audio_only" and properties["dolby_atmos"] is False
    assert commands[0][commands[0].index("-c:v") + 1] == "copy"
    assert "-vf" not in commands[0] and "hvc1" in commands[0]
    assert media.AAC_STEREO_FILTER in commands[0]
    assert data["audio_mix_revision"] == 2 and data["audio_decode_verified"] is True
    monkeypatch.setattr(media, "_probe", lambda *a: pytest.fail("verified rendition must reuse"))
    again, reused = media.ensure_playback_variant(db, part, archive, {})
    assert reused and again.id == fallback.id and len(commands) == 1


@pytest.mark.parametrize("fault", ["dovi", "color", "audio", "packets", "tracks"])
def test_changed_picture_or_invalid_audio_copy_is_never_published(original, monkeypatch, fault):
    db, part, archive = original
    mock_conversion(monkeypatch, fault=fault)
    with pytest.raises(IngestError):
        media.ensure_playback_variant(db, part, archive, {})
    assert db.scalar(select(func.count()).select_from(Asset)) == 1
    assert db.scalar(select(func.count()).select_from(MediaVariant)) == 1
    assert read_asset_bytes(db, archive.asset_id) == b"original-media"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_synthetic_ec3_audio_fallback_without_private_samples(original, tmp_path):
    db, part, archive = original
    source = tmp_path / "synthetic.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "eac3", "-ac", "6", str(source)], check=True, timeout=60)
    before = file_digest(source)
    asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
    archive.asset_id, archive.audio_codec = asset.id, "eac3"
    copied, reused = media.ensure_playback_variant(db, part, archive, {"transcode_timeout_seconds": 30})
    assert not reused and copied.video_codec == "h264" and copied.audio_codec == "aac"
    assert copied.metadata_json["video_payload_verified"] and copied.metadata_json["audio_channels"] == 2
    assert copied.metadata_json["dolby_atmos"] is False and not copied.metadata_json["dolby_vision"]
    assert file_digest(source) == before


@pytest.mark.skipif(not os.environ.get("TREASURE_TEST_DOLBY_SAMPLES") or not os.environ.get("TREASURE_TEST_DOLBY_FAILURE")
                    or not shutil.which("ffmpeg"), reason="explicit read-only real 6/8-channel samples required")
@pytest.mark.parametrize("channels", [6, 8])
def test_real_dv_ec3_to_aac_then_hls_preserves_video_and_source(original, tmp_path, channels):
    db, part, archive = original
    samples = Path(os.environ["TREASURE_TEST_DOLBY_SAMPLES"])
    video_path = samples / "video.mp4"
    audio_path = samples / "audio.m4a" if channels == 6 else Path(os.environ["TREASURE_TEST_DOLBY_FAILURE"])
    hashes = [file_digest(path) for path in (video_path, audio_path)]
    source = tmp_path / "dv-ec3.mp4"
    # Bound work to 3 seconds; these are new scratch fixtures, never archive edits.
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(video_path), "-i", str(audio_path),
        "-t", "3", "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-strict", "unofficial",
        "-tag:v", "hvc1", "-movflags", "+faststart+write_colr", str(source)], check=True, timeout=60)
    vstream, astream, duration = media._probe(source)
    assert astream["channels"] == channels and media._dovi_configuration(vstream)["rpu_present_flag"] == 1
    source_hash = file_digest(source)
    asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
    archive.asset_id, archive.video_codec, archive.audio_codec, archive.duration = asset.id, "hevc", "eac3", duration
    archive.width, archive.height = vstream["width"], vstream["height"]
    db.commit()
    copied, reused = media.ensure_playback_variant(db, part, archive, {"transcode_timeout_seconds": 60})
    assert not reused and copied.audio_codec == "aac" and copied.metadata_json["dolby_atmos"] is False
    output = tmp_path / "aac.mp4"
    output.write_bytes(read_asset_bytes(db, copied.asset_id))
    video, audio, _ = media._probe(output)
    assert audio["codec_name"] == "aac" and audio["channels"] == 2 and audio["channel_layout"] == "stereo"
    assert media._dovi_configuration(video) == media._dovi_configuration(vstream)
    assert media._packet_hash(output, "video", lambda: None) == media._packet_hash(source, "video", lambda: None)
    assert ec3_configuration(output) == {}
    packaged = hls.package_variant(db, copied.id)
    assert packaged.metadata_json["source_variant_id"] == copied.id
    assert packaged.metadata_json["source_kind"] == "playback"
    assert packaged.metadata_json["dolby_atmos"] is False and packaged.metadata_json["ec3"] == {}
    assert packaged.metadata_json["dolby_vision"] and packaged.metadata_json["payloads_verified"]
    assert packaged.metadata_json["compatibility_mode"] == "audio_only"
    assert file_digest(source) == source_hash
    assert [file_digest(path) for path in (video_path, audio_path)] == hashes
