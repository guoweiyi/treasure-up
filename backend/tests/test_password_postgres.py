import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import delete, func, select, update

from app.config import settings
from app import main, schemas
from app.models import LoginAttempt, User, UserSession
from app.security import hash_password, password_attempt_lock, reserve_password_attempt
from test_passkeys_postgres import _request, _wait_for_postgres_lock_waiters, _worker_session
from test_postgres_integration import postgres_workspace  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get('TREASURE_RUN_POSTGRES_TESTS') != '1', reason='Isolated PostgreSQL opt-in required')


def test_parallel_password_requests_cannot_exceed_reserved_quota(postgres_workspace, monkeypatch):
    space = postgres_workspace
    monkeypatch.setattr(settings, 'login_limit', 3)
    digest = hashlib.sha256(_request().client.host.encode()).hexdigest()
    ready, pids = threading.Barrier(9), Queue()

    def reserve(db):
        reserve_password_attempt(db, _request())
        return 200

    with space.sessions() as blocker:
        with password_attempt_lock(blocker, digest):
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(_worker_session, space, ready, pids, reserve) for _ in range(8)]
                try:
                    ready.wait(timeout=10)
                    worker_pids = {pids.get(timeout=1) for _ in range(8)}
                    _wait_for_postgres_lock_waiters(space, worker_pids)
                finally:
                    blocker.rollback()
                results = [future.result(timeout=15) for future in futures]
    assert results.count(200) == 3
    assert results.count(429) == 5
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(LoginAttempt)) == 3


@pytest.mark.parametrize('change', ['password', 'disabled'])
def test_password_reset_or_disable_wins_over_inflight_login(postgres_workspace, monkeypatch, change):
    space = postgres_workspace
    password = 'isolated-password-123'
    with space.sessions() as db:
        user = User(username='race-user', password_hash=hash_password(password))
        db.add(user); db.commit()
        user_id = user.id
    hashed, proceed = threading.Event(), threading.Event()
    verify = main.verify_password

    def race(value, encoded):
        result = verify(value, encoded)
        hashed.set()
        assert proceed.wait(timeout=10)
        return result

    monkeypatch.setattr(main, 'verify_password', race)
    def login():
        with space.sessions() as db:
            try:
                main.login(schemas.Login(username='race-user', password=password), _request(), Response(), db)
                return 200
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(login)
        try:
            assert hashed.wait(timeout=10)
            with space.sessions() as db:
                changes = {'disabled': True} if change == 'disabled' else {'password_hash': hash_password('replacement-password-123')}
                db.execute(update(User).where(User.id == user_id).values(**changes))
                db.execute(delete(UserSession).where(UserSession.user_id == user_id))
                db.commit()
        finally:
            proceed.set()
        assert pending.result(timeout=15) == 401
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(UserSession)) == 0
