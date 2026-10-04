from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import jobs
from app.config import settings
from app.db import get_db, make_engine
from app.main import app
from app import library_deletion as deletion
from app.models import (Asset, AssetLocation, AssetRef, AuditLog, BackupSet, Base, CaptureRun, Collection,
    CollectionItem, Comment, CommentAsset, CommentVersion, Creator, DanmakuSnapshot, Job, MediaVariant,
    PlatformUser, PlaybackSession, Setting, SourceAccount, SourceSubscription, StorageObservation,
    StorageProfile, SubtitleTrack, User, UserSnapshot, Video, VideoAnnotation, VideoCreator, VideoPart,
    VideoStar, WatchProgress, VideoStatSnapshot, utcnow)
from app.security import hash_password
from app.storage.base import content_key
from app.storage.service import ingest_file
from test_api import login


@pytest.fixture
def library(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'deletion.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(jobs, "heartbeat", lambda *args: args[-1].wait())
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    with factory() as db:
        db.add_all([User(username=name, password_hash=hash_password("a-test-password-123"), role=name)
                    for name in ("admin", "editor", "reader")])
        db.add(StorageProfile(name="fixture", kind="local", config={"root": str(tmp_path / "media")}, is_default=True))
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app, base_url="http://localhost") as client:
            yield client, db, tmp_path
        app.dependency_overrides.clear()
    engine.dispose()


def asset(db, tmp, name):
    path = tmp / ("source-" + name)
    path.write_bytes((name + "-fixture").encode())
    return ingest_file(db, path, kind="fixture", mime_type="application/octet-stream")


def video(db, bvid="BV1234567890"):
    row = Video(bvid=bvid, title=bvid)
    db.add(row); db.flush()
    part = VideoPart(video_id=row.id, cid=str(len(list(db.scalars(select(Video.id))))) + "00")
    db.add(part); db.flush()
    return row, part


def request(client, kind, identifier):
    prefix = f"/api/v1/admin/{kind}/{identifier}"
    summary = client.get(prefix + "/deletion-preview")
    assert summary.status_code == 200, summary.text
    result = client.post(prefix + "/deletion", json={"confirm": True, "preview_token": summary.json()["preview_token"]})
    assert result.status_code == 202, result.text
    return result.json()["job_id"], summary.json()


def finish(db, job_id, maximum=20):
    for _ in range(maximum):
        db.expunge_all()
        job = db.get(Job, job_id)
        if job.status in {"succeeded", "partial", "failed", "blocked"}:
            return job
        job.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
        jobs.run_job_id(job_id)
    raise AssertionError("Deletion did not finish within bounded slices")


