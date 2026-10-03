import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Base, MediaVariant, Video, VideoPart
from app.playback.hls import dovi_configuration, load_hls_index, package_asset_ids, package_variant, parse_generated_playlist, render_manifest
from app.playback.loudness import analyze_variant, bypass_reason, loudness_result
from app.playback.tools import PlaybackError, probe_media
from app.storage.base import file_digest
from app.storage.service import ingest_file, materialize_asset


def sample_index():
    return {"schema": "treasure.hls.v1", "source_asset_id": str(uuid4()), "stream_copy": True,
        "init_asset_id": str(uuid4()), "target_duration": 9,
        "segments": [{"sequence": 0, "asset_id": str(uuid4()), "duration": 8.25},
                     {"sequence": 1, "asset_id": str(uuid4()), "duration": 1.0}]}


def test_manifest_keeps_asset_references_and_authorized_routes_only():
    index = sample_index()
    result = render_manifest(index, lambda asset: f"/api/playback/session/segments/{asset}")
    assert '#EXT-X-MAP:URI="/api/' in result and "#EXT-X-TARGETDURATION:9" in result
    assert "#EXTINF:8.250000" in result and "#EXT-X-ENDLIST" in result
    assert len(package_asset_ids(index)) == 3
    for bad in ("https://cloud.invalid/signed", "//external/asset", '/asset"\n#EXT-X-KEY'):
        with pytest.raises(PlaybackError):
            render_manifest(index, lambda _: bad)


def test_generated_playlist_rejects_unfinished_or_external_files():
    valid = '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:8.25,\nsegment_000000.m4s\n#EXT-X-ENDLIST\n'
    assert parse_generated_playlist(valid)[1][0]["duration"] == 8.25
    for malformed in (valid.replace("#EXT-X-ENDLIST", ""), valid.replace("segment_000000.m4s", "../secret"), valid.replace("8.25", "nan")):
        with pytest.raises(PlaybackError):
            parse_generated_playlist(malformed)


def test_loudness_only_attenuates_and_spatial_audio_is_bypassed():
    assert loudness_result(-10, -0.5)["gain_db"] == -8
    assert loudness_result(-25, -3)["gain_linear"] == 1
    assert loudness_result("-inf", "-inf")["status"] == "silent"
    with pytest.raises(PlaybackError):
        loudness_result("nan", -1)
    assert bypass_reason({"codec_name": "eac3", "channels": 2})[1] is True
    assert bypass_reason({"codec_name": "truehd", "channels": 8})[1] is True
    assert bypass_reason({"codec_name": "aac", "channels": 6}) == ("multichannel_audio", False)


@pytest.mark.skipif(not shutil.which(str(settings.ffmpeg_path)) or not shutil.which(str(settings.ffprobe_path)), reason="FFmpeg integration tools unavailable")
def test_real_hls_streamcopy_packet_identity_and_analysis_original_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = tmp_path / "input.mp4"
    # Keyframes every 8 seconds intentionally exceed the default six-second target.
    subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000", "-t", "17", "-c:v", "libx264", "-g", "80",
        "-keyint_min", "80", "-sc_threshold", "0", "-c:a", "aac", "-movflags", "+faststart", str(source)], check=True, capture_output=True)
    before = file_digest(source)
    with Session(engine) as db:
        asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
        video = Video(bvid="BVsynthetic"); db.add(video); db.flush()
        part = VideoPart(video_id=video.id, cid="test", duration=17); db.add(part); db.flush()
        original = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="synthetic",
            quality="source", width=128, height=72, video_codec="h264", audio_codec="aac", duration=17)
        db.add(original); db.flush()
        analysis = analyze_variant(db, original.id)
        assert analysis["status"] == "ready" and 0 < analysis["gain_linear"] <= 1
        package = package_variant(db, original.id)
        index = load_hls_index(db, package.asset_id)
        assert any(segment["duration"] > 6 for segment in index["segments"])
        assert package.metadata_json["loudness"] == analysis
        folder = settings.scratch_dir / "verify"; folder.mkdir()
        initialization = materialize_asset(db, index["init_asset_id"], folder / "init.mp4")
        lines = ['#EXTM3U', '#EXT-X-VERSION:7', f'#EXT-X-TARGETDURATION:{index["target_duration"]}', '#EXT-X-MAP:URI="init.mp4"']
        for i, segment in enumerate(index["segments"]):
            name = f"segment_{i}.m4s"
            materialize_asset(db, segment["asset_id"], folder / name)
            lines.extend([f'#EXTINF:{segment["duration"]},', name])
        playlist = folder / "index.m3u8"; playlist.write_text("\n".join([*lines, "#EXT-X-ENDLIST"]))
        def packet_hash(path, stream):
            return subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-i", str(path), "-map", stream,
                "-c", "copy", "-f", "hash", "-hash", "sha256", "-"], capture_output=True, check=True).stdout
        assert packet_hash(source, "0:v:0") == packet_hash(playlist, "0:v:0")
        assert packet_hash(source, "0:a:0") == packet_hash(playlist, "0:a:0")
        subprocess.run([str(settings.ffmpeg_path), "-v", "error", "-xerror", "-i", str(playlist), "-f", "null", "-"], capture_output=True, check=True)
        assert file_digest(source) == before
        assert package_variant(db, original.id).id == package.id
    engine.dispose()


