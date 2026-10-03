"""Opt-in PostgreSQL concurrency guarantees, without repeating WebAuthn crypto."""
import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from starlette.requests import Request

from app import passkeys
from app.config import settings
from app.models import PasskeyAttempt, PasskeyChallenge, utcnow
from test_postgres_integration import postgres_workspace  # noqa: F401; isolated test DB fixture


pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit isolated PostgreSQL test opt-in required")


def _request(binding=None):
    headers = [(b"cookie", f"{passkeys.CHALLENGE_COOKIE}={binding}".encode())] if binding else []
    return Request({"type": "http", "method": "POST", "path": "/api/v1/auth/passkeys/login/verify",
                    "scheme": "https", "headers": headers, "client": ("198.51.100.73", 50000),
                    "server": ("archive.example", 443), "query_string": b""})


def _wait_for_postgres_lock_waiters(space, pids):
    """Assert genuine database contention, not merely simultaneous thread start."""
    deadline = time.monotonic() + 5
    with space.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
        while time.monotonic() < deadline:
            waiting = set(observer.scalars(text("""
                SELECT pid FROM pg_stat_activity
                WHERE datname=current_database() AND wait_event_type='Lock'
                  AND cardinality(pg_blocking_pids(pid)) > 0
            """)))
            if pids <= waiting:
                return
            time.sleep(0.02)
    pytest.fail("Concurrent sessions did not all reach a PostgreSQL lock wait")


def _worker_session(space, ready, pids, callback):
    with space.sessions() as db:
        db.execute(text("SET LOCAL lock_timeout = '10s'"))
        db.execute(text("SET LOCAL statement_timeout = '15s'"))
        pids.put(db.scalar(text("SELECT pg_backend_pid()")))
        ready.wait(timeout=10)
        try:
            return callback(db)
        except HTTPException as error:
            return error.status_code


def test_postgres_concurrent_attempt_reservations_respect_exact_quota(postgres_workspace, monkeypatch):
    space = postgres_workspace
    monkeypatch.setattr(settings, "passkey_limit", 3)
    monkeypatch.setattr(settings, "passkey_window_seconds", 600)
    purpose = "login_verify"
    digest = hashlib.sha256(_request().client.host.encode()).hexdigest()
    ready, pids = threading.Barrier(9), Queue()

    def reserve(db):
        passkeys.reserve_attempt(db, _request(), purpose)
        return 200

    with space.sessions() as blocker:
        # Hold the actual transaction advisory lock, then prove all eight
        # independent sessions contend for it before any quota count runs.
        with passkeys._limit_lock(blocker, digest, purpose):
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(_worker_session, space, ready, pids, reserve) for _ in range(8)]
                try:
                    ready.wait(timeout=10)
                    worker_pids = {pids.get(timeout=1) for _ in range(8)}
                    assert len(worker_pids) == 8
                    _wait_for_postgres_lock_waiters(space, worker_pids)
                finally:
                    blocker.rollback()  # Release before joining blocked workers, including on assertion failure.
                statuses = [future.result(timeout=15) for future in futures]

    assert statuses.count(200) == 3
    assert statuses.count(429) == 5
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(PasskeyAttempt).where(
            PasskeyAttempt.client_hash == digest, PasskeyAttempt.purpose == purpose)) == 3


def test_postgres_challenge_is_consumed_once_under_row_lock_contention(postgres_workspace, monkeypatch):
    space = postgres_workspace
    monkeypatch.setattr(settings, "passkey_origin", "https://archive.example")
    monkeypatch.setattr(settings, "passkey_rp_id", "archive.example")
    binding = "synthetic-single-use-binding"
    with space.sessions() as db:
        challenge = PasskeyChallenge(challenge="c3ludGhldGljLWNoYWxsZW5nZQ", purpose="login",
            binding_hash=hashlib.sha256(binding.encode()).hexdigest(), origin="https://archive.example",
            rp_id="archive.example", expires_at=utcnow() + timedelta(minutes=5))
        db.add(challenge)
        db.commit()
        challenge_id = challenge.id
    ready, pids = threading.Barrier(3), Queue()

    def consume(db):
        return passkeys.consume_challenge(db, _request(binding), challenge_id, "login")

    with space.sessions() as blocker:
        blocker.scalar(select(PasskeyChallenge).where(PasskeyChallenge.id == challenge_id).with_for_update())
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_worker_session, space, ready, pids, consume) for _ in range(2)]
            try:
                ready.wait(timeout=10)
                worker_pids = {pids.get(timeout=1) for _ in range(2)}
                assert len(worker_pids) == 2
                _wait_for_postgres_lock_waiters(space, worker_pids)
            finally:
                blocker.rollback()
            results = [future.result(timeout=15) for future in futures]

    winners = [result for result in results if isinstance(result, dict)]
    assert winners == [{"challenge": "c3ludGhldGljLWNoYWxsZW5nZQ", "origin": "https://archive.example",
                        "rp_id": "archive.example"}]
    assert results.count(400) == 1
    with space.sessions() as db:
        assert db.get(PasskeyChallenge, challenge_id).consumed_at is not None
        with pytest.raises(HTTPException) as replay:
            passkeys.consume_challenge(db, _request(binding), challenge_id, "login")
        assert replay.value.status_code == 400
