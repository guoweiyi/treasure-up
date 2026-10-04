"""Actual row/advisory-lock competition in disposable PostgreSQL databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import os
from queue import Queue
import secrets
import threading

import pytest
from sqlalchemy import func, select, text
from starlette.requests import Request

from app import jobs, userscript_api as integration
from app.ingest.errors import IngestDeferred
from app.models import IntegrationToken, Job, SourceAccount, User, utcnow
from app.schemas import IngestPolicy
from test_passkeys_postgres import _wait_for_postgres_lock_waiters, _worker_session
from test_postgres_integration import postgres_workspace  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1", reason="Isolated PostgreSQL opt-in required")


def seed(space, name):
    secret = "tu_ingest_" + secrets.token_urlsafe(32)
    with space.sessions() as db:
        user = User(username=name, role="admin", password_hash="fixture-not-used")
        account = SourceAccount(name=name, secret_encrypted="fixture-not-used", status="valid")
        db.add_all([user, account]); db.flush()
        token = IntegrationToken(user_id=user.id, account_id=account.id, name=name, scope=integration.SCOPE,
            token_hash=hashlib.sha256(secret.encode()).hexdigest(), policy=IngestPolicy().model_dump(),
            expires_at=utcnow() + timedelta(days=1))
        db.add(token); db.commit()
        return secret, token.id, user.id


def request(secret):
    return Request({"type": "http", "method": "POST", "path": "/api/v1/integrations/userscript/videos", "scheme": "https",
        "headers": [(b"authorization", ("Bearer " + secret).encode()), (b"content-type", b"application/json")],
        "client": ("198.51.100.10", 4000), "server": ("archive.example", 443), "query_string": b""})


def test_parallel_different_tokens_reuse_one_bvid_job(postgres_workspace, monkeypatch):
    space = postgres_workspace
    first, second = seed(space, "first"), seed(space, "second")
    ready = threading.Barrier(2)
    original = integration._batch_locks
    def race(db, bvids):
        ready.wait(timeout=10)
        original(db, bvids)
    monkeypatch.setattr(integration, "_batch_locks", race)
    def submit(secret):
        with space.sessions() as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            return integration.submit_videos(integration.VideoSubmission(bvids=["BV1234567890"]), request(secret), db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [first[0], second[0]]))
    assert sorted(row["items"][0]["status"] for row in results) == ["active", "queued"]
    assert len({row["items"][0]["job_id"] for row in results}) == 1
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.sum(IntegrationToken.day_videos))) == 1


def test_parallel_submissions_cannot_exceed_token_batch_quota(postgres_workspace, monkeypatch):
    space = postgres_workspace
    secret, token_id, user_id = seed(space, "quota")
    monkeypatch.setattr(integration, "MINUTE_REQUESTS", 2)
    ready, pids = threading.Barrier(5), Queue()
    def submit(db):
        return integration.submit_videos(integration.VideoSubmission(bvids=["BV1234567890"]), request(secret), db)
    with space.sessions() as blocker:
        blocker.scalar(select(User).where(User.id == user_id).with_for_update(key_share=True))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_worker_session, space, ready, pids, submit) for _ in range(4)]
            try:
                ready.wait(timeout=10)
                _wait_for_postgres_lock_waiters(space, {pids.get(timeout=1) for _ in range(4)})
            finally:
                blocker.rollback()
            results = [future.result(timeout=15) for future in futures]
    assert results.count(429) == 2 and sum(isinstance(value, dict) for value in results) == 2
    with space.sessions() as db:
        assert db.get(IntegrationToken, token_id).minute_requests == 2
        assert db.get(IntegrationToken, token_id).day_videos == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 1


@pytest.mark.parametrize("change", ["revoke", "disable"])
def test_revoke_or_disable_wins_before_waiting_submission(postgres_workspace, change):
    space = postgres_workspace
    secret, token_id, user_id = seed(space, "revocation")
    ready, pids = threading.Barrier(2), Queue()
    def submit(db):
        return integration.submit_videos(integration.VideoSubmission(bvids=["BV1234567890"]), request(secret), db)
    with space.sessions() as blocker:
        target = blocker.scalar(select(IntegrationToken).where(IntegrationToken.id == token_id).with_for_update()) if change == "revoke" else blocker.scalar(
            select(User).where(User.id == user_id).with_for_update(key_share=True))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_worker_session, space, ready, pids, submit)
            try:
                ready.wait(timeout=10)
                _wait_for_postgres_lock_waiters(space, {pids.get(timeout=1)})
                if change == "revoke": target.revoked_at = utcnow()
                else: target.disabled = True
                blocker.commit()
            finally:
                blocker.rollback()
            assert future.result(timeout=15) == 401
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_source_enqueue_and_script_batch_share_bvid_lock(postgres_workspace):
    space = postgres_workspace
    secret, _, _ = seed(space, "shared-enqueue")
    ready, pids = threading.Barrier(2), Queue()
    def submit(db):
        return integration.submit_videos(integration.VideoSubmission(bvids=["BV1234567890"]), request(secret), db)
    with space.sessions() as source:
        queued = jobs.enqueue(source, "archive_video", "BV1234567890")
        job_id = queued.id
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_worker_session, space, ready, pids, submit)
            try:
                ready.wait(timeout=10)
                _wait_for_postgres_lock_waiters(space, {pids.get(timeout=1)})
                source.commit()
            finally:
                source.rollback()
            result = future.result(timeout=15)
    assert result["items"][0]["status"] == "active"
    assert result["items"][0]["job_id"] == job_id
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.sum(IntegrationToken.day_videos))) == 0


def test_source_reverse_order_defers_instead_of_deadlocking_script_batch(postgres_workspace, monkeypatch):
    space = postgres_workspace
    secret, _, _ = seed(space, "reverse-order")
    first, second = "BV1234567890", "BV1234567891"
    ready, pids = threading.Barrier(2), Queue()
    first_locked = threading.Event()
    original = integration._batch_locks
    def hold_first(db, bvids):
        original(db, [first])
        first_locked.set()
        original(db, bvids)
    monkeypatch.setattr(integration, "_batch_locks", hold_first)
    def submit(db):
        return integration.submit_videos(integration.VideoSubmission(bvids=[first, second]), request(secret), db)
    with space.sessions() as source:
        jobs.enqueue(source, "archive_video", second)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_worker_session, space, ready, pids, submit)
            try:
                ready.wait(timeout=10)
                assert first_locked.wait(5)
                _wait_for_postgres_lock_waiters(space, {pids.get(timeout=1)})
                with pytest.raises(IngestDeferred) as error:
                    jobs.enqueue(source, "archive_video", first)
                assert error.value.code == "enqueue_busy"
            finally:
                source.rollback()
            result = future.result(timeout=15)
    assert result["counts"]["queued"] == 2
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 2