def test_video_deletion_removes_entire_graph_but_preserves_shared_content(library):
    client, db, tmp = library
    login(client)
    row, part = video(db)
    other, other_part = video(db, "BV1234567891")
    cover, media, index, segment, dm, subtitle, image, avatar = [asset(db, tmp, name) for name in
        ("cover", "media", "index", "segment", "dm", "subtitle", "image", "avatar")]
    row.cover_asset_id = other.cover_asset_id = cover.id
    author = PlatformUser(uid="100", display_name="shared", avatar_asset_id=avatar.id)
    orphan = PlatformUser(uid="101", display_name="orphan")
    db.add_all([author, orphan]); db.flush()
    snapshot = UserSnapshot(user_id=author.id, display_name="shared", avatar_asset_id=avatar.id)
    orphan_snapshot = UserSnapshot(user_id=orphan.id)
    db.add_all([snapshot, orphan_snapshot]); db.flush()
    comment = Comment(video_id=row.id, rpid="1", author_user_id=author.id, author_snapshot_id=snapshot.id)
    orphan_comment = Comment(video_id=row.id, rpid="2", author_user_id=orphan.id, author_snapshot_id=orphan_snapshot.id)
    shared_comment = Comment(video_id=other.id, rpid="3", author_user_id=author.id, author_snapshot_id=snapshot.id)
    db.add_all([comment, orphan_comment, shared_comment]); db.flush()
    original = MediaVariant(part_id=part.id, asset_id=media.id, format_key="original")
    hls = MediaVariant(part_id=part.id, asset_id=index.id, format_key="hls", kind="hls")
    run = CaptureRun(video_id=row.id)
    db.add_all([original, hls, run]); db.flush()
    admin = db.scalar(select(User).where(User.username == "admin"))
    account = SourceAccount(name="fixture", secret_encrypted="never-read")
    collection = Collection(source_id="1000", title="fixture")
    db.add_all([account, collection]); db.flush()
    source = SourceSubscription(collection_id=collection.id, account_id=account.id, enabled=True)
    db.add_all([source, CollectionItem(collection_id=collection.id, source_resource_id="2:1", video_id=row.id),
        CommentVersion(comment_id=comment.id, run_id=run.id, author_snapshot_id=snapshot.id),
        CommentAsset(comment_id=comment.id, asset_id=image.id),
        DanmakuSnapshot(part_id=part.id, run_id=run.id, raw_asset_ids=[dm.id], data_asset_id=dm.id),
        SubtitleTrack(part_id=part.id, language="zh", raw_asset_id=subtitle.id, asset_id=subtitle.id),
        VideoAnnotation(video_id=row.id), VideoStar(user_id=admin.id, video_id=row.id),
        WatchProgress(user_id=admin.id, part_id=part.id), VideoStatSnapshot(video_id=row.id, run_id=run.id),
        PlaybackSession(user_id=admin.id, variant_id=hls.id, protocol="hls", expires_at=utcnow() + timedelta(hours=1)),
        StorageObservation(profile_id=db.scalar(select(StorageProfile.id)), asset_id=media.id, latency_ms=1, elapsed_ms=1, bytes_read=1, succeeded=True),
        AssetRef(asset_id=segment.id, entity_type="asset", entity_id=index.id, purpose="hls_segment"),
        AssetRef(asset_id=media.id, entity_type="asset", entity_id=index.id, purpose="hls_source"),
        AssetRef(asset_id=index.id, entity_type="media_variant", entity_id=hls.id, purpose="playback"),
        AssetRef(asset_id=image.id, entity_type="comment", entity_id=comment.id, purpose="attachment")])
    producer = jobs.enqueue(db, "archive_video", row.id, account.id)
    producer.checkpoint = {"comments": {"source_raw": "must disappear"}}
    scanner = jobs.enqueue(db, "scan_collection", collection.id, account.id)
    db.commit()
    deleting, summary = request(client, "videos", row.id)
    for item in (source, collection, producer, scanner):
        db.refresh(item)
    assert summary["video_count"] == 1 and summary["comment_count"] == 2
    assert summary["shared_asset_count"] >= 1
    assert not db.get(SourceSubscription, source.id).enabled
    assert not db.get(Collection, collection.id).enabled
    assert db.get(Job, producer.id).status == db.get(Job, scanner.id).status == "cancelled"
    job = finish(db, deleting)
    assert job.status == "succeeded", job.error
    assert job.result["deleted_videos"] == 1 and job.result["retained_shared_assets"] >= 1
    assert db.get(Video, row.id) is None and db.get(Video, other.id)
    for model in (VideoPart, CommentVersion, CommentAsset, MediaVariant, DanmakuSnapshot, SubtitleTrack,
                  VideoAnnotation, VideoStar, WatchProgress, VideoStatSnapshot, PlaybackSession, CaptureRun, CollectionItem):
        expected = 1 if model is VideoPart else 0
        assert db.scalar(select(func.count()).select_from(model)) == expected, model
    assert db.get(PlatformUser, orphan.id) is None and db.get(UserSnapshot, orphan_snapshot.id) is None
    assert db.get(PlatformUser, author.id) and db.get(UserSnapshot, snapshot.id)
    assert db.get(Comment, shared_comment.id)
    for item in (media, index, segment, dm, subtitle, image):
        assert db.get(Asset, item.id) is None
        assert not (tmp / "media" / content_key(item.sha256)).exists()
    for item in (cover, avatar):
        assert db.get(Asset, item.id)
        assert (tmp / "media" / content_key(item.sha256)).exists()
    assert db.get(Job, producer.id).checkpoint == {"deleted_by": deleting}
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == "request_library_deletion"))
    assert deletion.is_suppressed(db, bvid=row.bvid)


def test_creator_deletion_detaches_collaboration_and_preserves_shared_comment_author(library):
    client, db, _ = library
    login(client)
    owned, _ = video(db)
    shared, _ = video(db, "BV1234567891")
    person = PlatformUser(uid="100", display_name="creator")
    db.add(person); db.flush()
    creator = Creator(user_id=person.id)
    db.add(creator); db.flush()
    db.add_all([VideoCreator(video_id=owned.id, creator_id=creator.id, role="owner"),
        VideoCreator(video_id=shared.id, creator_id=creator.id, role="staff"),
        Comment(video_id=shared.id, rpid="1", author_user_id=person.id),
        Collection(kind="creator", source_id=person.uid)])
    db.commit()
    deleting, summary = request(client, "creators", creator.id)
    assert summary["video_count"] == summary["collaboration_count"] == 1
    result = finish(db, deleting)
    assert result.status == "succeeded", result.error
    assert result.result["collaborations_detached"] == 1
    assert db.get(Creator, creator.id) is None and db.get(Video, owned.id) is None
    assert db.get(Video, shared.id) and db.get(PlatformUser, person.id)
    assert db.scalar(select(func.count()).select_from(VideoCreator)) == 0
    assert deletion.is_suppressed(db, uid=person.uid)


