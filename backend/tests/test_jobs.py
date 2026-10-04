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


def test_new_continuation_event_dispatches_when_due_without_sixty_second_delay(job_sessions, monkeypatch):
    job_id = new_job(job_sessions)
    clock = [utcnow() + timedelta(seconds=1)]
    monkeypatch.setattr(jobs, "utcnow", lambda: clock[0])
    monkeypatch.setattr("app.ingest.runner.run_job", lambda db, job: {"continuation": True})
    sent = []
    with job_sessions() as db:
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
    assert sent == [(job_id, "archive_video")]
    assert jobs.run_job_id(job_id)["status"] == "queued"
    with job_sessions() as db:
        assert db.scalar(select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.job_id == job_id, OutboxEvent.published_at.is_(None))) == 1
        # The continuation is real, but its two-second availability gate still applies.
        clock[0] += timedelta(seconds=1)
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        assert len(sent) == 1
        clock[0] += timedelta(seconds=1)
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        assert sent == [(job_id, "archive_video"), (job_id, "archive_video")]
        assert db.scalar(select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.job_id == job_id, OutboxEvent.published_at.is_(None))) == 0
        # Once the new event was published, a queued job is not sent again immediately.
        clock[0] += timedelta(seconds=1)
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        assert len(sent) == 2


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


def test_component_failure_is_not_erased_by_comment_budget_continuation(job_sessions, monkeypatch):
    from app.ingest import runner
    from app.ingest.errors import IngestError
    from app.models import Comment, SourceAccount, Video, VideoPart
    from test_ingest_runner import FakeClient, comment

    comments, subtitles = [], []
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            comments.append(offset)
            return {"cursor": {"is_end": False, "pagination_reply": {"next_offset": f"cursor-{len(comments)}"}},
                    "replies": [comment(str(len(comments)), member=False)]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    monkeypatch.setattr(runner, "decrypt_secret", lambda _: "mock-cookie")
    def fail_subtitles(*args, **kwargs):
        subtitles.append(True)
        raise IngestError("模拟持续字幕失败", code="invalid_subtitle")
    monkeypatch.setattr(runner, "_subtitles", fail_subtitles)
    with job_sessions() as db:
        account = SourceAccount(name="mock", secret_encrypted="mock")
        video = Video(bvid="BV1234567890", aid="123")
        db.add_all([account, video]); db.flush()
        part = VideoPart(video_id=video.id, cid="1", duration=10)
        db.add(part); db.flush()
        job = jobs.enqueue(db, "archive_video", video.id, account.id, policy={"request_budget": 2,
            "fetch_danmaku": False, "fetch_subtitles": True, "create_compatible_copy": False, "images": False})
        job.max_attempts = 2
        job.checkpoint = {"metadata_done": True, "basics_done": True, "part_ids": [part.id]}
        db.commit()
        job_id = job.id
    for number, expected in [(1, "queued"), (2, "partial")]:
        assert jobs.run_job_id(job_id)["status"] == expected
        with job_sessions() as db:
            job = db.get(Job, job_id)
            assert job.attempts == number
            assert job.checkpoint["comments"]["offset"] == f"cursor-{number}"
            assert job.result["components"] == ["invalid_subtitle"]
            job.available_at = utcnow() - timedelta(seconds=1)
            db.commit()
    assert comments == ["", "cursor-1"] and len(subtitles) == 2
    with job_sessions() as db:
        # Selection waits for the bounded sample to finish. A failed sibling
        # component preserves candidates without archiving every scanned row.
        assert db.scalar(select(func.count()).select_from(Comment)) == 0
        assert len(db.get(Job, job_id).checkpoint["comments"]["candidates"]) == 2


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


def test_storage_probe_is_a_collector_job_with_real_result(job_sessions, tmp_path, monkeypatch):
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
    assert queue_for_kind("probe_storage") == "collector"
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
    assert queue_for_kind("archive_video") == "collector"
    assert queue_for_kind("download_media") == "download"
    assert queue_for_kind("create_playback") == "media"
    assert queue_for_kind("migrate_storage") == "media"
    assert queue_for_kind("probe_storage") == "collector"
    assert queue_for_kind("backup") == "backup"
    assert queue_for_kind("scan_collection") == "collector"
    assert queue_for_kind("refresh_comments") == "collector"


def test_download_progress_is_fenced_and_uses_a_separate_session(job_sessions):
    from app.ingest.runner import Context
    job_id = new_job(job_sessions, kind="download_media")
    with job_sessions() as db:
        job = jobs.claim(db, job_id, "owner")
        ctx = Context(db, job, None)
        ctx.save()
        progress = {"phase": "download", "downloaded_bytes": 512, "total_bytes": 1024}
        ctx.media_progress(progress)
        with job_sessions() as observer:
            row = observer.get(Job, job_id)
            assert row.result["progress"]["downloaded_bytes"] == 512
            assert row.checkpoint["run_id"] == ctx.run.id
            row.status = "cancelled"
            observer.commit()
        ctx.last_progress = 0
        with pytest.raises(jobs.LeaseLost):
            ctx.media_progress({**progress, "downloaded_bytes": 1024})
    with job_sessions() as db:
        row = db.get(Job, job_id)
        assert row.status == "cancelled" and row.result["progress"]["downloaded_bytes"] == 512


def test_existing_probe_is_republished_to_collector_without_resetting_stopped_jobs(job_sessions):
    from app.scheduler import queue_for_kind
    probe_id = new_job(job_sessions, kind="probe_storage")
    stopped_id = new_job(job_sessions, kind="probe_storage")
    with job_sessions() as db:
        db.get(Job, stopped_id).status = "cancelled"
        for outbox in db.scalars(select(OutboxEvent)):
            outbox.published_at = utcnow() - timedelta(seconds=90)
        db.commit()
        dispatched = []
        jobs.recover_and_dispatch(db, lambda identity, kind: dispatched.append((identity, queue_for_kind(kind))))
        assert dispatched == [(probe_id, "collector")]
        assert db.get(Job, probe_id).attempts == 0
        assert db.get(Job, stopped_id).status == "cancelled"
        assert jobs.claim(db, probe_id, "collector") is not None
    with job_sessions() as stale_media_worker:
        assert jobs.claim(stale_media_worker, probe_id, "old-media-message") is None
