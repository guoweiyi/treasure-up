"""Opt-in deletion fencing and asset-reference contention in random PG databases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import os
import threading

import pytest
from sqlalchemy import select

from app import jobs, library_deletion as deletion
from app.ingest import runner
from app.models import Asset, Job, User, Video, VideoPart, utcnow
from app.storage.service import ingest_file
from test_postgres_integration import postgres_workspace  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                               reason="Explicit isolated PostgreSQL test opt-in required")


def test_delete_cancels_producer_commit_and_waits_for_its_lease(postgres_workspace, monkeypatch):
    space = postgres_workspace
    with space.sessions() as db:
        actor = User(username="admin", role="admin", password_hash="fixture-not-used")
        video = Video(bvid="BV1234567890")
        db.add_all([actor, video]); db.flush()
        db.add(VideoPart(video_id=video.id, cid="1"))
        producer = jobs.enqueue(db, "archive_video", video.id)
        db.commit()
        actor_id, video_id, producer_id = actor.id, video.id, producer.id
    entered, release = threading.Event(), threading.Event()
    def late_producer(db, job):
        entered.set()
        assert release.wait(10)
        db.add(Asset(sha256="a" * 64, size=1, kind="late-write"))
        db.commit()  # Cancelled job cannot publish anything after deletion intent.
        return {}
    monkeypatch.setattr(runner, "run_job", late_producer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(jobs.run_job_id, producer_id)
        try:
            assert entered.wait(5)
            with space.sessions() as db:
                summary = deletion.preview(db, "video", video_id)
                assert type(summary["asset_bytes"]) is int
                requested = deletion.request_deletion(db, "video", video_id, summary["preview_token"], db.get(User, actor_id))
            delete_id = requested["job_id"]
            assert jobs.run_job_id(delete_id)["status"] == "queued"
            with space.sessions() as db:
                assert db.get(Video, video_id)
                assert db.get(Job, delete_id).attempts == 0
        finally:
            release.set()
        assert result.result(timeout=10)["status"] == "cancelled"
    with space.sessions() as db:
        assert db.scalar(select(Asset.id).where(Asset.kind == "late-write")) is None
        job = db.get(Job, delete_id)
        job.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert jobs.run_job_id(delete_id)["status"] == "succeeded"
    with space.sessions() as db:
        assert db.get(Video, video_id) is None


def test_gc_rechecks_reference_committed_while_waiting_for_asset_lock(postgres_workspace, monkeypatch):
    from app.storage import locking
    space = postgres_workspace
    source = space.root / "shared.bin"
    source.write_bytes(b"independent fixture")
    with space.sessions() as db:
        asset = ingest_file(db, source, kind="image", mime_type="image/png")
        db.commit()
        asset_id = asset.id
    content_entered = threading.Event()
    original = locking.content_lock
    @contextmanager
    def observed_lock(*args, **kwargs):
        with original(*args, **kwargs):
            content_entered.set()
            yield
    monkeypatch.setattr(locking, "content_lock", observed_lock)
    def collect():
        with space.sessions() as db:
            def publish(result):
                db.commit()
                return result
            return deletion._gc_asset(db, asset_id, lambda: None, publish)
    with space.sessions() as writer, ThreadPoolExecutor(max_workers=1) as pool:
        writer.add(Video(bvid="BV1234567890", cover_asset_id=asset_id))
        writer.flush()  # FK insertion holds KEY SHARE until commit.
        future = pool.submit(collect)
        try:
            assert content_entered.wait(5)
            assert not future.done()
            writer.commit()
        finally:
            writer.rollback()
        assert future.result(timeout=10) == "shared"
    with space.sessions() as db:
        assert db.get(Asset, asset_id)
