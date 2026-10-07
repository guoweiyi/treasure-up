"""Synthetic HTTP/persistence regressions for administrator Bilibili authorization."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import source_credentials_api as endpoints
from app.ingest.errors import IngestError
from app.ingest.passport import Credential, QrCode, QrPoll
from app.main import app
from app.models import AuditLog, Job, SourceAccount, SourceAuthorization, UserSession, utcnow
from app.security import decrypt_secret, encrypt_secret
from test_api import context, login

ACCOUNTS = "/api/v1/admin/accounts"
AUTHORIZATIONS = ACCOUNTS + "/authorizations"
OLD_TOKEN = "synthetic-old-refresh-token"
NEW_TOKEN = "synthetic-new-refresh-token"
QR_KEY = "synthetic-qr-key"
JCT = "a" * 32
OLD_COOKIE = f"SESSDATA=synthetic-old-session; bili_jct={JCT}; DedeUserID=12345"
NEW_COOKIE = f"SESSDATA=synthetic-new-session; bili_jct={JCT}; DedeUserID=12345"


@pytest.fixture
def protocol(monkeypatch):
    """Only mock remote services; retain route, permissions, encryption and transactions."""
    state = SimpleNamespace(
        credential=Credential(NEW_COOKIE, NEW_TOKEN, "12345", JCT),
        generated=0, polled=0, verified=0, closed=0,
        polls=[], navs=[], on_poll=None, on_nav=None,
    )

    class Passport:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def generate_qrcode(self):
            state.generated += 1
            return QrCode(f"https://passport.bilibili.com/h5-app/passport/login/scan?qrcode_key={QR_KEY}", QR_KEY)

        def poll_qrcode(self, key):
            assert key == QR_KEY
            state.polled += 1
            if state.on_poll:
                state.on_poll()
            result = state.polls.pop(0) if state.polls else QrPoll("success", state.credential)
            if isinstance(result, Exception):
                raise result
            return result

    class Client:
        def __init__(self, cookie):
            assert cookie == state.credential.cookie_text

        def nav(self):
            state.verified += 1
            if state.on_nav:
                state.on_nav()
            result = state.navs.pop(0) if state.navs else {"mid": int(state.credential.uid), "isLogin": True}
            if isinstance(result, Exception):
                raise result
            return result

        def close(self):
            state.closed += 1

    monkeypatch.setattr(endpoints, "BiliPassport", Passport)
    monkeypatch.setattr(endpoints, "BiliClient", Client)
    return state


def create_account(client, **overrides):
    response = client.post(ACCOUNTS, json={
        "name": "本地合成测试账号", "cookie": OLD_COOKIE, "refresh_token": OLD_TOKEN, **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


def start(client, account_id=None):
    response = client.post(AUTHORIZATIONS, json={
        "name": "扫码合成账号", **({"account_id": account_id} if account_id else {}),
    })
    assert response.status_code == 201, response.text
    assert response.json()["expires_at"].endswith(("Z", "+00:00"))
    return response.json()


def poll(client, authorization_id):
    response = client.post(f"{AUTHORIZATIONS}/{authorization_id}/poll")
    assert response.status_code == 200, response.text
    return response.json()


def allow_next_poll(db, authorization_id):
    row = db.get(SourceAuthorization, authorization_id)
    row.last_polled_at = utcnow() - timedelta(seconds=4)
    db.commit()


def assert_private(value):
    serialized = json.dumps(value, ensure_ascii=False)
    for secret in (OLD_TOKEN, NEW_TOKEN, OLD_COOKIE, NEW_COOKIE, "secret_encrypted",
                   "refresh_token_encrypted", "credential_encrypted", "key_encrypted"):
        assert secret not in serialized


def assert_not_installed(db, authorization_id, status, account_id=None):
    db.expire_all()
    row = db.get(SourceAuthorization, authorization_id)
    assert row.status == status
    assert row.key_encrypted is None and row.credential_encrypted is None
    if account_id:
        assert decrypt_secret(db.get(SourceAccount, account_id).secret_encrypted) != NEW_COOKIE
    else:
        assert db.scalar(select(func.count()).select_from(SourceAccount)) == 0


def test_account_refresh_token_is_writeonly_encrypted_and_partial_edits_preserve_it(context):
    client, db, _ = context
    login(client)
    created = create_account(client)
    assert created["has_refresh_token"] is True
    assert created["auto_refresh_enabled"] is True
    assert created["refresh_status"] == "ready"
    assert created["last_refreshed_at"] is None and created["next_refresh_at"]
    account = db.get(SourceAccount, created["id"])
    ciphertext = account.refresh_token_encrypted
    assert OLD_TOKEN not in ciphertext
    assert decrypt_secret(ciphertext) == OLD_TOKEN
    assert decrypt_secret(account.secret_encrypted) == OLD_COOKIE
    assert_private(created)
    assert_private(client.get(ACCOUNTS).json())
    result = client.patch(f"{ACCOUNTS}/{account.id}", json={"name": "改名", "auto_refresh_enabled": False})
    assert result.status_code == 200, result.text
    assert result.json()["refresh_status"] == "paused"
    db.refresh(account)
    assert account.refresh_token_encrypted == ciphertext
    assert account.name == "改名"
    result = client.patch(f"{ACCOUNTS}/{account.id}", json={"auto_refresh_enabled": True})
    assert result.json()["refresh_status"] == "ready"
    assert_private(result.json())


def test_cookie_replacement_clears_old_token_and_confirmation_state_unless_pair_is_supplied(context):
    client, db, _ = context
    login(client)
    account_id = create_account(client)["id"]
    account = db.get(SourceAccount, account_id)
    account.refresh_pending_encrypted = encrypt_secret("synthetic-pending-confirmation")
    account.refresh_error_code, account.refresh_failures = "network_error", 3
    account.uid, account.status = "12345", "valid"
    db.commit()
    result = client.patch(f"{ACCOUNTS}/{account_id}", json={"cookie": NEW_COOKIE})
    assert result.status_code == 200, result.text
    assert result.json()["has_refresh_token"] is False
    assert result.json()["refresh_status"] == "unsupported"
    db.refresh(account)
    assert account.refresh_pending_encrypted is None
    assert account.refresh_error_code is None and account.refresh_failures == 0
    assert account.uid is None and account.status == "unverified"
    assert decrypt_secret(account.secret_encrypted) == NEW_COOKIE
    result = client.patch(f"{ACCOUNTS}/{account_id}", json={"cookie": OLD_COOKIE, "refresh_token": NEW_TOKEN})
    assert result.status_code == 200 and result.json()["has_refresh_token"] is True
    db.refresh(account)
    assert decrypt_secret(account.refresh_token_encrypted) == NEW_TOKEN
    result = client.patch(f"{ACCOUNTS}/{account_id}", json={"refresh_token": ""})
    assert result.json()["has_refresh_token"] is False
    db.refresh(account)
    assert account.refresh_token_encrypted is None
    assert decrypt_secret(account.secret_encrypted) == OLD_COOKIE


@pytest.mark.parametrize("role,expected", [(None, 401), ("reader", 403)])
def test_authorization_routes_require_an_administrator(context, protocol, role, expected):
    client, _, _ = context
    if role:
        login(client, role)
    paths = (AUTHORIZATIONS, f"{AUTHORIZATIONS}/unknown/poll",
             f"{AUTHORIZATIONS}/unknown/cancel", f"{ACCOUNTS}/unknown/refresh")
    for path in paths:
        result = client.post(path, json={"name": "账号"})
        assert result.status_code == expected, result.text
    assert protocol.generated == protocol.polled == protocol.verified == 0


@pytest.mark.parametrize("headers", [
    {"X-CSRF-Token": "wrong-token"},
    {"Origin": "https://untrusted.invalid"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_credentials_and_qr_mutations_enforce_csrf_and_same_origin(context, protocol, headers):
    client, _, _ = context
    login(client)
    account_id = create_account(client)["id"]
    authorization = start(client, account_id)
    requests = [
        ("POST", ACCOUNTS, {"name": "账号", "cookie": OLD_COOKIE}),
        ("PATCH", f"{ACCOUNTS}/{account_id}", {"refresh_token": NEW_TOKEN}),
        ("POST", AUTHORIZATIONS, {"name": "账号"}),
        ("POST", f"{AUTHORIZATIONS}/{authorization['id']}/poll", None),
        ("POST", f"{AUTHORIZATIONS}/{authorization['id']}/cancel", None),
        ("POST", f"{ACCOUNTS}/{account_id}/refresh", None),
    ]
    for method, path, body in requests:
        result = client.request(method, path, json=body, headers=headers)
        assert result.status_code == 403, (path, result.text)
    assert protocol.polled == protocol.verified == 0


def test_qr_owner_is_bound_to_the_login_session_not_just_the_admin_user(context, protocol):
    client, _, _ = context
    login(client)
    authorization = start(client)
    with TestClient(app, base_url="http://localhost") as other:
        login(other)
        for action in ("poll", "cancel"):
            result = other.post(f"{AUTHORIZATIONS}/{authorization['id']}/{action}")
            assert result.status_code == 404, result.text
    assert protocol.polled == 0
    assert poll(client, authorization["id"])["status"] == "success"


def test_qr_success_encrypts_full_credentials_and_repeated_poll_is_idempotent(context, protocol):
    client, db, _ = context
    login(client)
    authorization = start(client)
    row = db.get(SourceAuthorization, authorization["id"])
    assert QR_KEY not in row.key_encrypted
    assert decrypt_secret(row.key_encrypted) == QR_KEY
    result = poll(client, authorization["id"])
    assert result["status"] == "success"
    account = db.get(SourceAccount, result["account"]["id"])
    assert account.uid == "12345" and account.status == "valid"
    assert decrypt_secret(account.secret_encrypted) == NEW_COOKIE
    assert decrypt_secret(account.refresh_token_encrypted) == NEW_TOKEN
    assert account.auto_refresh_enabled is True
    assert account.last_verified_at is not None
    db.refresh(row)
    assert row.credential_encrypted is None and row.key_encrypted is None
    second = poll(client, authorization["id"])
    assert second["status"] == "success" and second["account"]["id"] == account.id
    assert db.scalar(select(func.count()).select_from(SourceAccount)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "authorize")) == 1
    assert protocol.polled == protocol.verified == protocol.closed == 1
    assert_private(result)
    assert_private(second)
    audit_details = [row.details for row in db.scalars(select(AuditLog))]
    assert_private(audit_details)
    assert QR_KEY not in json.dumps(audit_details)


def test_matching_existing_account_is_updated_in_place_by_qr_login(context, protocol):
    client, db, _ = context
    login(client)
    account_id = create_account(client, auto_refresh_enabled=False)["id"]
    authorization = start(client, account_id)
    result = poll(client, authorization["id"])
    assert result["status"] == "success" and result["account"]["id"] == account_id
    assert result["account"]["name"] == "扫码合成账号"
    assert result["account"]["auto_refresh_enabled"] is True
    assert db.scalar(select(func.count()).select_from(SourceAccount)) == 1
    account = db.get(SourceAccount, account_id)
    assert decrypt_secret(account.secret_encrypted) == NEW_COOKIE
    assert decrypt_secret(account.refresh_token_encrypted) == NEW_TOKEN
    assert_private(result)


@pytest.mark.parametrize("operation", ["create", "patch", "authorization"])
def test_account_validation_errors_never_echo_submitted_secrets(context, protocol, operation):
    client, db, _ = context
    login(client)
    payload = {"name": "账号", "cookie": OLD_COOKIE, "refresh_token": NEW_TOKEN * 300}
    if operation == "create":
        result = client.post(ACCOUNTS, json=payload)
    elif operation == "patch":
        account_id = create_account(client)["id"]
        result = client.patch(f"{ACCOUNTS}/{account_id}", json=payload)
    else:
        result = client.post(AUTHORIZATIONS, json=payload)
    assert result.status_code == 422, result.text
    assert isinstance(result.json()["detail"], str)
    assert result.headers["cache-control"] == "private, no-store"
    assert_private(result.json())
    assert protocol.generated == 0


def test_nav_transient_failure_retries_persisted_encrypted_credentials_without_repolling_qr(context, protocol):
    client, db, _ = context
    login(client)
    protocol.navs = [IngestError("temporary network error", code="network_error")]
    authorization = start(client)
    result = poll(client, authorization["id"])
    assert result["status"] == "validating"
    row = db.get(SourceAuthorization, authorization["id"])
    assert row.key_encrypted is None
    assert NEW_TOKEN not in row.credential_encrypted
    encrypted = json.loads(decrypt_secret(row.credential_encrypted))
    assert encrypted["cookie_text"] == NEW_COOKIE and encrypted["refresh_token"] == NEW_TOKEN
    assert db.scalar(select(func.count()).select_from(SourceAccount)) == 0
    assert_private(result)
    # A rapid repeat is throttled locally; a later retry resumes identity verification only.
    assert poll(client, authorization["id"])["status"] == "validating"
    assert protocol.verified == 1
    allow_next_poll(db, authorization["id"])
    assert poll(client, authorization["id"])["status"] == "success"
    assert protocol.polled == 1 and protocol.verified == protocol.closed == 2


@pytest.mark.parametrize("mismatch", ["nav", "existing_account"])
def test_qr_cannot_install_a_different_uid(context, protocol, mismatch):
    client, db, _ = context
    login(client)
    account_id = None
    if mismatch == "nav":
        protocol.navs = [{"mid": 99999, "isLogin": True}]
    else:
        account_id = create_account(client, cookie=OLD_COOKIE.replace("12345", "99999"))["id"]
    authorization = start(client, account_id)
    result = poll(client, authorization["id"])
    assert result["status"] == "error" and "不一致" in result["message"]
    assert_not_installed(db, authorization["id"], "error", account_id)


@pytest.mark.parametrize("timing", ["before_poll", "during_nav"])
def test_manual_credential_change_invalidates_an_older_qr_generation(context, protocol, timing):
    client, db, _ = context
    login(client)
    account_id = create_account(client)["id"]
    authorization = start(client, account_id)
    replacement = OLD_COOKIE.replace("synthetic-old-session", "synthetic-manual-session")
    if timing == "before_poll":
        changed = client.patch(f"{ACCOUNTS}/{account_id}", json={"cookie": replacement, "refresh_token": OLD_TOKEN})
        assert changed.status_code == 200, changed.text
    else:
        def manual_change():
            account = db.get(SourceAccount, account_id)
            account.secret_encrypted = encrypt_secret(replacement)
            db.commit()
        protocol.on_nav = manual_change
    result = poll(client, authorization["id"])
    assert result["status"] == "error" and "已被修改" in result["message"]
    assert_not_installed(db, authorization["id"], "error", account_id)
    assert decrypt_secret(db.get(SourceAccount, account_id).secret_encrypted) == replacement


@pytest.mark.parametrize("timing", ["before_poll", "during_poll", "during_nav"])
@pytest.mark.parametrize("terminal", ["cancelled", "expired"])
def test_cancelled_or_expired_qr_never_installs_credentials(context, protocol, terminal, timing):
    client, db, _ = context
    login(client)
    authorization = start(client)
    identifier = authorization["id"]

    def stop_authorization():
        row = db.get(SourceAuthorization, identifier)
        if terminal == "expired":
            row.expires_at = utcnow() - timedelta(seconds=1)
        else:
            # Match a cancellation committed while the simulated HTTP call is in flight.
            row.status = "cancelled"
            row.key_encrypted = row.credential_encrypted = None
        db.commit()

    if timing == "before_poll":
        if terminal == "cancelled":
            result = client.post(f"{AUTHORIZATIONS}/{identifier}/cancel")
            assert result.status_code == 200 and result.json()["status"] == "cancelled"
        else:
            stop_authorization()
    elif timing == "during_poll":
        protocol.on_poll = stop_authorization
    else:
        protocol.on_nav = stop_authorization
    assert poll(client, identifier)["status"] == terminal
    assert_not_installed(db, identifier, terminal)
    assert protocol.polled == (0 if timing == "before_poll" else 1)
    assert protocol.verified == (1 if timing == "during_nav" else 0)


def test_expired_admin_session_during_identity_check_cannot_bind_credentials(context, protocol):
    client, db, _ = context
    login(client)
    authorization = start(client)
    session_id = db.get(SourceAuthorization, authorization["id"]).session_id

    def expire_session():
        db.get(UserSession, session_id).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    protocol.on_nav = expire_session
    result = client.post(f"{AUTHORIZATIONS}/{authorization['id']}/poll")
    assert result.status_code == 401, result.text
    assert db.scalar(select(func.count()).select_from(SourceAccount)) == 0
    assert db.get(SourceAuthorization, authorization["id"]).status != "success"


def test_starting_another_qr_cancels_the_previous_one_and_wipes_its_ephemeral_key(context, protocol):
    client, db, _ = context
    login(client)
    first, second = start(client), start(client)
    assert first["id"] != second["id"]
    assert poll(client, first["id"])["status"] == "cancelled"
    assert_not_installed(db, first["id"], "cancelled")
    assert poll(client, second["id"])["status"] == "success"
    assert protocol.polled == 1


def test_manual_refresh_reuses_a_queued_job_and_cookie_only_accounts_report_requirement(context, protocol):
    client, db, _ = context
    login(client)
    account_id = create_account(client)["id"]
    first = client.post(f"{ACCOUNTS}/{account_id}/refresh")
    assert first.status_code == 200, first.text
    assert first.json()["kind"] == "refresh_credentials"
    assert first.json()["status"] == "queued"
    second = client.post(f"{ACCOUNTS}/{account_id}/refresh")
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    bare_id = create_account(client, refresh_token="")["id"]
    unsupported = client.post(f"{ACCOUNTS}/{bare_id}/refresh")
    assert unsupported.status_code == 422 and "刷新令牌" in unsupported.json()["detail"]
    assert protocol.generated == protocol.polled == protocol.verified == 0
    assert_private(first.json())
