"""Personal-token lifecycle under actual PostgreSQL contention and recovery.

Only random disposable databases from postgres_workspace are modified.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import os
from queue import Queue
import secrets
import threading

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app import backup, identity_api, main, security, userscript_api
from app.config import settings
from app.models import (IdentityToken, IntegrationToken, LoginAttempt, Setting, SourceAccount,
                        User, UserSession, utcnow)
from app.schemas import Login, UserUpdate
from test_passkeys_postgres import _wait_for_postgres_lock_waiters, _worker_session
from test_postgres_integration import postgres_workspace  # noqa: F401


pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Isolated PostgreSQL opt-in required")
PASSWORD = "identity-test-password-123"


def request(path="/api/v1/auth/token-login", *, client="198.51.100.91", headers=()):
    return Request({"type": "http", "method": "POST", "path": path, "scheme": "https",
                    "headers": [(b"origin", b"https://archive.example"), *headers],
                    "client": (client, 5000), "server": ("archive.example", 443), "query_string": b""})


def seed(space, *, total=1):
    secret = "tul_" + secrets.token_urlsafe(40)
    with space.sessions() as db:
        user = User(username="identity-owner", password_hash=security.hash_password(PASSWORD), role="admin")
        actor = User(username="other-admin", password_hash="unused", role="admin")
        db.add_all([user, actor]); db.flush()
        for number in range(total):
            token = IdentityToken(user_id=user.id, name=f"device-{number}",
                                  token_hash=hashlib.sha256((secret if number == 0 else secrets.token_urlsafe(40)).encode()).hexdigest(),
                                  expires_at=utcnow() + timedelta(days=1))
            db.add(token); db.flush()
            if number == 0:
                token_id = token.id
        session, _ = security.create_session(db, user)
        db.commit()
        return secret, token_id, user.id, session.id, actor.id


def token_login(db, secret):
    return identity_api.token_login(identity_api.TokenLogin(token=secret), request(), Response(), db)


@pytest.mark.parametrize("change", ["revoke", "disable", "demote", "password"])
def test_revocation_or_admin_change_wins_before_waiting_token_login(postgres_workspace, change):
    space = postgres_workspace
    secret, token_id, user_id, session_id, actor_id = seed(space)
    ready, pids = threading.Barrier(2), Queue()
    with space.sessions() as blocker:
        user = blocker.scalar(select(User).where(User.id == user_id).with_for_update(key_share=True))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_worker_session, space, ready, pids, lambda db: token_login(db, secret))
            try:
                ready.wait(timeout=10)
                _wait_for_postgres_lock_waiters(space, {pids.get(timeout=1)})
                if change == "revoke":
                    identity_api.revoke_identity_token(token_id, request(),
                        identity=(user, blocker.get(UserSession, session_id)), db=blocker)
                else:
                    body = {"disabled": True} if change == "disable" else (
                        {"role": "reader"} if change == "demote" else {"password": "new-identity-password-123"})
                    main.update_user(user_id, UserUpdate(**body), user=blocker.get(User, actor_id), db=blocker)
            finally:
                blocker.rollback()
            assert future.result(timeout=15) == 401
    with space.sessions() as db:
        assert db.get(IdentityToken, token_id) is None
        assert db.scalar(select(func.count()).select_from(UserSession).where(UserSession.identity_token_id == token_id)) == 0


def test_revoke_waits_for_login_then_removes_its_new_session(postgres_workspace):
    space = postgres_workspace
    secret, token_id, user_id, session_id, _ = seed(space)
    login_ready, login_pids = threading.Barrier(2), Queue()
    revoke_ready, revoke_pids = threading.Barrier(2), Queue()

    def revoke(db):
        identity_api.revoke_identity_token(token_id, request(),
            identity=(db.get(User, user_id), db.get(UserSession, session_id)), db=db)
        return 204

    with space.sessions() as blocker:
        blocker.scalar(select(IdentityToken).where(IdentityToken.id == token_id).with_for_update())
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(_worker_session, space, login_ready, login_pids, lambda db: token_login(db, secret))
            try:
                login_ready.wait(timeout=10)
                login_pid = login_pids.get(timeout=1)
                _wait_for_postgres_lock_waiters(space, {login_pid})
                second = pool.submit(_worker_session, space, revoke_ready, revoke_pids, revoke)
                revoke_ready.wait(timeout=10)
                _wait_for_postgres_lock_waiters(space, {login_pid, revoke_pids.get(timeout=1)})
            finally:
                blocker.rollback()
            assert first.result(timeout=15)["user"]["id"] == user_id
            assert second.result(timeout=15) == 204
    with space.sessions() as db:
        assert db.get(IdentityToken, token_id) is None
        # Password session survives; the token-derived session is retired.
        assert list(db.scalars(select(UserSession.id))) == [session_id]


def test_parallel_token_creation_obeys_twenty_token_cap(postgres_workspace):
    space = postgres_workspace
    _, _, user_id, session_id, _ = seed(space, total=19)
    ready, pids = threading.Barrier(3), Queue()

    def issue(db):
        return identity_api.create_identity_token(identity_api.TokenCreate(name="racing device", password=PASSWORD),
            request(), identity=(db.get(User, user_id), db.get(UserSession, session_id)), db=db)

    with space.sessions() as blocker:
        blocker.scalar(select(User).where(User.id == user_id).with_for_update(key_share=True))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker_session, space, ready, pids, issue) for _ in range(2)]
            try:
                ready.wait(timeout=10)
                _wait_for_postgres_lock_waiters(space, {pids.get(timeout=1) for _ in range(2)})
            finally:
                blocker.rollback()
            results = [future.result(timeout=15) for future in futures]
    assert sum(isinstance(value, dict) for value in results) == 1 and results.count(409) == 1
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(IdentityToken)) == 20


def test_concurrent_password_and_token_failures_share_durable_rate_limit(postgres_workspace, monkeypatch):
    space = postgres_workspace
    monkeypatch.setattr(settings, "login_limit", 3)
    ready, pids = threading.Barrier(7), Queue()
    digest = hashlib.sha256(request().client.host.encode()).hexdigest()

    def password(db):
        return main.login(Login(username="missing", password=PASSWORD), request("/api/v1/auth/login"), Response(), db)

    with space.sessions() as blocker:
        with security.password_attempt_lock(blocker, digest):
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(_worker_session, space, ready, pids,
                    password if index % 2 else lambda db: token_login(db, "tul_" + "x" * 54)) for index in range(6)]
                try:
                    ready.wait(timeout=10)
                    _wait_for_postgres_lock_waiters(space, {pids.get(timeout=1) for _ in range(6)})
                finally:
                    blocker.rollback()
                results = [future.result(timeout=15) for future in futures]
    assert results.count(401) == 3 and results.count(429) == 3
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(LoginAttempt)) == 3
        assert db.scalar(select(func.count()).select_from(UserSession)) == 0


def test_expired_token_cleanup_cascades_derived_sessions_only(postgres_workspace):
    space = postgres_workspace
    _, token_id, user_id, session_id, _ = seed(space)
    with space.sessions() as db:
        item = db.get(IdentityToken, token_id)
        item.expires_at = utcnow() - timedelta(seconds=1)
        derived, _ = security.create_session(db, db.get(User, user_id))
        derived.identity_token_id = token_id
        derived.expires_at = item.expires_at
        db.commit()
        identity_api.create_identity_token(identity_api.TokenCreate(name="new", password=PASSWORD), request(),
            identity=(db.get(User, user_id), db.get(UserSession, session_id)), db=db)
    with space.sessions() as db:
        assert db.get(IdentityToken, token_id) is None
        assert list(db.scalars(select(UserSession.id))) == [session_id]


def test_identity_and_userscript_tokens_never_cross_authentication_boundaries(postgres_workspace):
    space = postgres_workspace
    secret, _, user_id, _, _ = seed(space)
    integration_secret = "tu_ingest_" + secrets.token_urlsafe(32)
    with space.sessions() as db:
        account = SourceAccount(name="fixture", secret_encrypted="unused", status="valid")
        db.add(account); db.flush()
        db.add(IntegrationToken(user_id=user_id, account_id=account.id, name="limited",
            token_hash=hashlib.sha256(integration_secret.encode()).hexdigest(), scope="ingest.submit",
            expires_at=utcnow() + timedelta(days=1)))
        db.commit()
        with pytest.raises(HTTPException) as invalid_login:
            token_login(db, integration_secret)
        assert invalid_login.value.status_code == 401
        db.rollback()
        with pytest.raises(HTTPException) as invalid_submit:
            userscript_api.integration_status(request(headers=[(b"authorization", ("Bearer " + secret).encode())]), db)
        assert invalid_submit.value.status_code == 401
        with pytest.raises(HTTPException) as invalid_session:
            security.authenticated(request(headers=[(b"authorization", ("Bearer " + secret).encode())]), db)
        assert invalid_session.value.status_code == 401
        assert db.scalar(select(func.count()).select_from(UserSession)) == 1


def test_backup_restore_retires_identity_tokens_and_their_sessions(postgres_workspace):
    space = postgres_workspace
    secret, _, user_id, _, _ = seed(space)
    with space.sessions() as db:
        token_login(db, secret)
        db.add(Setting(key="backup", value={"destination": str(space.root / "backup"), "key_id": "test"}))
        db.commit()
        archive = backup.create_backup(db, key=space.key)
        backup_id = archive.id
    result = backup.restore_backup(space.root / "backup", backup_id,
        space.restore_url.render_as_string(hide_password=False), space.root / "restored", key=space.key)
    assert result["restored"] is True
    engine = create_engine(space.restore_url)
    space.engines.append(engine)
    with Session(engine) as db:
        assert db.get(User, user_id) is not None
        assert db.scalar(select(func.count()).select_from(IdentityToken)) == 0
        assert db.scalar(select(func.count()).select_from(UserSession)) == 0
