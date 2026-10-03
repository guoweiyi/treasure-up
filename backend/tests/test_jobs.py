from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

from app import jobs
from app.models import Asset, AssetLocation, Base, Job, JobAttempt, OutboxEvent, Setting, StorageProfile, utcnow


@pytest.fixture
def job_sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    # Tests finish within a heartbeat interval; no timing-dependent background I/O.
    monkeypatch.setattr(jobs, "heartbeat", lambda job_id, owner, stop: stop.wait())
    yield factory
    engine.dispose()


def new_job(factory, *, kind="archive_video", policy=None, max_attempts=3):
    with factory() as db:
        job = jobs.enqueue(db, kind, "target", policy=policy)
        job.max_attempts = max_attempts
        db.commit()
        return job.id


def test_ingest_policy_precedence_and_frozen_children(job_sessions):
    with job_sessions() as db:
        system = Setting(key="ingest", value={"quality": "720p", "request_budget": 20, "download_media": False})
        db.add(system)
        db.commit()
        parent = jobs.enqueue(db, "scan_collection", "collection", policy={"quality": "1080p"})
        db.commit()
        assert parent.policy["quality"] == "1080p"
        assert parent.policy["request_budget"] == 20
        assert parent.policy["download_media"] is False
        assert parent.policy["fetch_danmaku"] is True  # Schema default.
        system.value = {"quality": "best", "request_budget": 1000, "download_media": True}
        db.commit()
        child = jobs.enqueue(db, "archive_video", "video", policy=parent.policy, frozen_policy=True)
        later = jobs.enqueue(db, "archive_video", "later")
        backup = jobs.enqueue(db, "backup", "library", policy={"test": "backup-only"})
        migration = jobs.enqueue(db, "migrate_storage", "source", policy={"target_profile_id": "target"})
        db.commit()
        assert child.policy == parent.policy
        assert later.policy["request_budget"] == 1000
        assert backup.policy == {"test": "backup-only"}
        assert migration.policy == {"target_profile_id": "target"}


def reclaim(factory, job_id, owner="replacement"):
    with factory() as db:
        db.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
        jobs.recover_and_dispatch(db, lambda *_: None)
        claimed = jobs.claim(db, job_id, owner)
        assert claimed is not None
        jobs.save_checkpoint(db, job_id, owner, {"replacement": True})


def test_atomic_claim_has_one_winner(job_sessions):
    job_id = new_job(job_sessions)
    barrier = threading.Barrier(2)

    def compete(owner):
        with job_sessions() as db:
            barrier.wait()
            claimed = jobs.claim(db, job_id, owner)
            return claimed.lease_owner if claimed else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ["first", "second"]))
    assert len([value for value in results if value]) == 1
    with job_sessions() as db:
        job = db.get(Job, job_id)
        assert job.status == "running" and job.attempts == 1


def test_expired_heartbeat_cannot_resurrect_lease(job_sessions):
    job_id = new_job(job_sessions)
    with job_sessions() as db:
        jobs.claim(db, job_id, "old")
        db.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
        assert not jobs.renew_lease(db, job_id, "old")
        jobs.recover_and_dispatch(db, lambda *_: None)
        assert jobs.claim(db, job_id, "new")
        assert not jobs.renew_lease(db, job_id, "old")
        assert jobs.renew_lease(db, job_id, "new")


def test_reclaimed_worker_cannot_commit_content_or_finish_new_owner(job_sessions, monkeypatch):
    job_id = new_job(job_sessions)

    def stale_work(db, job):
        reclaim(job_sessions, job.id)
        db.add(Asset(sha256="a" * 64, size=4, kind="video", mime_type="video/mp4"))
        db.commit()  # Must fail before publishing the pending asset.
        return {"should_not_happen": True}

    monkeypatch.setattr("app.ingest.runner.run_job", stale_work)
    result = jobs.run_job_id(job_id)
    assert result["status"] == "lease_lost"
    with job_sessions() as db:
        current = db.get(Job, job_id)
        assert current.lease_owner == "replacement" and current.status == "running"
        assert current.checkpoint == {"replacement": True}
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(JobAttempt.status)) == "lease_lost"


def test_finalization_is_fenced_even_when_worker_returns_without_writes(job_sessions, monkeypatch):
    job_id = new_job(job_sessions)

    def stale_return(db, job):
        reclaim(job_sessions, job.id)
        return {"complete": True}

    monkeypatch.setattr("app.ingest.runner.run_job", stale_return)
    assert jobs.run_job_id(job_id)["status"] == "lease_lost"
    with job_sessions() as db:
        assert db.get(Job, job_id).lease_owner == "replacement"
        assert db.get(Job, job_id).result == {}


def test_redis_loss_republishes_queued_work_and_duplicate_delivery_is_harmless(job_sessions):
    job_id = new_job(job_sessions)
    sent = []
    with job_sessions() as db:
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        assert len(sent) == 1
        # The published Redis message was lost; the DB record remains queued.
        db.execute(update(OutboxEvent).values(published_at=utcnow() - timedelta(seconds=61)))
        db.commit()
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        assert len(sent) == 2 and sent[0] == sent[1]
        assert jobs.claim(db, job_id, "winner")
    with job_sessions() as duplicate:
        assert jobs.claim(duplicate, job_id, "duplicate") is None


