from datetime import timedelta
import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import settings
from app.main import app
from app.models import AuditLog, IdentityToken, User, UserSession, utcnow
from app.security import COOKIE_NAME
from test_api import context, login

PASSWORD = "a-test-password-123"


def issue(client, **kwargs):
    result = client.post("/api/v1/auth/identity-tokens", json={"name": "iPad", "password": PASSWORD, **kwargs})
    assert result.status_code == 201, result.text
    return result.json()


def test_token_is_hashed_scoped_revocable_and_never_listed(context):
    client, db, _ = context
    login(client, "reader")
    data = issue(client)
    stored = db.get(IdentityToken, data["item"]["id"])
    assert stored.token_hash == hashlib.sha256(data["token"].encode()).hexdigest()
    assert data["token"] not in client.get("/api/v1/auth/identity-tokens").text
    assert all(data["token"] not in str(row.details) for row in db.scalars(select(AuditLog)))
    with TestClient(app, base_url="http://localhost") as device:
        logged = device.post("/api/v1/auth/token-login", json={"token": data["token"]})
        assert logged.status_code == 200 and logged.json()["user"]["role"] == "reader"
        assert "HttpOnly" in logged.headers["set-cookie"]
        assert device.get("/api/v1/admin/users").status_code == 403
        assert device.post("/api/v1/auth/logout").status_code == 403
        assert client.delete(f'/api/v1/auth/identity-tokens/{stored.id}').status_code == 204
        assert device.get("/api/v1/auth/me").status_code == 401
        assert device.post("/api/v1/auth/token-login", json={"token": data["token"]}).status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200


def test_expiry_ownership_csrf_password_origin_and_integration_token(context, monkeypatch):
    client, db, _ = context
    login(client)
    token = issue(client, expires_days=1)
    item = db.get(IdentityToken, token["item"]["id"])
    with TestClient(app, base_url="http://localhost") as device:
        assert device.post("/api/v1/auth/token-login", json={"token": token["token"]},
                           headers={"Origin": "https://evil.invalid"}).status_code == 403
        result = device.post("/api/v1/auth/token-login", json={"token": token["token"]})
        assert result.status_code == 200
        created = db.scalar(select(UserSession).where(UserSession.identity_token_id == item.id))
        assert created.expires_at.replace(tzinfo=item.expires_at.tzinfo) <= item.expires_at
        item.expires_at = utcnow() - timedelta(seconds=1); db.commit()
        assert device.post("/api/v1/auth/token-login", json={"token": token["token"]}).status_code == 401
        # Scoped ingestion credentials must never become full account credentials.
        assert device.post("/api/v1/auth/token-login", json={"token": "tui_" + "x" * 50}).status_code == 401
    login(client, "reader")
    assert client.delete(f'/api/v1/auth/identity-tokens/{item.id}').status_code == 404
    assert client.post("/api/v1/auth/identity-tokens", json={"name": "x", "password": "wrong"}).status_code == 403
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/auth/identity-tokens", json={"name": "x", "password": PASSWORD}).status_code == 403


def test_password_change_rotates_current_session_and_revokes_tokens(context):
    client, db, _ = context
    login(client)
    token = issue(client)
    old_cookie = client.cookies.get(COOKIE_NAME)
    result = client.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": "replacement-password-123"})
    assert result.status_code == 200 and client.cookies.get(COOKIE_NAME) != old_cookie
    assert client.get("/api/v1/auth/me").status_code == 200
    with TestClient(app, base_url="http://localhost") as device:
        assert device.get("/api/v1/auth/me", headers={"Cookie": f"{COOKIE_NAME}={old_cookie}"}).status_code == 401
        assert device.post("/api/v1/auth/token-login", json={"token": token["token"]}).status_code == 401
        assert device.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 401
        assert device.post("/api/v1/auth/login", json={"username": "admin", "password": "replacement-password-123"}).status_code == 200


def test_admin_password_reset_retires_identity_tokens(context):
    client, db, _ = context
    login(client, "reader")
    token = issue(client)
    user = db.scalar(select(User).where(User.username == "reader"))
    login(client)
    assert client.patch(f"/api/v1/admin/users/{user.id}", json={"password": "replacement-password-123"}).status_code == 200
    assert client.post("/api/v1/auth/token-login", json={"token": token["token"]}).status_code == 401


def test_token_login_rate_is_shared_with_password_login(context, monkeypatch):
    client, _, _ = context
    monkeypatch.setattr(settings, "login_limit", 2)
    for _ in range(2):
        assert client.post("/api/v1/auth/token-login", json={"token": "tul_" + "x" * 50}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 429
