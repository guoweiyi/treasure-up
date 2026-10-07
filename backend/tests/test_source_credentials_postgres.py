"""Opt-in real PG credential recovery/races; all upstream replies are synthetic."""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app import source_credentials as service
from app import source_credentials_api as api
from app.config import settings
from app.ingest.errors import IngestDeferred, IngestError
from app.ingest.passport import Credential, QrCode, QrPoll, RefreshInfo
from app.ingest.runner import credential_lock
from app.models import Job, SourceAccount, SourceAuthorization, User, UserSession, utcnow
from app.security import decrypt_secret, encrypt_secret
from test_postgres_integration import postgres_workspace

pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit TREASURE_RUN_POSTGRES_TESTS=1 required")

OLD_COOKIE = "SESSDATA=synthetic-old; bili_jct=" + "a" * 32 + "; DedeUserID=123"
FRESH = Credential("SESSDATA=synthetic-new; bili_jct=" + "b" * 32 + "; DedeUserID=123",
                   "new-synthetic-token", "123", "b" * 32)


@pytest.fixture
def seeded(postgres_workspace, monkeypatch):
    space = postgres_workspace
    monkeypatch.setattr(settings, "secret_key_file", None)
    monkeypatch.setattr(settings, "secret_key", space.key)
    with space.sessions() as db:
        user = User(username="synthetic-admin", password_hash="unused", role="admin")
        account = SourceAccount(name="synthetic-source", secret_encrypted=encrypt_secret(OLD_COOKIE),
                                uid="123", status="valid")
        service.reset_refresh(account, "old-synthetic-token")
        db.add_all([user, account])
        db.flush()
        session = UserSession(user_id=user.id, token_hash="a" * 64, csrf_token="synthetic-csrf",
                              expires_at=utcnow() + timedelta(hours=1))
        db.add(session)
        db.flush()
        authorization = SourceAuthorization(user_id=user.id, session_id=session.id, name="synthetic-source",
            account_id=account.id, account_generation=service.generation(account),
            key_encrypted=encrypt_secret("synthetic-qr-key"), expires_at=utcnow() + timedelta(minutes=3))
        db.add(authorization)
        db.commit()
        return SimpleNamespace(space=space, account_id=account.id, authorization_id=authorization.id,
                               identity=(user.id, session.id))


class Passport:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def refresh_info(self, cookie):
        return RefreshInfo(True, 1700000000000)

    def refresh(self, cookie, token, *, before_rotate, timestamp_ms):
        before_rotate()
        return FRESH

    def confirm_refresh(self, cookie, token):
        assert cookie == FRESH.cookie_text
        assert token == "old-synthetic-token"

    def poll_qrcode(self, key):
        assert key == "synthetic-qr-key"
        return QrPoll("success", FRESH)

    def generate_qrcode(self):
        return QrCode("https://passport.bilibili.com/synthetic-qr", "new-qr-key")


def run_refresh(fixture, passport, **kwargs):
    with fixture.space.sessions() as db:
        return service.refresh_account(db, fixture.account_id, passport_factory=passport, **kwargs)


def run_poll(fixture):
    with fixture.space.sessions() as db:
        return api.poll_authorization(fixture.authorization_id, identity=fixture.identity, db=db)


def account_snapshot(fixture):
    with fixture.space.sessions() as db:
        account = db.get(SourceAccount, fixture.account_id)
        db.expunge(account)
        return account


