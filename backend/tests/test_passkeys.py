"""Actual P-256/COSE/CBOR WebAuthn signatures; no authenticator or verifier mocks."""
import base64
import hashlib
import json
import os
from datetime import timedelta
from uuid import UUID

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import get_db
from app.models import Base, PasskeyChallenge, PasskeyCredential, User, UserSession, utcnow
from app.passkeys import CHALLENGE_COOKIE, router, consume_challenge
from app.security import COOKIE_NAME, create_session, hash_password

ORIGIN = "http://localhost:8788"
PASSWORD = "synthetic-passkey-password"


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class Authenticator:
    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        public = self.private.public_key().public_numbers()
        self.cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")})
        self.identifier = os.urandom(32)

    def response(self, options, user_id, *, register=False, uv=True, origin=ORIGIN, rp_id="localhost",
                 cross_origin=False, counter=1, bad_signature=False):
        data = json.dumps({"type": "webauthn.create" if register else "webauthn.get",
            "challenge": options["public_key"]["challenge"], "origin": origin, "crossOrigin": cross_origin}).encode()
        flags = 1 | (4 if uv else 0) | (64 if register else 0)
        auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + counter.to_bytes(4, "big")
        response = {"clientDataJSON": b64(data)}
        if register:
            auth_data += bytes(16) + len(self.identifier).to_bytes(2, "big") + self.identifier + self.cose
            response.update(attestationObject=b64(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})), transports=["internal"])
        else:
            signature = self.private.sign(auth_data + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
            response.update(authenticatorData=b64(auth_data), signature=b64(signature[:-1] + bytes([signature[-1] ^ 1])) if bad_signature else b64(signature),
                            userHandle=b64(UUID(user_id).bytes))
        return {"id": b64(self.identifier), "rawId": b64(self.identifier), "type": "public-key", "response": response}


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(settings, "passkeys_enabled", True)
    monkeypatch.setattr(settings, "passkey_origin", ORIGIN)
    monkeypatch.setattr(settings, "passkey_rp_id", "localhost")
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "passkey_limit", 50)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(router)
    with Session(engine, expire_on_commit=False) as db:
        user = User(username="owner", password_hash=hash_password(PASSWORD), role="admin")
        other = User(username="other", password_hash=hash_password(PASSWORD), role="reader")
        db.add_all([user, other]); db.commit()
        session, token = create_session(db, user); db.commit()
        def dependency():
            yield db
        app.dependency_overrides[get_db] = dependency
        with TestClient(app, base_url=ORIGIN) as client:
            client.cookies.set(COOKIE_NAME, token)
            client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": session.csrf_token})
            yield client, db, user, other
    engine.dispose()


def register(client, user, device=None):
    device = device or Authenticator()
    options = client.post("/api/v1/auth/passkeys/register/options", json={"password": PASSWORD, "name": "测试密钥"})
    assert options.status_code == 200, options.text
    data = options.json()
    assert data["public_key"]["authenticatorSelection"]["userVerification"] == "required"
    assert data["public_key"]["authenticatorSelection"]["residentKey"] == "required"
    result = client.post("/api/v1/auth/passkeys/register/verify", json={"challenge_id": data["challenge_id"], "name": "测试密钥",
        "credential": device.response(data, user.id, register=True)})
    assert result.status_code == 200, result.text
    return device, result.json()["item"]


