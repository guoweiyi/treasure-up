import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yt_dlp

from app.config import settings
from app.ingest import media


def select_formats(policy, formats):
    with yt_dlp.YoutubeDL({"quiet": True, "merge_output_format": "mp4"}) as downloader:
        return list(media.format_selector(policy, builder=downloader.build_format_selector)({"formats": formats}))[0]


def fixture_formats():
    aac = {"format_id": "aac", "format": "aac", "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "url": "https://cdn.invalid/aac"}
    ec3 = {**aac, "format_id": "ec3", "acodec": "ec-3"}
    flac = {**aac, "format_id": "flac", "acodec": "flac"}
    avc = {"format_id": "avc", "format": "video", "vcodec": "avc1.640028", "acodec": "none", "ext": "mp4", "width": 1920, "height": 1080, "url": "https://cdn.invalid/avc"}
    dv = {**avc, "format_id": "dv", "vcodec": "hev1.2.4.L153.B0", "dynamic_range": "DV", "width": 3840, "height": 2160}
    av1 = {**dv, "format_id": "av1", "vcodec": "av01", "dynamic_range": "SDR", "width": 7680, "height": 4320}
    return [aac, ec3, flac, avc, dv, av1]


def test_dolby_preferences_preserve_quality_cap_and_h264_override():
    formats = fixture_formats()
    assert [f["format_id"] for f in select_formats({}, formats)["requested_formats"]] == ["dv", "ec3"]
    assert [f["format_id"] for f in select_formats({"quality": "1080p"}, formats)["requested_formats"]] == ["avc", "ec3"]
    assert [f["format_id"] for f in select_formats({"prefer_h264": True}, formats)["requested_formats"]] == ["avc", "ec3"]
    assert [f["format_id"] for f in select_formats({"prefer_dolby_vision": False, "prefer_dolby_atmos": False}, formats)["requested_formats"]] == ["av1", "flac"]


@pytest.mark.parametrize("config,verified", [({}, False), ({"dv_profile": 5, "rpu_present_flag": 0}, False), ({"dv_profile": 5, "rpu_present_flag": 1}, True)])
def test_dv_requires_dovi_configuration_and_rpu_flag(config, verified):
    video = {"codec_name": "hevc", "color_transfer": "smpte2084", "side_data_list": [
        {"side_data_type": "DOVI configuration record", **config, "untrusted_url": "https://source.invalid/?secret=bad"}]}
    result = media.inspect_dolby(Path("not-opened.mp4"), video, {"codec_name": "aac"})
    assert result["dolby_vision"] is verified
    assert "secret" not in json.dumps(result)
    assert not result["dolby_atmos"] and not result["spatial_audio_output_verified"]


@pytest.mark.parametrize("raw_profile,verified", [(None, False), ("Dolby Digital Plus", False), ("Dolby Digital Plus + Dolby Atmos", True)])
def test_eac3_container_profile_alone_cannot_prove_atmos(tmp_path, monkeypatch, raw_profile, verified):
    monkeypatch.setattr(settings, "scratch_dir", tmp_path)
    commands = []
    def raw_sample(arguments, output, maximum, timeout, guard):
        commands.append(arguments)
        output.write_bytes(b"test-eac3")
    monkeypatch.setattr(media, "_run_ffmpeg", raw_sample)
    monkeypatch.setattr(media, "ec3_configuration", lambda path: {"joc": True, "complexity_index_type_a": 16})
    probe = {"streams": [{"codec_type": "audio", "codec_name": "eac3", "profile": raw_profile}]}
    monkeypatch.setattr(media.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(probe).encode()))
    result = media.inspect_dolby(Path("source.mp4"), {"codec_name": "hevc"},
        {"codec_name": "eac3", "profile": "Dolby Digital Plus + Dolby Atmos", "channels": 6})
    assert result["dolby_atmos"] is verified and not result["dolby_vision"]
    assert commands[0][commands[0].index("-c:a") + 1] == "copy"
    assert result["audio_verification_sample_seconds"] == 10
    assert not list(tmp_path.glob("treasure-joc-*"))