@pytest.mark.parametrize("interruption", ["process_crash", "unknown_post_result"])
def test_uncertain_rotation_never_reissued_even_on_repeated_manual_check(seeded, interruption):
    calls = []

    class Interrupted(Passport):
        def refresh(self, cookie, token, *, before_rotate, timestamp_ms):
            before_rotate()
            calls.append("rotate")
            if interruption == "process_crash":
                raise RuntimeError("synthetic process interruption")
            error = IngestError("synthetic network failure", code="network_error")
            error.mutation_started = error.mutation_uncertain = True
            raise error

    if interruption == "process_crash":
        with pytest.raises(RuntimeError, match="synthetic process interruption"):
            run_refresh(seeded, Interrupted)
        assert account_snapshot(seeded).refresh_phase == "refreshing"
    else:
        assert run_refresh(seeded, Interrupted)["refresh_status"] == "relogin_required"

    class NoRequests(Passport):
        def refresh_info(self, cookie):
            pytest.fail("Uncertain old generation must not issue another upstream check/rotation")

    for _ in range(2):
        result = run_refresh(seeded, NoRequests, manual=True)
        assert result["refresh_status"] == "relogin_required"
    account = account_snapshot(seeded)
    assert calls == ["rotate"]
    assert decrypt_secret(account.secret_encrypted) == OLD_COOKIE
    assert account.refresh_pending_encrypted is None


def test_rotation_durable_before_confirmation_and_resume_only_confirms(seeded):
    calls = []

    class CrashAtConfirm(Passport):
        def refresh(self, *args, **kwargs):
            calls.append("rotate")
            return super().refresh(*args, **kwargs)

        def confirm_refresh(self, cookie, token):
            persisted = account_snapshot(seeded)
            assert decrypt_secret(persisted.secret_encrypted) == FRESH.cookie_text
            assert decrypt_secret(persisted.refresh_token_encrypted) == FRESH.refresh_token
            assert decrypt_secret(persisted.refresh_pending_encrypted) == "old-synthetic-token"
            assert persisted.refresh_phase == "pending_confirm"
            raise RuntimeError("synthetic crash before confirmation")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        run_refresh(seeded, CrashAtConfirm)

    class ConfirmOnly(Passport):
        def refresh_info(self, cookie):
            pytest.fail("Persisted rotation must resume confirmation without refresh_info")

        def confirm_refresh(self, cookie, token):
            calls.append("confirm")
            super().confirm_refresh(cookie, token)

    assert run_refresh(seeded, ConfirmOnly)["refresh_status"] == "ready"
    account = account_snapshot(seeded)
    assert calls == ["rotate", "confirm"]
    assert account.refresh_pending_encrypted is None
    assert decrypt_secret(account.secret_encrypted) == FRESH.cookie_text


def test_confirmation_failures_preserve_new_cookie_and_stop_automatic_retry(seeded):
    calls = []

    class FailingConfirmation(Passport):
        def refresh(self, *args, **kwargs):
            calls.append("rotate")
            return super().refresh(*args, **kwargs)

        def confirm_refresh(self, cookie, token):
            super().confirm_refresh(cookie, token)
            calls.append("confirm")
            raise IngestError("synthetic confirmation timeout", code="network_error")

    for attempt in range(service.MAX_CONFIRM_FAILURES):
        assert run_refresh(seeded, FailingConfirmation)["refresh_status"] == "pending_confirm"
        account = account_snapshot(seeded)
        assert account.refresh_failures == attempt + 1
        assert decrypt_secret(account.secret_encrypted) == FRESH.cookie_text
        assert decrypt_secret(account.refresh_pending_encrypted) == "old-synthetic-token"
    assert calls.count("rotate") == 1
    assert account.next_refresh_at is None
    assert account.refresh_error_code == "confirm_exhausted"
    with seeded.space.sessions() as db:
        service.schedule_refreshes(db)
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    # An explicit manual retry may confirm, but must never rotate again.
    class ConfirmOnly(Passport):
        def refresh_info(self, cookie):
            pytest.fail("Pending confirmation must not consume another refresh token")

    assert run_refresh(seeded, ConfirmOnly, manual=True)["refresh_status"] == "ready"
    account = account_snapshot(seeded)
    assert account.refresh_pending_encrypted is None
    assert account.refresh_failures == 0


