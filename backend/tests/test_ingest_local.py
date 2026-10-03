import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest import local_import, media, runner
from app.ingest.errors import IngestError
from app.models import Base, Creator, DanmakuSnapshot, MediaVariant, Video, VideoPart
from app.storage.service import read_asset_bytes


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(local_import, "_probe", lambda path: ({"codec_name": "h264", "width": 1920, "height": 1080}, {"codec_name": "aac"}, 3.5))
    monkeypatch.setattr(runner, "BiliClient", lambda *args, **kwargs: pytest.fail("local import contacted source"))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_import_local_is_deterministic_without_fake_up(db, tmp_path):
    path = tmp_path / "sample.mp4"; path.write_bytes(b"mock-media")
    first = local_import.import_local(db, path, {"title": "本地样本"})
    db.commit()
    second = local_import.import_local(db, path, {})
    assert first.id == second.id and first.bvid.startswith("local_")
    assert first.capture_status == "local_import"
    assert db.scalar(select(func.count()).select_from(Creator)) == 0
    assert db.scalar(select(func.count()).select_from(MediaVariant)) == 1


def test_import_local_preserves_real_source_ids_and_danmaku(db, tmp_path):
    path = tmp_path / "sample.mp4"; path.write_bytes(b"mock-media")
    dm = tmp_path / "danmaku.json"
    dm.write_text(json.dumps([{"id": "9007199254740993", "text": "中文", "time": 1, "mode": 1, "fontSize": 32, "color": "#ffffff"}]), encoding="utf-8")
    video = local_import.import_local(db, path, {"bvid": "BV1234567890", "aid": "123", "cid": "456", "owner": {"uid": "789", "name": "UP"}, "danmaku_mode_space": "artplayer"}, dm)
    assert video.aid == "123"
    assert db.scalar(select(VideoPart)).cid == "456"
    assert db.scalar(select(func.count()).select_from(Creator)) == 1
    snapshot = db.scalar(select(DanmakuSnapshot))
    data = json.loads(read_asset_bytes(db, snapshot.data_asset_id))
    assert data[0]["mode"] == 5 and data[0]["size"] == 32
    assert data[0]["id"] == "9007199254740993"


def test_invalid_source_metadata_rejected_before_persistence(db, tmp_path):
    path = tmp_path / "sample.mp4"; path.write_bytes(b"mock-media")
    with pytest.raises(IngestError):
        local_import.import_local(db, path, {"bvid": "made-up"})
    assert db.scalar(select(func.count()).select_from(Video)) == 0


@pytest.mark.parametrize("quality,short_side", [("best", 2160), ("1080p", 1080), ("720p", 720), ("480p", 480), ("4k", 2160)])
@pytest.mark.parametrize("portrait", [False, True])
def test_public_quality_policy_selects_actual_short_side(quality, short_side, portrait):
    import yt_dlp
    formats = [{"format_id": "audio", "format": "audio", "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "url": "https://cdn.invalid/audio"}]
    for size in (480, 720, 1080, 2160):
        width, height = ((size, size * 16 // 9) if portrait else (size * 16 // 9, size))
        formats.append({"format_id": str(size), "format": str(size), "width": width, "height": height,
                        "vcodec": "av01", "acodec": "none", "ext": "mp4", "url": "https://cdn.invalid/video"})
    with yt_dlp.YoutubeDL({"quiet": True, "merge_output_format": "mp4"}) as downloader:
        selector = media.format_selector({"quality": quality}, builder=downloader.build_format_selector)
        selected = list(selector({"formats": formats}))
    assert len(selected) == 1
    assert selected[0]["requested_formats"][0]["format_id"] == str(short_side)
    assert selected[0]["requested_formats"][1]["format_id"] == "audio"


def test_prefer_h264_has_real_codec_preference_and_av1_fallback():
    import yt_dlp
    audio = {"format_id": "a", "format": "audio", "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "url": "https://cdn.invalid/a"}
    h264 = {"format_id": "h264", "format": "h264", "vcodec": "avc1.640028", "acodec": "none", "ext": "mp4", "width": 1920, "height": 1080, "url": "https://cdn.invalid/h"}
    av1 = {**h264, "format_id": "av1", "vcodec": "av01.0.08M.08"}
    with yt_dlp.YoutubeDL({"quiet": True}) as downloader:
        selector = media.format_selector({"quality": "1080p", "prefer_h264": True}, builder=downloader.build_format_selector)
        assert list(selector({"formats": [audio, h264, av1]}))[0]["requested_formats"][0]["format_id"] == "h264"
        assert list(selector({"formats": [audio, av1]}))[0]["requested_formats"][0]["format_id"] == "av1"


def test_arbitrary_download_expression_not_a_quality():
    with pytest.raises(IngestError):
        media.format_selector({"quality": "best; curl anything"})
