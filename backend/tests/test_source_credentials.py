"""Credential lifecycle tests use synthetic values and never contact Bilibili."""
from datetime import timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import source_credentials as service
from app.config import settings
from app.ingest.errors import IngestDeferred, IngestError
from app.ingest import passport as protocol
from app.ingest.passport import Credential, RefreshInfo
from app.models import Base, Job, SourceAccount, utcnow
from app.security import decrypt_secret, encrypt_secret

OLD_COOKIE = "SESSDATA=synthetic-old; bili_jct=" + "a" * 32 + "; DedeUserID=123"
NEW_COOKIE = "SESSDATA=synthetic-new; bili_jct=" + "b" * 32 + "; DedeUserID=123"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", Fernet.generate_key().decode())
    engine = create_engine("sqlite:///" + str(tmp_path / "credentials.db"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        row = SourceAccount(name="Test", uid="123", status="valid", secret_encrypted=encrypt_secret(OLD_COOKIE))
        service.reset_refresh(row, "old-refresh")
        db.add(row)
        db.commit()
        identity = row.id
    yield sessions, identity
    engine.dispose()


class Passport:
    def __init__(self, *, refresh=True, confirm_error=None, rotation_error=None, on_info=None, on_confirm=None):
        self.needed, self.confirm_error, self.rotation_error = refresh, confirm_error, rotation_error
        self.on_info, self.on_confirm = on_info, on_confirm
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def refresh_info(self, cookie):
        self.calls.append(("info", cookie))
        if self.on_info:
            self.on_info()
        return RefreshInfo(refresh=self.needed, timestamp_ms=1700000000000)

    def refresh(self, cookie, token, *, timestamp_ms, before_rotate):
        before_rotate()
        self.calls.append(("rotate", cookie, token))
        if self.rotation_error:
            raise self.rotation_error
        return Credential(cookie_text=NEW_COOKIE, refresh_token="new-refresh", uid="123", bili_jct="b" * 32)

    def confirm_refresh(self, cookie, old_token):
        self.calls.append(("confirm", cookie, old_token))
        if self.on_confirm:
            self.on_confirm()
        if self.confirm_error:
            raise self.confirm_error


def run(workspace, passport, **kwargs):
    sessions, identity = workspace
    with sessions() as db:
        return service.refresh_account(db, identity, passport_factory=lambda: passport, **kwargs)


def read(workspace):
    sessions, identity = workspace
    with sessions() as db:
        return db.get(SourceAccount, identity)


def test_fresh_cookie_only_checks_once_and_schedules_next_day(workspace):
    passport = Passport(refresh=False)
    result = run(workspace, passport)
    account = read(workspace)
    assert result["rotated"] is False
    assert [call[0] for call in passport.calls] == ["info"]
    assert service.utc(account.next_refresh_at) > utcnow() + timedelta(hours=23)
    assert decrypt_secret(account.refresh_token_encrypted) == "old-refresh"


def test_rotation_commits_both_generations_before_confirmation(workspace):
    def inspect_pending():
        account = read(workspace)
        assert decrypt_secret(account.secret_encrypted) == NEW_COOKIE
        assert decrypt_secret(account.refresh_token_encrypted) == "new-refresh"
        assert decrypt_secret(account.refresh_pending_encrypted) == "old-refresh"
        assert account.refresh_phase == "pending_confirm"
    passport = Passport(on_confirm=inspect_pending)
    result = run(workspace, passport)
    account = read(workspace)
    assert result["rotated"] is True
    assert passport.calls[-1] == ("confirm", NEW_COOKIE, "old-refresh")
    assert account.refresh_pending_encrypted is None
    assert account.refresh_phase == "ready"
    public = str(service.account_view(account)) + str(result)
    assert all(secret not in public for secret in (OLD_COOKIE, NEW_COOKIE, "old-refresh", "new-refresh"))


def test_successful_rotation_restores_expired_account_availability(workspace):
    sessions, identity = workspace
    with sessions() as db:
        db.get(SourceAccount, identity).status = "expired"
        db.commit()
    run(workspace, Passport())
    row = read(workspace)
    assert row.status == "valid" and row.last_verified_at


def test_confirmation_restart_retries_only_confirm_and_stops_automatic_after_five(workspace):
    error = IngestError("synthetic", code="network_error")
    run(workspace, Passport(confirm_error=error))
    for _ in range(4):
        retry = Passport(confirm_error=error)
        run(workspace, retry)
        assert retry.calls == [("confirm", NEW_COOKIE, "old-refresh")]
    row = read(workspace)
    assert row.refresh_pending_encrypted and row.refresh_failures == 5
    assert row.refresh_error_code == "confirm_exhausted" and row.next_refresh_at is None
    sessions, identity = workspace
    with sessions() as db:
        service.schedule_refreshes(db)
        assert db.scalar(select(Job.id)) is None
    retry = Passport()
    assert run(workspace, retry, manual=True)["rotated"] is True
    assert retry.calls == [("confirm", NEW_COOKIE, "old-refresh")]


@pytest.mark.parametrize("from_crash", [False, True])
def test_unknown_rotation_result_never_replays_even_after_manual_retry(workspace, from_crash):
    if from_crash:
        sessions, identity = workspace
        with sessions() as db:
            db.get(SourceAccount, identity).refresh_phase = "refreshing"
            db.commit()
        passport = Passport()
    else:
        error = IngestError("synthetic", code="network_error")
        error.mutation_started = True
        error.mutation_uncertain = True
        passport = Passport(rotation_error=error)
    result = run(workspace, passport)
    assert result["error_code"] == "refresh_uncertain"
    for _ in range(2):
        retry = Passport()
        assert run(workspace, retry, manual=True)["refresh_status"] == "relogin_required"
        assert retry.calls == []
    sessions, identity = workspace
    with sessions() as db:
        with pytest.raises(IngestError, match="重新扫码"):
            service.enqueue_refresh(db, db.get(SourceAccount, identity), manual=True)
    assert decrypt_secret(read(workspace).secret_encrypted) == OLD_COOKIE


@pytest.mark.parametrize("change", ["replace", "disable", "pause"])
def test_changes_during_info_abort_before_rotation(workspace, change):
    sessions, identity = workspace
    def mutate():
        with sessions() as db:
            row = db.get(SourceAccount, identity)
            if change == "replace":
                row.secret_encrypted = encrypt_secret("replacement")
                service.reset_refresh(row, "replacement-refresh")
            elif change == "disable":
                row.status = "disabled"
            else:
                row.auto_refresh_enabled = False
            db.commit()
    passport = Passport(on_info=mutate)
    with pytest.raises(IngestError):
        run(workspace, passport)
    assert [call[0] for call in passport.calls] == ["info"]
    if change == "replace":
        assert decrypt_secret(read(workspace).secret_encrypted) == "replacement"


def test_cancel_after_rotation_preserves_new_credentials_for_confirmation(workspace):
    def guard():
        if read(workspace).refresh_phase == "pending_confirm":
            raise RuntimeError("cancelled")
    passport = Passport()
    with pytest.raises(RuntimeError, match="cancelled"):
        run(workspace, passport, check_active=guard)
    assert [call[0] for call in passport.calls] == ["info", "rotate"]
    assert decrypt_secret(read(workspace).secret_encrypted) == NEW_COOKIE
    retry = Passport()
    run(workspace, retry)
    assert retry.calls == [("confirm", NEW_COOKIE, "old-refresh")]


def test_paused_auto_job_and_manual_check_are_distinct(workspace):
    sessions, identity = workspace
    with sessions() as db:
        db.get(SourceAccount, identity).auto_refresh_enabled = False
        db.commit()
    passport = Passport(refresh=False)
    assert run(workspace, passport)["refresh_status"] == "paused" and not passport.calls
    assert run(workspace, passport, manual=True)["refresh_status"] == "ready"
    assert not read(workspace).auto_refresh_enabled


def test_scheduler_is_bounded_deduplicated_and_does_not_rollback_caller(workspace):
    sessions, identity = workspace
    with sessions() as db:
        for index in range(25):
            row = SourceAccount(name=str(index), secret_encrypted=encrypt_secret(OLD_COOKIE))
            service.reset_refresh(row, "token")
            db.add(row)
        db.commit()
        service.schedule_refreshes(db)
        assert len(db.scalars(select(Job)).all()) == 20
        service.schedule_refreshes(db)
        assert len(db.scalars(select(Job)).all()) == 26
        service.schedule_refreshes(db)
        assert len(db.scalars(select(Job)).all()) == 26
        row = db.get(SourceAccount, identity)
        row.name = "caller-change"
        service.schedule_refreshes(db)
        assert row.name == "caller-change" and row in db.dirty


def test_manual_check_reuses_active_job_and_bounds_repeated_checks(workspace):
    sessions, identity = workspace
    with sessions() as db:
        account = db.get(SourceAccount, identity)
        one = service.enqueue_refresh(db, account, manual=True)
        db.commit()
        assert service.enqueue_refresh(db, account, manual=True).id == one.id
        one.status = "succeeded"
        account.last_refresh_check_at = utcnow()
        db.commit()
        with pytest.raises(IngestDeferred):
            service.enqueue_refresh(db, account, manual=True)


@pytest.mark.parametrize("stage", ["info", "correspond"])
@pytest.mark.parametrize("header,seconds", [("7200", 7200), ("1", 900), ("999999999", 86400), ("invalid", 900)])
def test_upstream_retry_after_extends_bounded_refresh_backoff_only(workspace, stage, header, seconds):
    calls = []
    def handler(request):
        calls.append(str(request.url).split("?")[0])
        if str(request.url).split("?")[0] == protocol.INFO and stage == "correspond":
            return httpx.Response(200, json={"code": 0, "data": {"refresh": True, "timestamp": 1700000000000}})
        return httpx.Response(429, headers={"retry-after": header})
    started = utcnow()
    result = run(workspace, protocol.BiliPassport(transport=httpx.MockTransport(handler)))
    row = read(workspace)
    assert result["refresh_status"] == "error" and row.refresh_error_code == "rate_limited"
    assert started + timedelta(seconds=seconds) <= service.utc(row.next_refresh_at) <= utcnow() + timedelta(seconds=seconds)
    assert row.cooldown_until is None and row.next_request_at is None and row.next_video_at is None
    assert protocol.REFRESH not in calls and protocol.CONFIRM not in calls
    assert decrypt_secret(row.secret_encrypted) == OLD_COOKIE


@pytest.mark.parametrize("header,seconds", [("43200", 43200), ("999999999", 86400), ("30", 14400), (None, None)])
def test_confirm_retry_limit_preserves_upstream_manual_cooldown_without_auto_retry(workspace, header, seconds):
    from app.source_credentials_api import check_refresh
    sessions, identity = workspace
    with sessions() as db:
        account = db.get(SourceAccount, identity)
        account.secret_encrypted = encrypt_secret(NEW_COOKIE)
        account.refresh_token_encrypted = encrypt_secret("new-refresh")
        account.refresh_pending_encrypted = encrypt_secret("old-refresh")
        account.refresh_phase, account.refresh_failures = "pending_confirm", 4
        db.commit()
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={"retry-after": header} if header is not None else {})
    started = utcnow()
    run(workspace, protocol.BiliPassport(transport=httpx.MockTransport(handler)))
    row = read(workspace)
    assert calls == [protocol.CONFIRM]
    assert row.refresh_failures == 5 and row.refresh_error_code == "confirm_exhausted"
    assert decrypt_secret(row.secret_encrypted) == NEW_COOKIE
    assert decrypt_secret(row.refresh_pending_encrypted) == "old-refresh"
    assert row.cooldown_until is None
    with sessions() as db:
        service.schedule_refreshes(db)
        assert db.scalar(select(Job.id)) is None
        if seconds is None:
            assert row.next_refresh_at is None
        else:
            assert started + timedelta(seconds=seconds) <= service.utc(row.next_refresh_at) <= utcnow() + timedelta(seconds=seconds)
            with pytest.raises(HTTPException) as error:
                check_refresh(identity, identity=("synthetic-admin", "synthetic-session"), db=db)
            assert error.value.status_code == 429
            assert seconds - 2 <= int(error.value.headers["Retry-After"]) <= seconds


def test_rotation_post_retry_after_does_not_reenable_unknown_mutation(workspace):
    calls = []
    def handler(request):
        url = str(request.url).split("?")[0]
        calls.append(url)
        if url == protocol.INFO:
            return httpx.Response(200, json={"code": 0, "data": {"refresh": True, "timestamp": 1700000000000}})
        if url.startswith(protocol.CORRESPOND):
            return httpx.Response(200, text='<div id="1-name">' + "c" * 32 + '</div>')
        assert url == protocol.REFRESH
        return httpx.Response(429, headers={"retry-after": "43200"})
    run(workspace, protocol.BiliPassport(transport=httpx.MockTransport(handler)))
    row = read(workspace)
    assert row.refresh_phase == "relogin_required" and row.refresh_error_code == "refresh_uncertain"
    assert row.next_refresh_at is None and row.cooldown_until is None
    retry = Passport()
    assert run(workspace, retry, manual=True)["refresh_status"] == "relogin_required"
    assert retry.calls == [] and calls.count(protocol.REFRESH) == 1