def test_deletion_waits_for_cancelled_producer_lease_to_end(library):
    client, db, _ = library
    login(client)
    row, _ = video(db)
    producer = jobs.enqueue(db, "archive_video", row.id)
    producer.status, producer.lease_owner = "running", "old-worker"
    producer.lease_expires_at = utcnow() + timedelta(minutes=1)
    db.commit()
    deleting, _ = request(client, "videos", row.id)
    assert jobs.run_job_id(deleting)["status"] == "queued"
    db.expunge_all()
    assert db.get(Video, row.id)
    assert db.get(Job, deleting).attempts == 0
    producer = db.get(Job, producer.id)
    producer.lease_owner = producer.lease_expires_at = None
    db.commit()
    assert finish(db, deleting).status == "succeeded"


def test_storage_delete_failure_retains_ledger_and_retry_confirms_missing_object(library, monkeypatch):
    from app.storage.local import LocalStorage
    client, db, tmp = library
    login(client)
    row, _ = video(db)
    cover = asset(db, tmp, "cover")
    row.cover_asset_id = cover.id
    db.commit()
    deleting, _ = request(client, "videos", row.id)
    db.get(Job, deleting).max_attempts = 1
    db.commit()
    original_delete = LocalStorage.delete
    def interrupted(self, key, **kwargs):
        original_delete(self, key, **kwargs)
        raise RuntimeError("provider secret=https://private.invalid/?token=SECRET")
    monkeypatch.setattr(LocalStorage, "delete", interrupted)
    job = finish(db, deleting)
    assert job.status == "partial" and "SECRET" not in job.error
    assert db.get(Video, row.id) is None and db.get(Asset, cover.id)
    assert cover.id in job.checkpoint["asset_ids"]
    assert not (tmp / "media" / content_key(cover.sha256)).exists()
    monkeypatch.setattr(LocalStorage, "delete", original_delete)
    result = client.post(f"/api/v1/admin/jobs/{deleting}/retry")
    assert result.status_code == 200
    assert finish(db, deleting).status == "succeeded"
    assert db.get(Asset, cover.id) is None


def test_backup_protected_assets_are_reported_and_retained(library):
    client, db, tmp = library
    login(client)
    row, _ = video(db)
    cover = asset(db, tmp, "protected-cover")
    row.cover_asset_id = cover.id
    db.add(BackupSet(status="complete", manifest={"assets": [{"id": cover.id}]}))
    db.commit()
    deleting, summary = request(client, "videos", row.id)
    assert summary["backup_protected_asset_count"] == 1
    job = finish(db, deleting)
    assert job.status == "succeeded" and job.result["retained_backup_assets"] == 1
    assert db.get(Asset, cover.id) and (tmp / "media" / content_key(cover.sha256)).exists()


def test_permissions_csrf_changed_scope_and_explicit_reimport(library):
    client, db, _ = library
    row, _ = video(db)
    db.commit()
    path = f"/api/v1/admin/videos/{row.id}"
    assert client.get(path + "/deletion-preview").status_code == 401
    for role in ("reader", "editor"):
        login(client, role)
        assert client.get(path + "/deletion-preview").status_code == 403
        assert client.post(path + "/deletion", json={"confirm": True, "preview_token": "a" * 64}).status_code == 403
        client.post("/api/v1/auth/logout")
    login(client)
    summary = client.get(path + "/deletion-preview").json()
    body = {"confirm": True, "preview_token": summary["preview_token"]}
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post(path + "/deletion", json=body).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    collection = Collection(source_id="new-linked-source")
    db.add(collection); db.flush()
    db.add(CollectionItem(collection_id=collection.id, source_resource_id="2:1", video_id=row.id))
    db.commit()
    assert client.post(path + "/deletion", json=body).status_code == 409
    deleting, _ = request(client, "videos", row.id)
    assert client.post(f"/api/v1/admin/deletions/{deleting}/allow-reimport").status_code == 409
    finish(db, deleting)
    assert deletion.is_suppressed(db, bvid=row.bvid)
    assert client.post(f"/api/v1/admin/deletions/{deleting}/allow-reimport").status_code == 200
    assert not deletion.is_suppressed(db, bvid=row.bvid)
    assert db.get(Collection, collection.id).enabled is False