def test_refresh_http_releases_account_mutex_but_excludes_second_rotation(seeded):
    entered, release = threading.Event(), threading.Event()

    class Paused(Passport):
        def refresh_info(self, cookie):
            entered.set()
            assert release.wait(10)
            return RefreshInfo(False, 1700000000000)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run_refresh, seeded, Paused)
        try:
            assert entered.wait(10)
            # Ordinary source API credential snapshots stay available during HTTP.
            with seeded.space.sessions() as db, credential_lock(db, seeded.account_id):
                row = db.get(SourceAccount, seeded.account_id, with_for_update=True)
                row.name = "updated while HTTP waits"
                db.commit()
            with pytest.raises(IngestDeferred) as blocked:
                run_refresh(seeded, Passport)
            assert blocked.value.code == "resource_busy"
            with seeded.space.sessions() as inspector:
                idle = inspector.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                                             "AND state='idle in transaction' AND pid<>pg_backend_pid()"))
                assert idle == 0
        finally:
            release.set()
        assert pending.result(timeout=10)["refresh_status"] == "ready"
    assert account_snapshot(seeded).name == "updated while HTTP waits"


def test_credential_replacement_during_correspond_prevents_rotation_post(seeded):
    entered, release = threading.Event(), threading.Event()
    calls = []

    class CorrespondWait(Passport):
        def refresh(self, cookie, token, *, before_rotate, timestamp_ms):
            entered.set()
            assert release.wait(10)
            before_rotate()
            calls.append("rotate")
            return FRESH

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run_refresh, seeded, CorrespondWait)
        try:
            assert entered.wait(10)
            with seeded.space.sessions() as db, credential_lock(db, seeded.account_id):
                account = db.get(SourceAccount, seeded.account_id)
                service.install_credential(account, FRESH)
                db.commit()
        finally:
            release.set()
        with pytest.raises(IngestDeferred) as changed:
            pending.result(timeout=10)
        assert changed.value.code == "account_changed"
    assert calls == []
    assert decrypt_secret(account_snapshot(seeded).secret_encrypted) == FRESH.cookie_text


def test_successful_rotation_is_saved_even_if_account_disabled_during_post(seeded):
    entered, release = threading.Event(), threading.Event()

    class RotateWait(Passport):
        def refresh(self, cookie, token, *, before_rotate, timestamp_ms):
            before_rotate()
            entered.set()
            assert release.wait(10)
            return FRESH

        def confirm_refresh(self, cookie, token):
            pytest.fail("Disabled source must not start confirmation HTTP")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run_refresh, seeded, RotateWait)
        try:
            assert entered.wait(10)
            with seeded.space.sessions() as db, credential_lock(db, seeded.account_id):
                db.get(SourceAccount, seeded.account_id).status = "disabled"
                db.commit()
        finally:
            release.set()
        with pytest.raises(IngestError) as stopped:
            pending.result(timeout=10)
        assert stopped.value.code == "account_disabled"
    account = account_snapshot(seeded)
    assert account.status == "disabled"
    assert account.refresh_phase == "pending_confirm"
    assert decrypt_secret(account.secret_encrypted) == FRESH.cookie_text
    assert decrypt_secret(account.refresh_pending_encrypted) == "old-synthetic-token"


class Client:
    def __init__(self, cookie):
        assert cookie == FRESH.cookie_text

    def close(self):
        pass

    def nav(self):
        return {"mid": 123}


