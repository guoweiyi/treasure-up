"""PostgreSQL regressions for User -> Session vs Session -> audit FK locks."""
import hashlib
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import event, func, select, text
from starlette.requests import Request

from app import cli, passkeys
from app.config import settings
from app.models import AuditLog, PasskeyChallenge, PasskeyCredential, User, UserSession, utcnow
from app.security import create_session, hash_password, verify_password
from test_passkeys import Authenticator, ORIGIN, b64
from test_postgres_integration import postgres_workspace  # noqa: F401


pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit isolated PostgreSQL test opt-in required")


def _request(binding=None):
    headers = [(b"origin", ORIGIN.encode())]
    if binding:
        headers.append((b"cookie", f"{passkeys.CHALLENGE_COOKIE}={binding}".encode()))
    return Request({"type": "http", "method": "POST", "path": "/api/v1/auth/passkeys/register/verify",
                    "scheme": "http", "headers": headers, "client": ("198.51.100.81", 50100),
                    "server": ("localhost", 8788), "query_string": b""})


@pytest.mark.parametrize("operation", ["reset", "register"])
def test_user_session_mutation_does_not_deadlock_passkey_revocation(postgres_workspace, monkeypatch, operation):
    space = postgres_workspace
    password, replacement = "synthetic-original-password", "synthetic-replacement-password"
    monkeypatch.setattr(settings, "passkeys_enabled", True)
    monkeypatch.setattr(settings, "passkey_origin", ORIGIN)
    monkeypatch.setattr(settings, "passkey_rp_id", "localhost")
    monkeypatch.setattr(settings, "passkey_limit", 50)
    challenge_bytes, binding = os.urandom(32), "synthetic-registration-binding"
    existing_device, new_device = Authenticator(), Authenticator()
    with space.sessions() as db:
        user = User(username="mutation-user", password_hash=hash_password(password), role="admin")
        db.add(user); db.flush()
        credential = PasskeyCredential(user_id=user.id, credential_id=b64(existing_device.identifier),
            public_key=b64(existing_device.cose), name="existing", transports=[], device_type="single_device")
        db.add(credential); db.flush()
        session, _ = create_session(db, user, passkey_credential_id=credential.id)
        password_session, _ = create_session(db, user)
        challenge = PasskeyChallenge(challenge=b64(challenge_bytes), purpose="register", user_id=user.id,
            session_id=session.id, binding_hash=hashlib.sha256(binding.encode()).hexdigest(), origin=ORIGIN,
            rp_id="localhost", expires_at=utcnow() + timedelta(minutes=5))
        db.add(challenge); db.commit()
        user_id, session_id, password_session_id = user.id, session.id, password_session.id
        credential_id, challenge_id = credential.id, challenge.id

    user_locked, sessions_deleted = threading.Event(), threading.Event()
    thread_state, database_errors = threading.local(), Queue()
    original_audit = passkeys._audit

    def after_execute(connection, cursor, statement, parameters, context, executemany):
        if (getattr(thread_state, "role", None) == "mutation" and not getattr(thread_state, "paused", False)
                and "FROM app_users" in statement
                and ("FOR UPDATE" in statement or "FOR NO KEY UPDATE" in statement)):
            # Both old and fixed implementations pause only after PostgreSQL
            # has actually acquired the User row lock.
            thread_state.paused = True
            user_locked.set()
            assert sessions_deleted.wait(timeout=10)

    def capture_database_error(context):
        code = getattr(context.original_exception, "sqlstate", None)
        if code:
            database_errors.put(code)

    def revoked_audit(db, user, action, identifier=None):
        assert action == "passkey_revoked"
        # revoke_passkey now holds the credential and deleted session rows;
        # the next audit flush needs KEY SHARE on the same User row.
        sessions_deleted.set()
        original_audit(db, user, action, identifier)

    monkeypatch.setattr(passkeys, "_audit", revoked_audit)
    event.listen(space.engine, "after_cursor_execute", after_execute)
    event.listen(space.engine, "handle_error", capture_database_error)
    monkeypatch.setattr(cli, "SessionLocal", space.sessions)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: replacement)
    monkeypatch.setattr(sys, "argv", ["treasure", "reset-password", "mutation-user"])
    body = passkeys.RegistrationVerificationInput(challenge_id=challenge_id, name="new-key",
        credential=new_device.response({"public_key": {"challenge": b64(challenge_bytes)}}, user_id, register=True))

    def mutation():
        thread_state.role = "mutation"
        if operation == "reset":
            cli.main()
            return "reset"
        with space.sessions() as db:
            db.execute(text("SET SESSION lock_timeout = '10s'"))
            db.execute(text("SET SESSION statement_timeout = '15s'"))
            identity = (db.get(User, user_id), db.get(UserSession, session_id))
            try:
                passkeys.register_verify(body, _request(binding), Response(), identity=identity, db=db)
            except HTTPException as error:
                return error.status_code
            return "registered"

    def revoke():
        thread_state.role = "revoke"
        assert user_locked.wait(timeout=10)
        with space.sessions() as db:
            db.execute(text("SET SESSION lock_timeout = '10s'"))
            db.execute(text("SET SESSION statement_timeout = '15s'"))
            identity = (db.get(User, user_id), db.get(UserSession, session_id))
            return passkeys.revoke_passkey(credential_id, passkeys.PasswordInput(password=password),
                                          _request(), identity=identity, db=db)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            mutating, revoking = executor.submit(mutation), executor.submit(revoke)
            try:
                assert revoking.result(timeout=20) == {"ok": True}
                assert mutating.result(timeout=20) == ("reset" if operation == "reset" else 400)
            finally:
                user_locked.set()
                sessions_deleted.set()
    finally:
        event.remove(space.engine, "after_cursor_execute", after_execute)
        event.remove(space.engine, "handle_error", capture_database_error)
    # register_verify deliberately maps DB exceptions to HTTP 400. Inspect
    # the driver's errors so a deadlock victim cannot masquerade as the expected
    # rejection of an already revoked registration session.
    assert database_errors.empty()
    with space.sessions() as db:
        user = db.get(User, user_id)
        assert verify_password(replacement if operation == "reset" else password, user.password_hash)
        assert db.get(PasskeyCredential, credential_id).revoked_at is not None
        assert db.get(UserSession, session_id) is None
        assert (db.get(UserSession, password_session_id) is None) == (operation == "reset")
        assert db.scalar(select(func.count()).select_from(PasskeyCredential).where(PasskeyCredential.user_id == user_id)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.actor_id == user_id, AuditLog.action == "passkey_revoked", AuditLog.entity_id == credential_id)) == 1