def test_deleted_bvid_cannot_run_again_or_recreate_through_source(library, monkeypatch):
    from app.ingest import runner
    from app.ingest.errors import IngestError
    client, db, _ = library
    login(client)
    row, _ = video(db)
    db.commit()
    deleting, _ = request(client, "videos", row.id)
    assert finish(db, deleting).status == "succeeded"
    new = jobs.enqueue(db, "archive_video", row.bvid)
    db.commit()
    monkeypatch.setattr(runner, "BiliClient", lambda *args, **kwargs: pytest.fail("must never call upstream"))
    assert jobs.run_job_id(new.id)["status"] == "failed"
    db.expire_all()
    assert db.get(Video, row.id) is None
    assert db.get(Job, new.id).error == "内容已由管理员删除；需先明确允许重新导入"


def test_scope_is_not_read_again_after_confirmation_token_check(library, monkeypatch):
    client, db, _ = library
    login(client)
    owned, _ = video(db)
    person = PlatformUser(uid="100", display_name="creator")
    db.add(person); db.flush()
    creator = Creator(user_id=person.id)
    db.add(creator); db.flush()
    db.add(VideoCreator(video_id=owned.id, creator_id=creator.id, role="owner")); db.commit()
    summary = client.get(f"/api/v1/admin/creators/{creator.id}/deletion-preview").json()
    original = deletion.preview
    added = []
    def preview_then_new_owner(db, kind, identifier, **kwargs):
        result = original(db, kind, identifier, **kwargs)
        new, _ = video(db, "BV1234567891")
        db.add(VideoCreator(video_id=new.id, creator_id=creator.id, role="owner"))
        db.flush()
        added.append(new.id)
        return result
    monkeypatch.setattr(deletion, "preview", preview_then_new_owner)
    response = client.post(f"/api/v1/admin/creators/{creator.id}/deletion", json={"confirm": True, "preview_token": summary["preview_token"]})
    assert response.status_code == 202
    job = db.get(Job, response.json()["job_id"])
    assert job.checkpoint["video_ids"] == [owned.id]
    assert added[0] not in job.checkpoint["video_ids"]


def test_gc_commits_catalog_and_checkpoint_before_releasing_backup_guard(library, monkeypatch):
    from contextlib import contextmanager
    from app import backup
    client, db, tmp = library
    login(client)
    row, _ = video(db)
    cover = asset(db, tmp, "guarded-cover")
    row.cover_asset_id = cover.id
    asset_id = cover.id
    db.commit()
    deleting, _ = request(client, "videos", row.id)
    original = backup.asset_gc_guard
    releases = []
    @contextmanager
    def checked_guard(session):
        with original(session):
            yield
            with Session(session.get_bind()) as observer:
                assert observer.get(Asset, asset_id) is None
                checkpoint = observer.get(Job, deleting).checkpoint
                assert checkpoint["asset_results"][asset_id] == "deleted"
                releases.append(True)
    monkeypatch.setattr(backup, "asset_gc_guard", checked_guard)
    assert finish(db, deleting).status == "succeeded"
    assert releases == [True]


def test_source_observation_does_not_recreate_deleted_video(library):
    from types import SimpleNamespace
    from app.source_monitoring import _observe, COUNTS
    client, db, _ = library
    login(client)
    row, _ = video(db)
    bvid = row.bvid
    db.commit()
    deleting, _ = request(client, "videos", row.id)
    assert finish(db, deleting).status == "succeeded"
    source = Collection(kind="favorite", source_id="new-source")
    run = CaptureRun()
    db.add_all([source, run]); db.flush()
    ctx = SimpleNamespace(db=db, run=run, policy={})
    scan = {"counts": {key: 0 for key in COUNTS}, "initial_strategy": "all", "mode": "initial",
            "baseline_started_at": utcnow().isoformat()}
    _observe(ctx, source, scan, {"bvid": bvid, "title": "deleted", "fav_time": 1}, "2:1", 0, utcnow(),
        lambda *args: pytest.fail("must not enqueue deleted video"))
    db.flush()
    assert db.scalar(select(Video.id).where(Video.bvid == bvid)) is None
    item = db.scalar(select(CollectionItem).where(CollectionItem.collection_id == source.id))
    assert item.video_id is None and item.observation["archive_decision"] == "deleted_locally"
    db.commit()
    assert client.post(f"/api/v1/admin/deletions/{deleting}/allow-reimport").status_code == 200
    run = CaptureRun()
    db.add(run); db.flush()
    ctx.run = run
    queued = []
    def enqueue_again(ctx, restored):
        queued.append(restored.id)
        return "queued"
    _observe(ctx, source, scan, {"bvid": bvid, "title": "restored", "fav_time": 1}, "2:1", 0, utcnow(), enqueue_again)
    db.flush()
    assert queued == [item.video_id]
    assert item.observation["archive_decision"] == "queued"