@pytest.mark.parametrize("intervention", ["cancel", "replace", "revoke_session", "demote_admin"])
def test_qr_nav_does_not_bind_after_cancel_credential_or_session_change(seeded, monkeypatch, intervention):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(api, "BiliPassport", Passport)

    class PausedClient(Client):
        def nav(self):
            entered.set()
            assert release.wait(10)
            return super().nav()

    monkeypatch.setattr(api, "BiliClient", PausedClient)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run_poll, seeded)
        try:
            assert entered.wait(10)
            with seeded.space.sessions() as db:
                row = db.get(SourceAuthorization, seeded.authorization_id)
                assert row.status == "validating"
                assert FRESH.refresh_token not in row.credential_encrypted
                db.rollback()
                if intervention == "cancel":
                    assert api.cancel_authorization(seeded.authorization_id, identity=seeded.identity, db=db)["status"] == "cancelled"
                elif intervention == "replace":
                    with credential_lock(db, seeded.account_id):
                        account = db.get(SourceAccount, seeded.account_id)
                        service.reset_refresh(account, "manually-replaced-token")
                        db.commit()
                elif intervention == "revoke_session":
                    db.delete(db.get(UserSession, seeded.identity[1]))
                    db.commit()
                else:
                    db.get(User, seeded.identity[0]).role = "reader"
                    db.commit()
        finally:
            release.set()
        if intervention in {"revoke_session", "demote_admin"}:
            with pytest.raises(HTTPException) as stopped:
                pending.result(timeout=10)
            assert stopped.value.status_code == 401
        else:
            assert pending.result(timeout=10)["status"] == ("cancelled" if intervention == "cancel" else "error")
    assert decrypt_secret(account_snapshot(seeded).secret_encrypted) == OLD_COOKIE


def test_qr_consumed_credential_survives_nav_failure_without_repolling(seeded, monkeypatch):
    calls = []

    class CountPoll(Passport):
        def poll_qrcode(self, key):
            calls.append("poll")
            return super().poll_qrcode(key)

    class FailingClient(Client):
        def nav(self):
            raise IngestError("synthetic network error", code="network_error")

    monkeypatch.setattr(api, "BiliPassport", CountPoll)
    monkeypatch.setattr(api, "BiliClient", FailingClient)
    assert run_poll(seeded)["status"] == "validating"
    with seeded.space.sessions() as db:
        row = db.get(SourceAuthorization, seeded.authorization_id)
        assert row.key_encrypted is None
        assert json.loads(decrypt_secret(row.credential_encrypted))["refresh_token"] == FRESH.refresh_token
        row.last_polled_at = utcnow() - timedelta(seconds=10)
        db.commit()
    monkeypatch.setattr(api, "BiliClient", Client)
    result = run_poll(seeded)
    assert result["status"] == "success"
    assert calls == ["poll"]
    assert all(secret not in str(result) for secret in [FRESH.cookie_text, FRESH.refresh_token, FRESH.bili_jct])
    assert decrypt_secret(account_snapshot(seeded).secret_encrypted) == FRESH.cookie_text
    with seeded.space.sessions() as db:
        row = db.get(SourceAuthorization, seeded.authorization_id)
        assert row.credential_encrypted is row.key_encrypted is None


@pytest.mark.parametrize("operation", ["new_qr", "cancel"])
def test_qr_cancellation_rechecks_old_status_after_concurrent_success(seeded, monkeypatch, operation):
    monkeypatch.setattr(api, "BiliPassport", Passport)

    def start():
        with seeded.space.sessions() as db:
            if operation == "cancel":
                return api.cancel_authorization(seeded.authorization_id, identity=seeded.identity, db=db)
            return api.start_authorization(api.AuthorizationInput(name="replacement", account_id=seeded.account_id),
                                           identity=seeded.identity, db=db)

    with seeded.space.sessions() as completing, ThreadPoolExecutor(max_workers=1) as pool:
        row = completing.get(SourceAuthorization, seeded.authorization_id, with_for_update=True)
        row.status = "success"
        completing.flush()
        pending = pool.submit(start)
        try:
            # Whether blocked on SELECT or stale UPDATE, observe the real lock
            # wait before committing. A correct locking SELECT rechecks ACTIVE.
            deadline = time.monotonic() + 10
            waiting = False
            while time.monotonic() < deadline:
                with seeded.space.sessions() as inspector:
                    waiting = bool(inspector.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND wait_event_type='Lock' AND query LIKE '%source_authorizations%'")))
                if waiting:
                    break
                time.sleep(.02)
            assert waiting, "Second authorization never reached the existing row lock"
        finally:
            completing.commit()
        assert pending.result(timeout=10)["status"] == ("pending" if operation == "new_qr" else "success")
    with seeded.space.sessions() as db:
        assert db.get(SourceAuthorization, seeded.authorization_id).status == "success"
