"""Real PostgreSQL pacing/statistics/heartbeat interaction in random test databases."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app import jobs
from app.ingest import runner
from app.ingest.throttle import AccountPacer
from app.models import Job, SourceAccount, Video, VideoPart, VideoStatSnapshot, utcnow
from test_postgres_integration import postgres_workspace  # noqa: F401; isolated test DB fixture


pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit isolated PostgreSQL test opt-in required")


def test_stats_resume_and_pacing_do_not_lock_out_heartbeat(postgres_workspace, monkeypatch):
    space = postgres_workspace
    with space.sessions() as db:
        account = SourceAccount(name="mock", secret_encrypted="fixture-not-a-credential")
        video = Video(bvid="BV1234567890", aid="123", capture_status="complete")
        db.add_all([account, video]); db.flush()
        db.add(VideoPart(video_id=video.id, cid="11", position=1, duration=361))
        job = jobs.enqueue(db, "refresh_stats", video.id, account.id, policy={"refresh_danmaku": True,
            "request_budget": 1, "request_interval_seconds": 3, "interval_jitter_seconds": 0})
        db.commit()
        job_id = job.id
    heartbeat_checks, views, segments = [], [], []

    def pulse():
        with space.sessions() as heart:
            heart.execute(text("SET LOCAL lock_timeout = '500ms'"))
            owner = heart.scalar(select(Job.lease_owner).where(Job.id == job_id))
            assert jobs.renew_lease(heart, job_id, owner)
            heartbeat_checks.append(True)

    virtual_clock = [utcnow()]
    def sleep(seconds):
        pulse()  # In the middle of pacing, while the runner session is open.
        virtual_clock[0] += timedelta(seconds=seconds)
    def pacer(db, account, policy, guard):
        return AccountPacer(db, account, policy, guard, clock=lambda: virtual_clock[0], sleep=sleep, jitter=lambda _: 0)
    monkeypatch.setattr(runner, "AccountPacer", pacer)
    monkeypatch.setattr(runner, "decrypt_secret", lambda _: "SESSDATA=mock")
    save = runner._save_stats
    def save_with_heartbeat(*args):
        result = save(*args)
        pulse()  # The new statistics row has flushed, but is not committed yet.
        return result
    monkeypatch.setattr(runner, "_save_stats", save_with_heartbeat)

    class Source:
        def __init__(self, *args, **kwargs): pass
        def close(self): pass
        def nav(self):
            self.before_request("https://api.bilibili.com/x/web-interface/nav")
            pulse()
            return {"isLogin": True, "mid": "1"}
        def view(self, bvid):
            self.before_request("https://api.bilibili.com/x/web-interface/view")
            views.append(bvid)
            # Fragment callbacks use an independent read session, not the
            # runner's session from a foreign thread.
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(self.before_request.__self__.guard).result(timeout=2)
            pulse()
            return {"bvid": bvid, "aid": "123", "stat": {"view": 9}, "pages": [{"cid": "11"}]}
        def segment(self, aid, cid, index):
            self.before_request("https://api.bilibili.com/x/v2/dm/wbi/web/seg.so")
            segments.append(index)
            pulse()
            return b""
    monkeypatch.setattr(runner, "BiliClient", Source)
    assert jobs.run_job_id(job_id)["status"] == "queued"
    with space.sessions() as db:
        job = db.get(Job, job_id)
        job.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert jobs.run_job_id(job_id)["status"] == "succeeded"
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(VideoStatSnapshot)) == 1
        assert db.get(Video, video.id).capture_status == "complete"
    assert len(views) == 1 and segments == [1, 2] and len(heartbeat_checks) >= 6


def test_media_transfer_releases_account_for_a_second_worker(postgres_workspace, monkeypatch):
    """Real advisory locks: metadata may finish while a download is in flight."""
    from app.ingest.errors import IngestDeferred
    space = postgres_workspace
    monkeypatch.setattr(runner, "decrypt_secret", lambda _: "SESSDATA=fixture")
    with space.sessions() as db:
        account = SourceAccount(name="mock", secret_encrypted="not-a-credential")
        video = Video(bvid="BV1234567890", aid="1", capture_status="metadata_ready")
        db.add_all([account, video]); db.flush()
        part = VideoPart(video_id=video.id, cid="1", duration=10)
        db.add(part); db.flush()
        download = jobs.enqueue(db, "download_media", video.id, account.id,
            policy={"create_compatible_copy": False, "request_interval_seconds": 1, "interval_jitter_seconds": 0})
        download.checkpoint = {"part_ids": [part.id]}
        verify = jobs.enqueue(db, "verify_account", account.id, account.id,
            policy={"request_interval_seconds": 1, "interval_jitter_seconds": 0})
        db.commit()
        download_id, verify_id = download.id, verify.id

    class Source:
        def __init__(self, *args, **kwargs): pass
        def close(self): pass
        def nav(self):
            with self.request_context("https://api.bilibili.com/x/web-interface/nav"):
                self.before_request("https://api.bilibili.com/x/web-interface/nav")
            return {"isLogin": True, "mid": "1"}
    monkeypatch.setattr(runner, "BiliClient", Source)
    observed = []
    def transfer(*_args, **_kwargs):
        # This runs inside the download worker's video and download-account
        # locks, on a different Session from the verification worker.
        with ThreadPoolExecutor(max_workers=1) as pool:
            observed.append(pool.submit(jobs.run_job_id, verify_id).result(timeout=10)["status"])
        raise IngestDeferred("End mocked transfer", retry_after_seconds=10)
    monkeypatch.setattr(runner, "archive_media", transfer)
    assert jobs.run_job_id(download_id)["status"] == "queued"
    assert observed == ["succeeded"]
    with space.sessions() as db:
        assert db.get(Job, download_id).attempts == 0
        assert db.get(Job, download_id).checkpoint["part_ids"] == [part.id]


def test_http_does_not_hold_credential_mutex_or_wait_for_legacy_interval(postgres_workspace):
    import hashlib
    from types import SimpleNamespace
    space = postgres_workspace
    with space.sessions() as db:
        clock = [utcnow()]
        account = SourceAccount(name="fixture", secret_encrypted="fixture",
                                next_request_at=clock[0] + timedelta(seconds=2))
        db.add(account); db.commit()
        key = int.from_bytes(hashlib.sha256(("account:" + account.id).encode()).digest()[:8], "big", signed=True)
        def probe():
            with space.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as other:
                acquired = other.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
                if acquired:
                    other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                return acquired
        waits = []
        def sleep(seconds):
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(probe).result(timeout=5)
            waits.append(seconds)
            clock[0] += timedelta(seconds=seconds)
        pacer = AccountPacer(db, account, {"request_interval_seconds": 3}, lambda: None,
            clock=lambda: clock[0], sleep=sleep, jitter=lambda _: 0)
        client = SimpleNamespace(cookies=[])
        with runner.account_request_scope(db, account, client, pacer,
                "https://api.bilibili.com/x/web-interface/nav", decryptor=lambda _: "SESSDATA=fixture"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(probe).result(timeout=5)
            pacer.before_request("https://api.bilibili.com/x/web-interface/nav")
        assert not waits and probe()


def test_rejection_persists_cooldown_without_committing_capture_or_reacquiring_http_lock(postgres_workspace):
    import hashlib
    from sqlalchemy import event
    from app.ingest.errors import IngestError
    from app.models import CaptureRun
    space = postgres_workspace
    with space.sessions() as db:
        account = SourceAccount(name="fixture", secret_encrypted="fixture")
        db.add(account); db.commit()
        run = CaptureRun(scope={"test": "must remain uncommitted"}, status="running")
        db.add(run); db.flush()
        run_id = run.id
        def forbid_caller_commit(_session):
            pytest.fail("risk backoff must not enter the caller's capture/job fence transaction")
        event.listen(db, "before_commit", forbid_caller_commit)
        key = int.from_bytes(hashlib.sha256(("account:" + account.id).encode()).digest()[:8], "big", signed=True)
        try:
            with space.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as other:
                assert other.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
                try:
                    rejection = IngestError("upstream rejected", code="rate_limited", retry_after_seconds=1800)
                    AccountPacer(db, account, {}, lambda: None, jitter=lambda _: 0).failed(rejection)
                finally:
                    other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            with space.sessions() as observer:
                saved = observer.get(SourceAccount, account.id)
                assert saved.risk_failures == 1 and saved.cooldown_until > utcnow() + timedelta(seconds=1790)
                assert observer.get(CaptureRun, run_id) is None
            assert not rejection.blocked and rejection.retry_after_seconds >= 1790
        finally:
            event.remove(db, "before_commit", forbid_caller_commit)
            db.rollback()


def test_ordinary_api_requests_can_overlap_after_short_credential_snapshots(postgres_workspace):
    import threading
    import httpx
    from app.ingest.client import BiliClient
    space = postgres_workspace
    with space.sessions() as db:
        account = SourceAccount(name="fixture", secret_encrypted="fixture", status="valid",
            next_request_at=utcnow() + timedelta(hours=1))
        db.add(account); db.commit()
        account_id = account.id
    arrived = threading.Barrier(2)
    def request():
        with space.sessions() as db:
            account = db.get(SourceAccount, account_id)
            def transport(incoming):
                assert incoming.headers["cookie"] == "SESSDATA=fixture"
                # Both real HTTP scopes must enter before either can finish.
                arrived.wait(timeout=5)
                return httpx.Response(200, json={"code": 0, "data": {"isLogin": True, "mid": "123"}})
            client = BiliClient("SESSDATA=fixture", transport=httpx.MockTransport(transport))
            pacer = AccountPacer(db, account, {"request_interval_seconds": 120}, lambda: None,
                sleep=lambda _: pytest.fail("ordinary APIs must not wait on the legacy request interval"))
            client.before_request = pacer.before_request
            client.request_context = lambda url: runner.account_request_scope(db, account, client, pacer, url,
                decryptor=lambda _: "SESSDATA=fixture")
            try:
                return client.nav()["mid"]
            finally:
                client.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        tasks = [workers.submit(request) for _ in range(2)]
        assert [task.result(timeout=10) for task in tasks] == ["123", "123"]


@pytest.mark.parametrize("old_result", ["success", "expired"])
def test_cookie_rotation_during_http_cannot_overwrite_new_account_in_postgres(postgres_workspace, monkeypatch, old_result):
    import httpx
    from app.ingest.client import BiliClient
    space = postgres_workspace
    with space.sessions() as db:
        account = SourceAccount(name="old", secret_encrypted="old-cipher", status="valid", uid="123")
        db.add(account); db.flush()
        job = jobs.enqueue(db, "verify_account", account.id, account.id)
        db.commit()
        account_id, job_id = account.id, job.id
    def rotate():
        with space.sessions() as db:
            with runner.credential_lock(db, account_id):
                account = db.get(SourceAccount, account_id)
                account.secret_encrypted, account.uid, account.status = "new-cipher", "999", "valid"
                db.commit()
    def transport(incoming):
        assert incoming.headers["cookie"] == "SESSDATA=old"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(rotate).result(timeout=5)
        return httpx.Response(200, json={"code": 0 if old_result == "success" else -101,
            "data": {"isLogin": True, "mid": "123"} if old_result == "success" else None})
    monkeypatch.setattr(runner, "decrypt_secret", lambda _: "SESSDATA=old")
    monkeypatch.setattr(runner, "BiliClient", lambda secret, **kwargs: BiliClient(secret,
        transport=httpx.MockTransport(transport), **kwargs))
    assert jobs.run_job_id(job_id)["status"] == "queued"
    with space.sessions() as db:
        account, job = db.get(SourceAccount, account_id), db.get(Job, job_id)
        assert (account.uid, account.status, account.risk_failures) == ("999", "valid", 0)
        assert job.attempts == 0