def test_failed_broker_send_does_not_mark_outbox_published(job_sessions):
    new_job(job_sessions)
    with job_sessions() as db:
        def offline(*_):
            raise ConnectionError("simulated broker unavailable")
        with pytest.raises(ConnectionError):
            jobs.recover_and_dispatch(db, offline)
        db.rollback()
        assert db.scalar(select(OutboxEvent.published_at)) is None
        sent = []
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        assert len(sent) == 1


def test_recently_dispatched_first_page_does_not_starve_later_jobs(job_sessions):
    with job_sessions() as db:
        for index in range(101):
            jobs.enqueue(db, "archive_video", f"target-{index}")
        db.commit()
        sent = []
        jobs.recover_and_dispatch(db, lambda job_id, _: sent.append(job_id))
        assert len(sent) == 100
        jobs.recover_and_dispatch(db, lambda job_id, _: sent.append(job_id))
        assert len(sent) == 101 and len(set(sent)) == 101


def test_successful_budget_continuations_preserve_failure_retry_budget(job_sessions, monkeypatch):
    job_id = new_job(job_sessions, max_attempts=2)

    def budget_slice(db, job):
        checkpoint = jobs.ensure_active(db, job.id, job.lease_owner)
        jobs.save_checkpoint(db, job.id, job.lease_owner, {"page": checkpoint.get("page", 0) + 1})
        return {"continuation": True}

    monkeypatch.setattr("app.ingest.runner.run_job", budget_slice)
    for _ in range(5):
        assert jobs.run_job_id(job_id)["status"] == "queued"
        with job_sessions() as db:
            job = db.get(Job, job_id)
            assert job.attempts == 0
            job.available_at = utcnow() - timedelta(seconds=1)
            db.commit()
    with job_sessions() as db:
        assert db.get(Job, job_id).checkpoint == {"page": 5}
        assert db.scalar(select(func.count()).select_from(JobAttempt)) == 5

    class TransientError(Exception):
        retryable = True

    def transient_failure(db, job):
        raise TransientError("simulated temporary error")

    monkeypatch.setattr("app.ingest.runner.run_job", transient_failure)
    assert jobs.run_job_id(job_id)["status"] == "queued"
    with job_sessions() as db:
        assert db.get(Job, job_id).attempts == 1


def test_account_cooldown_deferrals_honor_deadline_without_using_failure_budget(job_sessions, monkeypatch):
    from app.ingest.errors import IngestDeferred, IngestError
    job_id = new_job(job_sessions, max_attempts=2)
    def real_rejection(db, job):
        raise IngestError("源站限流", code="rate_limited", blocked=False, retry_after_seconds=1800)
    monkeypatch.setattr("app.ingest.runner.run_job", real_rejection)
    assert jobs.run_job_id(job_id)["status"] == "queued"
    with job_sessions() as db:
        job = db.get(Job, job_id)
        assert job.attempts == 1
        job.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    def waiting_for_account(db, job):
        raise IngestDeferred("账号冷却中", retry_after_seconds=3600)
    monkeypatch.setattr("app.ingest.runner.run_job", waiting_for_account)
    for _ in range(4):
        assert jobs.run_job_id(job_id)["status"] == "queued"
        with job_sessions() as db:
            job = db.get(Job, job_id)
            assert job.attempts == 1  # Preserve the actual earlier rejection.
            deadline = job.available_at.replace(tzinfo=utcnow().tzinfo)
            assert (deadline - utcnow()).total_seconds() >= 3598
            assert jobs.claim(db, job_id, "premature") is None
            job.available_at = utcnow() - timedelta(seconds=1)
            db.commit()
    monkeypatch.setattr("app.ingest.runner.run_job", lambda db, job: {"stats_updated": True})
    assert jobs.run_job_id(job_id)["status"] == "succeeded"


@pytest.mark.parametrize("requested", ["paused", "cancelled"])
def test_pause_and_cancel_cannot_be_overwritten_by_worker_success(job_sessions, monkeypatch, requested):
    job_id = new_job(job_sessions)

    def cancelled_work(db, job):
        with job_sessions() as controller:
            controller.execute(update(Job).where(Job.id == job.id).values(status=requested))
            controller.commit()
        db.add(Asset(sha256="b" * 64, size=9, kind="video", mime_type="video/mp4"))
        db.commit()
        return {"succeeded": True}

    monkeypatch.setattr("app.ingest.runner.run_job", cancelled_work)
    assert jobs.run_job_id(job_id)["status"] == requested
    with job_sessions() as db:
        job = db.get(Job, job_id)
        assert job.status == requested and job.lease_owner is None
        assert db.scalar(select(func.count()).select_from(Asset)) == 0