def test_selected_eac3_cannot_be_silently_replaced_by_aac():
    from app.ingest.errors import IngestError
    candidate = [{"acodec": "ec-3"}]
    with pytest.raises(IngestError, match="EC-3") as error:
        media.verify_selected_audio(candidate, {"codec_name": "aac"})
    assert error.value.code == "audio_codec_mismatch"
    assert media.verify_selected_audio(candidate, {"codec_name": "eac3"}) is True
    assert media.verify_selected_audio([{"acodec": "mp4a.40.2"}], {"codec_name": "aac"}) is False


def test_missing_joc_never_repairs_published_or_unapproved_paths(tmp_path, monkeypatch):
    from app.ingest.errors import IngestError
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    path = tmp_path / "published.mp4"
    path.write_bytes(b"must-stay-unchanged")
    monkeypatch.setattr(media, "_run_ffmpeg", lambda args, output, *rest: output.write_bytes(b"sample"))
    monkeypatch.setattr(media, "ec3_configuration", lambda path: {"joc": False})
    probe = {"streams": [{"codec_type": "audio", "codec_name": "eac3", "profile": "Dolby Digital Plus + Dolby Atmos"}]}
    monkeypatch.setattr(media.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(probe).encode()))
    monkeypatch.setattr(media, "preserve_ec3_configuration", lambda *args, **kwargs: pytest.fail("published file must not be repaired"))
    for repair, code in [(False, "dolby_metadata_missing"), (True, "invalid_path")]:
        with pytest.raises(IngestError) as error:
            media.inspect_dolby(path, {}, {"codec_name": "eac3"}, repair_scratch=repair)
        assert error.value.code == code
    assert path.read_bytes() == b"must-stay-unchanged"


def test_mp4_dolby_mux_parameters_preserve_dovi_boxes_and_audio_tag():
    args = media._merge_options({"requested_formats": [fixture_formats()[4], fixture_formats()[1]]})
    assert args[args.index("-strict") + 1] == "unofficial"
    assert args[args.index("-tag:v") + 1] == "hvc1"
    assert args[args.index("-tag:a") + 1] == "ec-3"
    assert "write_colr" in args[args.index("-movflags") + 1]