@pytest.mark.skipif(not os.environ.get("TREASURE_DOLBY_SAMPLE_DIR"), reason="Explicit authorized Dolby sample directory required")
def test_real_dolby_hls_init_and_payloads_preserved(tmp_path, monkeypatch):
    """Opt-in local media-only fixtures: no credentials, manifests or source network."""
    samples = Path(os.environ["TREASURE_DOLBY_SAMPLE_DIR"])
    assert file_digest(samples / "video.mp4")[0] == "4fef9efb210b355261ca2b6d7e8c96a07466c3d466b3a192342525ba380496c8"
    assert file_digest(samples / "audio.m4a")[0] == "a9e8b9e910845a9674ee90660603a5af9f9a23b474a68d89e63fc610eb54b44d"
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    source = tmp_path / "dolby.mp4"
    subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-i", str(samples / "video.mp4"),
        "-i", str(samples / "audio.m4a"), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-strict", "unofficial",
        "-movflags", "+write_colr", "-tag:v", "hvc1", "-tag:a", "ec-3", str(source)], check=True, capture_output=True)
    before = file_digest(source)
    probe = probe_media(source)
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    duration = float(probe["format"]["duration"])
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
        video = Video(bvid="BVdolbyfixture"); db.add(video); db.flush()
        part = VideoPart(video_id=video.id, cid="dolby", duration=duration); db.add(part); db.flush()
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="dolby",
            video_codec="hevc", audio_codec="eac3", width=video_stream["width"], height=video_stream["height"], duration=duration)
        db.add(variant); db.flush()
        analysis = analyze_variant(db, variant.id)
        assert analysis["status"] == "bypassed" and analysis["atmos_bypass"] is True
        package = package_variant(db, variant.id)
        metadata = package.metadata_json
        assert metadata["dovi"] == dovi_configuration(video_stream)
        assert metadata["payloads_verified"] is True
        assert metadata["payload_hashes"] == {
            "video": "42238cb1d3b87270d6dffa99f68343026db2e7574fc0695ed11f4f983f464de2",
            "audio": "10004ecb6a36f99b08852a86cff2c96f35b5375fa82696e7a92b3ca1600cabab"}
        assert metadata["spatial_audio_output_verified"] is False
        index = load_hls_index(db, package.asset_id)
        initialization = materialize_asset(db, index["init_asset_id"], settings.scratch_dir / "verify-init.mp4")
        init_video = next(s for s in probe_media(initialization)["streams"] if s["codec_type"] == "video")
        assert dovi_configuration(init_video) == dovi_configuration(video_stream)
        assert file_digest(source) == before
    engine.dispose()
