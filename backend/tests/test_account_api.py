from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import PasskeyCredential, User
from app.security import COOKIE_NAME
from test_api import context, login

PASSWORD = "a-test-password-123"
REPLACEMENT = "replacement-password-123"


def test_retired_personal_token_routes_are_unavailable(context):
    client, _, _ = context
    for logged_in in (False, True):
        if logged_in:
            login(client)
        assert client.post("/api/v1/auth/token-login", json={"token": "tul_test-only"}).status_code == 404
        assert client.get("/api/v1/auth/identity-tokens").status_code == 404
        assert client.post("/api/v1/auth/identity-tokens", json={"password": PASSWORD}).status_code == 404
    assert "identity-token" not in client.get("/api/v1/server").json()["authentication"]
    assert client.get("/api/v1/auth/passkeys/capabilities").status_code == 200


def test_password_change_rotates_sessions_and_preserves_passkeys(context):
    client, db, _ = context
    login(client)
    owner = db.scalar(select(User).where(User.username == "admin"))
    key = PasskeyCredential(user_id=owner.id, credential_id="fixture", public_key="fixture", name="my passkey")
    db.add(key); db.commit()
    old_cookie = client.cookies.get(COOKIE_NAME)
    result = client.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": REPLACEMENT})
    assert result.status_code == 200 and client.cookies.get(COOKIE_NAME) != old_cookie
    assert client.get("/api/v1/auth/me").status_code == 200
    assert db.get(PasskeyCredential, key.id) is not None
    with TestClient(app, base_url="http://localhost") as device:
        assert device.get("/api/v1/auth/me", headers={"Cookie": f"{COOKIE_NAME}={old_cookie}"}).status_code == 401
        assert device.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 401
        assert device.post("/api/v1/auth/login", json={"username": "admin", "password": REPLACEMENT}).status_code == 200


def test_password_change_requires_current_password_origin_and_csrf(context):
    client, _, _ = context
    body = {"current_password": PASSWORD, "new_password": REPLACEMENT}
    assert client.post("/api/v1/auth/password", json=body).status_code == 401
    login(client)
    assert client.post("/api/v1/auth/password", json={**body, "current_password": "wrong"}).status_code == 403
    assert client.post("/api/v1/auth/password", json={**body, "new_password": "short"}).status_code == 422
    assert client.post("/api/v1/auth/password", json=body, headers={"Origin": "https://unrelated.invalid"}).status_code == 403
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/auth/password", json=body).status_code == 403


def test_password_change_failures_share_login_rate_limit(context, monkeypatch):
    client, _, _ = context
    login(client)
    monkeypatch.setattr(settings, "login_limit", 2)
    # The successful login is deliberately not charged as a failed attempt.
    for _ in range(2):
        assert client.post("/api/v1/auth/password", json={"current_password": "wrong", "new_password": REPLACEMENT}).status_code == 403
    result = client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert result.status_code == 429 and int(result.headers["Retry-After"]) > 0