@pytest.mark.skipif(not os.environ.get("TREASURE_TEST_DOLBY_SAMPLES") or not shutil.which("ffmpeg"), reason="explicit offline Dolby fixture required")
def test_offline_original_dv_atmos_streams_archive_without_payload_changes(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models import Base, Video, VideoPart
    from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP
    fixtures = Path(os.environ["TREASURE_TEST_DOLBY_SAMPLES"])
    video_path, audio_path = fixtures / "video.mp4", fixtures / "audio.m4a"
    assert video_path.is_file() and audio_path.is_file()
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    vstream, _, duration = media._probe(video_path)
    class OfflineDownloader(yt_dlp.YoutubeDL):
        def extract_info(self, url, download=False):
            return {"id": "BV1234567890", "ext": "mp4", "format_id": "dv+ec3", "requested_formats": [
                {"format_id": "dv", "vcodec": "hev1", "acodec": "none", "dynamic_range": "DV", "width": vstream["width"], "height": vstream["height"]},
                {"format_id": "ec3", "vcodec": "none", "acodec": "ec-3"}]}
        def process_info(self, info):
            info["filepath"] = self.params["outtmpl"]["default"].replace("%(ext)s", "mp4")
            info["__files_to_merge"] = [str(video_path), str(audio_path)]
            self.run_pp(FFmpegMergerPP(self), info)
        def urlopen(self, request): pytest.fail("offline fixture must never access network")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", OfflineDownloader)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        video = Video(bvid="BV1234567890", aid="123")
        db.add(video); db.flush()
        part = VideoPart(video_id=video.id, cid="11", duration=duration, position=1)
        db.add(part); db.flush()
        client = SimpleNamespace(cookies=[], view=lambda bvid: {"pages": [{"cid": "11", "page": 1}]})
        variant, reused = media.archive_media(db, client, video, part, {"max_download_bytes": 500_000_000})
        db.commit()
        evidence = video.metadata_json["media_properties"][variant.id]
        assert not reused and evidence["dolby_vision"] and evidence["dolby_atmos"]
        assert evidence["payloads_verified"] and set(evidence["payload_hashes"]) == {"video", "audio"}
        assert variant.video_codec == "hevc" and variant.audio_codec == "eac3"
        assert evidence["dovi"]["rpu_present_flag"] == 1
        assert evidence["atmos_evidence"] == "eac3_joc_bitstream_profile"
        assert evidence["ec3"]["joc"] is True
        assert evidence["ec3"]["complexity_index_type_a"] == 16
    engine.dispose()


@pytest.mark.skipif(not os.environ.get("TREASURE_TEST_DOLBY_FAILURE") or not shutil.which("ffmpeg"), reason="explicit offline AC-3 core / EC-3 dependent fixture required")
@pytest.mark.parametrize("resume_complete_merge", [False, True])
def test_offline_missing_source_joc_restores_fresh_and_resumed_unpublished_merge(tmp_path, monkeypatch, resume_complete_merge):
    import hashlib
    import subprocess
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models import Base, Video, VideoPart
    from app.playback.ec3 import ec3_configuration
    from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP

    source = Path(os.environ["TREASURE_TEST_DOLBY_FAILURE"])
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert ec3_configuration(source)["joc"] is False
    video_path, audio_path = tmp_path / "test-video.mp4", tmp_path / "test-audio.m4a"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=25", "-t", "1", "-c:v", "mpeg4", str(video_path)], check=True, timeout=60)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-t", "1", "-c", "copy", "-f", "mp4", str(audio_path)], check=True, timeout=60)
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    monkeypatch.setattr(settings, "media_root", tmp_path / "library")

    class OfflineDownloader(yt_dlp.YoutubeDL):
        def extract_info(self, url, download=False):
            return {"id": "BV124YF68Ekd", "ext": "mp4", "format_id": "test+ec3", "requested_formats": [
                {"format_id": "test", "vcodec": "mpeg4", "acodec": "none"},
                {"format_id": "ec3", "vcodec": "none", "acodec": "ec-3"}]}

        def process_info(self, info):
            info["filepath"] = self.params["outtmpl"]["default"].replace("%(ext)s", "mp4")
            if resume_complete_merge:
                # Simulate yt-dlp finding last attempt's completed but unpublished
                # file: ControlledMerger will not be called on this path.
                subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(video_path), "-i", str(audio_path),
                    "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", info["filepath"]], check=True, timeout=60)
            else:
                info["__files_to_merge"] = [str(video_path), str(audio_path)]
                self.run_pp(FFmpegMergerPP(self), info)

        def urlopen(self, request):
            pytest.fail("offline fixture must never access network")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", OfflineDownloader)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        video = Video(bvid="BV124YF68Ekd", aid="123")
        db.add(video); db.flush()
        part = VideoPart(video_id=video.id, cid="11", duration=1, position=1)
        db.add(part); db.flush()
        client = SimpleNamespace(cookies=[], view=lambda bvid: {"pages": [{"cid": "11", "page": 1}]})
        variant, reused = media.archive_media(db, client, video, part, {"max_download_bytes": 50_000_000})
        db.commit()
        evidence = video.metadata_json["media_properties"][variant.id]
        assert not reused and variant.audio_codec == "eac3"
        assert evidence["dolby_atmos"] and evidence["audio_channels"] == 8
        assert evidence["ec3"]["joc"] and evidence["ec3"]["complexity_index_type_a"] == 16
        assert evidence["ec3_configuration_evidence"] == "eac3_addbsi_sample"
        assert evidence["ec3_scratch_configuration_restored"]
        assert set(evidence["ec3_repair_payload_hashes"]) == {"video", "audio"}
        assert evidence["ec3_repair_payload_hashes"]["audio"] == media._packet_hash(audio_path, "audio", lambda: None)
        assert evidence["ec3_repair_payload_hashes"]["video"] == media._packet_hash(video_path, "video", lambda: None)
    engine.dispose()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
