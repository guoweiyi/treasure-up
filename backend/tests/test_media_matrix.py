"""Small real encodes exercise production muxing on every Docker/CI test run.

Synthetic E-AC-3 is deliberately not Atmos; the separate authorized fixtures
are still needed to test actual JOC and Dolby Vision payloads.
"""
import shutil
import subprocess
import json
from fractions import Fraction

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Base, MediaVariant, Video, VideoPart
from app.playback.hls import load_hls_index, package_variant, packet_digest
from app.playback.tools import probe_media
from app.storage.base import file_digest
from app.storage.service import ingest_file, materialize_asset


def packet_timeline(path):
    result = subprocess.run([str(settings.ffprobe_path), "-v", "error", "-show_packets", "-show_streams",
        "-show_data_hash", "sha256", "-of", "json", str(path)], check=True, capture_output=True, timeout=60)
    data = json.loads(result.stdout)
    streams = {stream["index"]: stream for stream in data["streams"]}
    rows = {}
    for packet in data["packets"]:
        stream = streams[packet["stream_index"]]
        scale = Fraction(stream["time_base"])
        rows.setdefault(stream["codec_type"], []).append((packet["data_hash"],
            int(packet["pts"]) * scale, int(packet["dts"]) * scale, packet["flags"]))
    return rows


@pytest.mark.skipif(not shutil.which(str(settings.ffmpeg_path)) or not shutil.which(str(settings.ffprobe_path)),
                    reason="FFmpeg integration tools unavailable")
@pytest.mark.parametrize("codec,audio,dimensions,hdr", [
    ("h264", "aac", "72x128", False),
    ("hevc", "aac", "128x72", False),
    ("av1", "aac", "128x72", False),
    ("hevc", "eac3", "128x72", True),
    ("h264", None, "128x72", False),
], ids=["portrait-avc", "hevc-aac", "av1-aac", "hdr10-ec3-not-atmos", "silent-avc"])
def test_real_codec_matrix_preserves_packets_and_reuses_complete_package(tmp_path, monkeypatch, codec, audio, dimensions, hdr):
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    encoder = {"h264": "libx264", "hevc": "libx265", "av1": "libaom-av1"}[codec]
    source = tmp_path / "source.mp4"
    args = [str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            f"testsrc2=size={dimensions}:rate=12"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    args += ["-t", "4", "-c:v", encoder, "-threads", "2", "-g", "24"]
    if codec == "hevc":
        args += ["-preset", "ultrafast", "-x265-params", "pools=1:frame-threads=1:log-level=error", "-tag:v", "hvc1"]
    elif codec == "av1":
        args += ["-cpu-used", "8", "-crf", "45"]
    else:
        args += ["-preset", "ultrafast", "-sc_threshold", "0"]
    if hdr:
        args += ["-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"]
        args[args.index("-x265-params") + 1] += ":colorprim=9:transfer=16:colormatrix=9"
    if audio:
        args += ["-c:a", audio, "-b:a", "192k", "-ac", "2"]
    args += ["-movflags", "+faststart+write_colr", str(source)]
    subprocess.run(args, check=True, capture_output=True, timeout=60)
    before = file_digest(source)
    source_probe = probe_media(source)
    video_stream = next(s for s in source_probe["streams"] if s["codec_type"] == "video")
    if hdr:
        assert video_stream["color_transfer"] == "smpte2084"
        assert video_stream["color_primaries"] == "bt2020"
    duration = float(source_probe["format"]["duration"])
    source_timeline = packet_timeline(source)
    # The actual fixture, not just -g, must contain the two-second split point.
    assert [packet[1] for packet in source_timeline["video"] if "K" in packet[3]] == [0, 2]
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            video = Video(bvid="BVcodecfixture"); db.add(video); db.flush()
            part = VideoPart(video_id=video.id, cid="fixture", duration=duration); db.add(part); db.flush()
            asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
            original = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="fixture",
                video_codec=codec, audio_codec=audio or "", width=video_stream["width"], height=video_stream["height"], duration=duration)
            db.add(original); db.flush()
            packaged = package_variant(db, original.id, segment_seconds=2)
            index = load_hls_index(db, packaged.asset_id)
            assert len(index["segments"]) >= 2
            assert packaged.metadata_json["dolby_vision"] is False
            assert packaged.metadata_json["dolby_atmos"] is False
            folder = settings.scratch_dir / "delivered"; folder.mkdir()
            materialize_asset(db, index["init_asset_id"], folder / "init.mp4")
            lines = ["#EXTM3U", "#EXT-X-VERSION:7", f'#EXT-X-TARGETDURATION:{index["target_duration"]}', '#EXT-X-MAP:URI="init.mp4"']
            for position, segment in enumerate(index["segments"]):
                name = f"segment_{position}.m4s"
                materialize_asset(db, segment["asset_id"], folder / name)
                lines += [f'#EXTINF:{segment["duration"]},', name]
            playlist = folder / "index.m3u8"
            playlist.write_text("\n".join([*lines, "#EXT-X-ENDLIST", ""]), encoding="utf-8")
            for kind in (["video", "audio"] if audio else ["video"]):
                assert packet_digest(source, kind) == packet_digest(playlist, kind)
            delivered_timeline = packet_timeline(playlist)
            offsets = []
            for kind, packets in source_timeline.items():
                actual = delivered_timeline[kind]
                assert [packet[0] for packet in packets] == [packet[0] for packet in actual]
                pts_offsets = {right[1] - left[1] for left, right in zip(packets, actual)}
                dts_offsets = {right[2] - left[2] for left, right in zip(packets, actual)}
                assert pts_offsets == dts_offsets and len(pts_offsets) == 1
                offsets.extend(pts_offsets)
            # FFmpeg rounds a shared negative-DTS shift to each track clock.
            # Every packet's PTS/DTS delta stays exact within its track; only
            # that one-time cross-clock rounding may differ by one track tick.
            max_tick = max(Fraction(stream["time_base"]) for stream in source_probe["streams"])
            assert max(offsets) - min(offsets) <= max_tick
            for position in range(len(index["segments"])):
                independent = folder / "independent.mp4"
                independent.write_bytes((folder / "init.mp4").read_bytes() +
                                        (folder / f"segment_{position}.m4s").read_bytes())
                assert "K" in packet_timeline(independent)["video"][0][3]
            delivered = probe_media(playlist)
            delivered_video = next(s for s in delivered["streams"] if s["codec_type"] == "video")
            assert delivered_video["codec_name"] == codec
            if hdr:
                assert delivered_video["color_transfer"] == "smpte2084"
                assert delivered_video["color_primaries"] == "bt2020"
            subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-xerror", "-threads", "2",
                "-i", str(playlist), "-f", "null", "-"], check=True, capture_output=True, timeout=60)
            assert package_variant(db, original.id, segment_seconds=2).id == packaged.id
            assert file_digest(source) == before
    finally:
        engine.dispose()