def test_backup_receives_cancellation_checkpoint(job_sessions, monkeypatch):
    job_id = new_job(job_sessions, kind="backup")

    def interrupted_backup(db, *, check_active):
        check_active()
        with job_sessions() as controller:
            controller.execute(update(Job).where(Job.id == job_id).values(status="cancelled"))
            controller.commit()
        check_active()
        pytest.fail("Cancelled backup should stop at its checkpoint")

    monkeypatch.setattr("app.backup.create_backup", interrupted_backup)
    assert jobs.run_job_id(job_id)["status"] == "cancelled"


def migration_job(factory):
    with factory() as db:
        source = StorageProfile(name="source", kind="local", enabled=True, is_default=True, config={})
        target = StorageProfile(name="target", kind="s3", enabled=True, is_default=False, config={})
        asset = Asset(sha256="c" * 64, size=1024, kind="video", mime_type="video/mp4")
        db.add_all([source, target, asset])
        db.flush()
        db.add(AssetLocation(asset_id=asset.id, storage_profile_id=source.id, object_key="assets/test", state="ready"))
        job = jobs.enqueue(db, "migrate_storage", source.id, policy={"target_profile_id": target.id})
        db.commit()
        return job.id, asset.id


def test_migration_resumes_persisted_multipart_and_clears_completed_upload(job_sessions, monkeypatch):
    job_id, asset_id = migration_job(job_sessions)
    calls = []

    def interrupted_upload(db, asset_id, profile_id, *, checkpoint, on_checkpoint):
        calls.append(checkpoint)
        if checkpoint is None:
            on_checkpoint({"upload_id": "resumable", "completed_parts": 2})
            raise OSError("simulated disconnect")
        assert checkpoint["upload_id"] == "resumable"
        on_checkpoint({**checkpoint, "completed_parts": 3})

    monkeypatch.setattr("app.storage.service.migrate_asset", interrupted_upload)
    assert jobs.run_job_id(job_id)["status"] == "failed"
    with job_sessions() as db:
        job = db.get(Job, job_id)
        assert job.checkpoint["uploads"][asset_id]["completed_parts"] == 2
        job.status, job.available_at = "queued", utcnow()
        db.commit()
    assert jobs.run_job_id(job_id)["status"] == "succeeded"
    with job_sessions() as db:
        checkpoint = db.get(Job, job_id).checkpoint
        assert checkpoint["completed"] == [asset_id] and checkpoint["uploads"] == {}
        assert checkpoint["asset_ids"] == [asset_id]


def test_old_migration_worker_cannot_replace_new_owner_checkpoint(job_sessions, monkeypatch):
    job_id, _ = migration_job(job_sessions)

    def lost_upload(db, asset_id, profile_id, *, checkpoint, on_checkpoint):
        reclaim(job_sessions, job_id)
        on_checkpoint({"upload_id": "stale-upload"})

    monkeypatch.setattr("app.storage.service.migrate_asset", lost_upload)
    assert jobs.run_job_id(job_id)["status"] == "lease_lost"
    with job_sessions() as db:
        assert db.get(Job, job_id).checkpoint == {"replacement": True}


def test_explicit_empty_migration_scope_does_no_work(job_sessions, monkeypatch):
    job_id, _ = migration_job(job_sessions)
    with job_sessions() as db:
        job = db.get(Job, job_id)
        job.policy = {**job.policy, "asset_ids": []}
        db.commit()
    monkeypatch.setattr("app.storage.service.migrate_asset", lambda *_args, **_kwargs: pytest.fail("Empty scope expanded to all assets"))
    result = jobs.run_job_id(job_id)
    assert result["status"] == "succeeded" and result["result"]["total"] == 0


def test_storage_probe_is_a_media_worker_job_with_real_result(job_sessions, tmp_path, monkeypatch):
    from app.config import settings
    from app.scheduler import queue_for_kind
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    root = tmp_path / "worker-media"
    with job_sessions() as db:
        profile = StorageProfile(name="test-worker-probe", kind="local", config={"root": str(root)}, enabled=True)
        db.add(profile)
        db.flush()
        item = jobs.enqueue(db, "probe_storage", profile.id)
        db.commit()
        job_id = item.id
    assert queue_for_kind("probe_storage") == "media"
    result = jobs.run_job_id(job_id)
    assert result["status"] == "succeeded"
    assert result["result"]["checks"]["sha256_readback"] is True
    assert result["result"]["checks"]["single_range"] is True
    assert result["result"]["multipart"] == "not_tested"
    assert not list((root / "probes").iterdir())
    with job_sessions() as db:
        assert db.get(Job, job_id).result["status"] == "passed"
def test_media_and_backup_jobs_use_dedicated_queues():
    from app.scheduler import queue_for_kind
    assert queue_for_kind("archive_video") == "media"
    assert queue_for_kind("migrate_storage") == "media"
    assert queue_for_kind("probe_storage") == "media"
    assert queue_for_kind("backup") == "backup"
    assert queue_for_kind("scan_collection") == "collector"
    assert queue_for_kind("refresh_comments") == "collector"