def test_real_crypto_registration_login_and_revoke_invalidates_issued_sessions(context):
    client, db, user, _ = context
    device, item = register(client, user)
    listed = client.get("/api/v1/auth/passkeys").json()["items"]
    assert len(listed) == 1 and "public_key" not in listed[0] and "credential_id" not in listed[0]
    client.cookies.clear()
    options_response = client.post("/api/v1/auth/passkeys/login/options", json={})
    assert "HttpOnly" in options_response.headers["set-cookie"] and "SameSite=strict" in options_response.headers["set-cookie"]
    options = options_response.json()
    assert not options["public_key"].get("allowCredentials")  # No username or credential enumeration.
    verified = client.post("/api/v1/auth/passkeys/login/verify", json={"challenge_id": options["challenge_id"],
        "credential": device.response(options, user.id, counter=2)})
    assert verified.status_code == 200, verified.text
    assert verified.json()["user"]["id"] == user.id
    client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]
    assert db.scalar(select(PasskeyCredential.sign_count)) == 2
    assert db.scalar(select(UserSession).where(UserSession.passkey_credential_id == item["id"])) is not None
    # This outstanding registration challenge exercises ON DELETE CASCADE on session revocation.
    assert client.post("/api/v1/auth/passkeys/register/options", json={"password": PASSWORD}).status_code == 200
    revoked = client.request("DELETE", "/api/v1/auth/passkeys/" + item["id"], json={"password": PASSWORD})
    assert revoked.status_code == 200, revoked.text
    assert client.get("/api/v1/auth/passkeys").status_code == 401
    assert db.get(PasskeyCredential, item["id"]).revoked_at is not None
    assert db.scalar(select(UserSession).where(UserSession.passkey_credential_id == item["id"])) is None
    options = client.post("/api/v1/auth/passkeys/login/options", json={}).json()
    assert client.post("/api/v1/auth/passkeys/login/verify", json={"challenge_id": options["challenge_id"],
        "credential": device.response(options, user.id, counter=3)}).status_code == 401


@pytest.mark.parametrize("failure", ["uv", "origin", "rp", "cross_origin", "signature", "user_handle", "counter"])
def test_failed_authentication_is_cryptographically_rejected_and_challenge_consumed(context, failure):
    client, db, user, other = context
    device, _ = register(client, user)
    options = client.post("/api/v1/auth/passkeys/login/options", json={}).json()
    args = {"counter": 2}
    args.update({"uv": {"uv": False}, "origin": {"origin": "https://evil.invalid"}, "rp": {"rp_id": "evil.invalid"},
                 "cross_origin": {"cross_origin": True}, "signature": {"bad_signature": True}, "counter": {"counter": 1}}.get(failure, {}))
    reply = device.response(options, other.id if failure == "user_handle" else user.id, **args)
    body = {"challenge_id": options["challenge_id"], "credential": reply}
    assert client.post("/api/v1/auth/passkeys/login/verify", json=body).status_code == 401
    assert db.get(PasskeyChallenge, options["challenge_id"]).consumed_at is not None
    body["credential"] = device.response(options, user.id, counter=2)
    assert client.post("/api/v1/auth/passkeys/login/verify", json=body).status_code == 400
    assert db.scalar(select(PasskeyCredential.sign_count)) == 1


def test_registration_requires_existing_login_csrf_password_and_uv(context):
    client, db, user, _ = context
    assert client.post("/api/v1/auth/passkeys/register/options", json={"password": "incorrect"}).status_code == 403
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/auth/passkeys/register/options", json={"password": PASSWORD}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    options = client.post("/api/v1/auth/passkeys/register/options", json={"password": PASSWORD}).json()
    device = Authenticator()
    assert client.post("/api/v1/auth/passkeys/register/verify", json={"challenge_id": options["challenge_id"],
        "credential": device.response(options, user.id, register=True, uv=False)}).status_code == 400
    assert db.scalar(select(PasskeyCredential)) is None
    client.cookies.clear()
    assert client.post("/api/v1/auth/passkeys/register/options", json={"password": PASSWORD}).status_code == 401


