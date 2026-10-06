import hashlib
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AssetLocation, Base, Creator, MediaVariant, PlatformUser, StorageProfile, Video, VideoCreator, VideoPart
from app.storage.base import StorageError, content_key, safe_key
from app.storage.lifecycle import purge_location, retire_location
from app.storage.naming import MediaName, is_managed_key, media_name_for_part
from app.storage.service import ingest_file, migrate_asset, read_asset_bytes, resolve_asset


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def test_unicode_names_keep_identity_container_and_full_digest():
    digest = "a" * 64
    name = MediaName("春日音乐会", "小云", "BV1d7hx6yEiE", 2, "第二场")
    key = name.key(digest, "video/x-matroska")
    assert key.startswith("videos/春日音乐会-小云 [BV1d7hx6yEiE]-P02-第二场")
    assert key.endswith(f"[{digest}].mkv")
    assert is_managed_key(key, digest)
    assert not is_managed_key(key, "b" * 64)
    assert not is_managed_key("unmanaged/" + key, digest)
    assert replace(name, variant="playback").key(digest, "video/mp4") != name.key(digest, "video/mp4")
    assert "播放副本" in replace(name, variant="playback").key(digest, "video/mp4")


def test_untrusted_metadata_cannot_create_paths_or_overlong_components():
    name = MediaName("../../CON\\片名%2f?#\x00\u202e" + "长" * 500, "AUX:" + "😀" * 100,
                     "../../BV" + "x" * 100, 100000, "..\\第二P" + "续" * 100, "playback")
    key = name.key("c" * 64, "video/mp4")
    assert key.count("/") == 1
    assert all(len(part.encode("utf-8")) <= 255 for part in key.split("/"))
    assert "\u202e" not in key and "\\" not in key and "%" not in key
    assert is_managed_key(key, "c" * 64)


@pytest.mark.parametrize("key", ["videos/CON .txt", "videos/COM¹.txt", "videos/ＬＰＴ１.txt", "videos/\u202e注入.mp4",
    "videos/文件\x00.mp4", "videos/片名%2f越界", "videos/片名?query", "videos/片名#anchor", "videos/片名\\越界",
    "videos/../escape", "videos/" + "长" * 86, "videos/\ud800"])
def test_unicode_key_validation_preserves_path_and_url_guards(key):
    with pytest.raises(StorageError):
        safe_key(key)


def test_readable_ingest_is_plaintext_deduplicated_and_migration_keeps_name(library, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"plain media bytes" * 100)
    name = MediaName("我的稿件", "原UP", "BV1d7hx6yEiE")
    asset = ingest_file(library, source, kind="media", mime_type="video/mp4", media_name=name)
    original = library.scalar(select(AssetLocation).where(AssetLocation.asset_id == asset.id))
    original_key = original.object_key
    assert original_key.startswith("videos/我的稿件-原UP")
    assert resolve_asset(library, asset.id)["path"].read_bytes() == source.read_bytes()
    again = ingest_file(library, source, kind="media", mime_type="video/mp4", media_name=replace(name, title="改名"))
    assert again.id == asset.id
    assert len(list(library.scalars(select(AssetLocation)))) == 1
    assert original.object_key == original_key
    target = StorageProfile(name="NAS", kind="local", config={"root": str(tmp_path / "replica")}, enabled=True)
    library.add(target)
    library.flush()
    copied = migrate_asset(library, asset.id, target.id)
    assert copied.object_key == original_key
    assert (tmp_path / "replica" / copied.object_key).read_bytes() == source.read_bytes()
    retire_location(library, original.id)
    assert purge_location(library, original.id)["state"] == "deleted"
    assert read_asset_bytes(library, asset.id) == source.read_bytes()


def test_existing_hash_archive_is_unchanged_but_explicit_copy_gets_metadata_name(library, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"old media bytes")
    asset = ingest_file(library, source, kind="media", mime_type="video/mp4")
    original = library.scalar(select(AssetLocation))
    assert original.object_key == content_key(asset.sha256)
    user = PlatformUser(uid="123", display_name="真实昵称")
    video = Video(bvid="BV1d7hx6yEiE", title="原稿件")
    library.add_all([user, video]); library.flush()
    creator = Creator(user_id=user.id)
    part = VideoPart(video_id=video.id, cid="456", title="第二P", position=2)
    library.add_all([creator, part]); library.flush()
    library.add(VideoCreator(video_id=video.id, creator_id=creator.id, role="owner"))
    library.add(MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="original"))
    library.flush()
    name = media_name_for_part(library, part)
    assert name.creator == "真实昵称"
    ingest_file(library, source, kind="media", mime_type="video/mp4", media_name=name)
    assert len(list(library.scalars(select(AssetLocation)))) == 1
    assert original.object_key == content_key(asset.sha256)
    target = StorageProfile(name="new NAS", kind="local", config={"root": str(tmp_path / "replica")}, enabled=True)
    library.add(target); library.flush()
    copied = migrate_asset(library, asset.id, target.id)
    assert copied.object_key == name.key(asset.sha256, asset.mime_type)
    assert original.object_key == content_key(asset.sha256)
    assert (settings.media_root / original.object_key).exists()


def test_metadata_files_keep_hash_layout(library, tmp_path):
    source = tmp_path / "metadata.json"
    source.write_bytes(b"[]")
    asset = ingest_file(library, source, kind="danmaku", mime_type="application/json")
    assert library.scalar(select(AssetLocation.object_key)) == content_key(hashlib.sha256(b"[]").hexdigest())
    assert read_asset_bytes(library, asset.id) == b"[]"


def test_copy_resumes_its_original_filename_after_metadata_changes(library, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"checkpoint media")
    name = MediaName("原始稿件名", "UP主", "BV1d7hx6yEiE")
    asset = ingest_file(library, source, kind="media", mime_type="video/mp4", media_name=name)
    target = StorageProfile(name="copy", kind="local", config={"root": str(tmp_path / "copy")}, enabled=True)
    library.add(target); library.flush()
    checkpoint_key = replace(name, title="上传时旧标题").key(asset.sha256, asset.mime_type)
    checkpoint = {"key": checkpoint_key, "sha256": asset.sha256, "part_size": 1024, "upload_id": "fixture"}
    copied = migrate_asset(library, asset.id, target.id, checkpoint=checkpoint)
    assert copied.object_key == checkpoint_key
    assert (tmp_path / "copy" / checkpoint_key).read_bytes() == source.read_bytes()


def test_copy_rejects_checkpoint_pointing_outside_owned_objects(library, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"checkpoint media")
    asset = ingest_file(library, source, kind="media", mime_type="video/mp4")
    target = StorageProfile(name="copy", kind="local", config={"root": str(tmp_path / "copy")}, enabled=True)
    library.add(target); library.flush()
    with pytest.raises(StorageError, match="ownership"):
        migrate_asset(library, asset.id, target.id,
            checkpoint={"key": "other/video.mp4", "sha256": asset.sha256, "upload_id": "fixture"})
    assert not list((tmp_path / "copy").iterdir())