def test_binding_expiration_origin_and_rate_limit(context, monkeypatch):
    client, db, user, _ = context
    device, _ = register(client, user)
    assert client.get("/api/v1/auth/passkeys/capabilities", params={"origin": ORIGIN}).json()["available"]
    assert not client.get("/api/v1/auth/passkeys/capabilities", params={"origin": "http://127.0.0.1:8788"}).json()["available"]
    assert client.post("/api/v1/auth/passkeys/login/options", json={}, headers={"Origin": "https://evil.invalid"}).status_code == 403
    options = client.post("/api/v1/auth/passkeys/login/options", json={}).json()
    challenge = db.get(PasskeyChallenge, options["challenge_id"])
    challenge.expires_at = utcnow() - timedelta(seconds=1); db.commit()
    assert client.post("/api/v1/auth/passkeys/login/verify", json={"challenge_id": options["challenge_id"],
        "credential": device.response(options, user.id, counter=2)}).status_code == 400
    options = client.post("/api/v1/auth/passkeys/login/options", json={}).json()
    client.cookies.clear()
    assert client.post("/api/v1/auth/passkeys/login/verify", json={"challenge_id": options["challenge_id"],
        "credential": device.response(options, user.id, counter=2)}).status_code == 400
    monkeypatch.setattr(settings, "passkey_limit", 2)
    assert client.post("/api/v1/auth/passkeys/login/options", json={}).status_code == 429


@pytest.mark.parametrize("race", ["disabled", "revoked", "expired"])
def test_registration_rechecks_account_and_session_after_signature(context, monkeypatch, race):
    import app.passkeys as service
    client, db, user, _ = context
    device = Authenticator()
    options = client.post("/api/v1/auth/passkeys/register/options", json={"password": PASSWORD}).json()
    original = service.verify_registration_response
    def verify_then_revoke(**kwargs):
        result = original(**kwargs)
        if race == "disabled":
            db.execute(update(User).where(User.id == user.id).values(disabled=True).execution_options(synchronize_session=False))
        elif race == "revoked":
            db.execute(delete(UserSession).execution_options(synchronize_session=False))
        else:
            db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)).execution_options(synchronize_session=False))
        db.commit()
        return result
    monkeypatch.setattr(service, "verify_registration_response", verify_then_revoke)
    result = client.post("/api/v1/auth/passkeys/register/verify", json={"challenge_id": options["challenge_id"],
        "credential": device.response(options, user.id, register=True)})
    assert result.status_code == 400
    assert db.scalar(select(PasskeyCredential)) is None


def test_registration_rejects_outer_id_substitution_and_password_typo_keeps_session(context):
    client, db, user, _ = context
    device, item = register(client, user)
    assert client.request("DELETE", "/api/v1/auth/passkeys/" + item["id"], json={"password": "incorrect"}).status_code == 403
    assert client.get("/api/v1/auth/passkeys").status_code == 200
    options = client.post("/api/v1/auth/passkeys/register/options", json={"password": PASSWORD}).json()
    reply = Authenticator().response(options, user.id, register=True)
    reply["id"] = reply["rawId"] = b64(os.urandom(32))
    assert client.post("/api/v1/auth/passkeys/register/verify", json={"challenge_id": options["challenge_id"], "credential": reply}).status_code == 400


@pytest.mark.parametrize("origin", ["http://localhost:abc", "https://[", "https://localhost:65536"])
def test_malformed_configuration_reports_unavailable(context, monkeypatch, origin):
    client, _, _, _ = context
    monkeypatch.setattr(settings, "passkey_origin", origin)
    assert client.get("/api/v1/auth/passkeys/capabilities").json()["available"] is False
    assert client.post("/api/v1/auth/passkeys/login/options", json={}).status_code == 503


def test_challenge_atomic_consumption_has_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace
    from fastapi import HTTPException
    engine = create_engine("sqlite:///" + str(tmp_path / "challenge.db"), connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    binding = b64(os.urandom(32))
    with Session(engine) as db:
        row = PasskeyChallenge(challenge=b64(os.urandom(32)), purpose="login", origin=settings.passkey_origin,
            rp_id=settings.passkey_rp_id, binding_hash=hashlib.sha256(binding.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(minutes=1))
        db.add(row); db.commit(); identifier = row.id
    barrier = Barrier(2)
    def consume():
        with Session(engine) as db:
            barrier.wait(timeout=5)
            try:
                consume_challenge(db, SimpleNamespace(cookies={CHALLENGE_COOKIE: binding}), identifier, "login")
                return "accepted"
            except HTTPException as error:
                assert error.status_code == 400
                return "consumed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: consume(), range(2))) == ["accepted", "consumed"]
    engine.dispose()
