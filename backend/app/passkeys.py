"""WebAuthn ceremonies: fixed RP/origin, one-use challenges, UV and user binding."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session
from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from app.config import settings
from app.db import get_db
from app.models import (AuditLog, PasskeyAttempt, PasskeyChallenge, PasskeyCredential, User, UserSession, utcnow)
from app.security import COOKIE_NAME, authenticated, create_session, verify_password

router = APIRouter(prefix="/api/v1/auth/passkeys", tags=["passkeys"])
CHALLENGE_COOKIE = "treasure_passkey_challenge"
COOKIE_PATH = "/api/v1/auth/passkeys"
_rate_mutex = threading.RLock()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(Input):
    pass


class PasswordInput(Input):
    password: str = Field(min_length=1, max_length=256)


class RegistrationInput(PasswordInput):
    name: str = Field(default="我的通行密钥", min_length=1, max_length=100)


class VerificationInput(Input):
    challenge_id: str = Field(min_length=36, max_length=36)
    credential: dict


class RegistrationVerificationInput(VerificationInput):
    name: str = Field(default="我的通行密钥", min_length=1, max_length=100)


def _b64(value: bytes):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value, *, maximum=65536):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid encoded field")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _b64(raw) != value:
        raise ValueError("Noncanonical encoded field")
    return raw


def site_policy():
    origin = settings.passkey_origin.rstrip("/")
    rp_id = settings.passkey_rp_id.lower()
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        raise HTTPException(503, "通行密钥站点地址或端口无效") from None
    invalid = (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
               or parsed.path or parsed.query or parsed.fragment or parsed.hostname != rp_id)
    try:
        ipaddress.ip_address(rp_id)
        invalid = True  # Use localhost for local development, a DNS RP for deployment.
    except ValueError:
        pass
    if parsed.scheme == "http" and parsed.hostname != "localhost":
        invalid = True
    if invalid:
        raise HTTPException(503, "通行密钥配置无效：请使用 HTTPS 域名或 http://localhost，RP ID 须与域名一致")
    return origin, rp_id


def _require_origin(request):
    if not settings.passkeys_enabled:
        raise HTTPException(503, "通行密钥功能未启用")
    origin, rp_id = site_policy()
    if request.headers.get("origin") != origin:
        raise HTTPException(403, "通行密钥请求来源不匹配，请使用配置的站点地址")
    return origin, rp_id


@router.get("/capabilities")
def capabilities(request: Request, origin: str | None = Query(None, max_length=500)):
    observed = origin or request.headers.get("origin") or str(request.base_url).rstrip("/")
    reason = None
    try:
        configured, rp_id = site_policy()
        if not settings.passkeys_enabled:
            reason = "通行密钥功能未启用"
        elif observed != configured:
            reason = "当前访问地址与通行密钥站点不一致，请使用 " + configured
    except HTTPException as error:
        configured, rp_id, reason = settings.passkey_origin, settings.passkey_rp_id, error.detail
    return {"enabled": settings.passkeys_enabled, "available": reason is None, "rp_id": rp_id,
            "rp_name": settings.passkey_rp_name, "origin": configured, "reason": reason, "password_login": True}


@contextmanager
def _limit_lock(db, client_hash, purpose):
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(f"passkey-rate:{client_hash}:{purpose}".encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        yield
    else:
        with _rate_mutex:
            yield


def reserve_attempt(db, request, purpose):
    client = request.client.host if request.client else "unknown"
    digest = hashlib.sha256(client.encode()).hexdigest()
    with _limit_lock(db, digest, purpose):
        since = utcnow() - timedelta(seconds=settings.passkey_window_seconds)
        count = db.scalar(select(func.count()).select_from(PasskeyAttempt).where(
            PasskeyAttempt.client_hash == digest, PasskeyAttempt.purpose == purpose, PasskeyAttempt.created_at >= since))
        if count >= settings.passkey_limit:
            db.rollback()
            raise HTTPException(429, "尝试过于频繁，请稍后再试")
        db.add(PasskeyAttempt(client_hash=digest, purpose=purpose))
        db.execute(delete(PasskeyAttempt).where(PasskeyAttempt.created_at < utcnow() - timedelta(days=1)))
        db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at < utcnow() - timedelta(days=1)))
        db.commit()  # Reserve before expensive verification; failures also consume quota.


def _new_challenge(db, response, *, purpose, origin, rp_id, user=None, session=None):
    lifetime = max(30, min(600, settings.passkey_challenge_seconds))
    challenge, binding = secrets.token_bytes(32), secrets.token_urlsafe(32)
    row = PasskeyChallenge(challenge=_b64(challenge), purpose=purpose, user_id=user.id if user else None,
        session_id=session.id if session else None, binding_hash=hashlib.sha256(binding.encode()).hexdigest(),
        origin=origin, rp_id=rp_id, expires_at=utcnow() + timedelta(seconds=lifetime))
    db.add(row); db.commit(); db.refresh(row)
    response.set_cookie(CHALLENGE_COOKIE, binding, httponly=True, secure=origin.startswith("https://"),
                        samesite="strict", max_age=lifetime, path=COOKIE_PATH)
    return row, challenge, lifetime


def consume_challenge(db, request, identifier, purpose, *, user=None, session=None):
    binding = request.cookies.get(CHALLENGE_COOKIE, "")
    if not binding or len(binding) > 128:
        raise HTTPException(400, "验证请求无效或已过期，请重新开始")
    clauses = [PasskeyChallenge.id == identifier, PasskeyChallenge.purpose == purpose,
        PasskeyChallenge.binding_hash == hashlib.sha256(binding.encode()).hexdigest(),
        PasskeyChallenge.consumed_at.is_(None), PasskeyChallenge.expires_at > utcnow()]
    clauses.extend([PasskeyChallenge.user_id == user.id, PasskeyChallenge.session_id == session.id] if user and session
                   else [PasskeyChallenge.user_id.is_(None), PasskeyChallenge.session_id.is_(None)])
    row = db.execute(update(PasskeyChallenge).where(*clauses).values(consumed_at=utcnow()).returning(
        PasskeyChallenge.challenge, PasskeyChallenge.origin, PasskeyChallenge.rp_id)).mappings().first()
    result = dict(row) if row else None
    db.commit()  # One-use even when the following signature/UV check fails.
    if not result:
        raise HTTPException(400, "验证请求无效或已过期，请重新开始")
    if (result["origin"], result["rp_id"]) != site_policy():
        raise HTTPException(400, "站点配置已变化，请重新开始验证")
    return result


def _credential_shape(credential):
    if len(json.dumps(credential)) > 200000:
        raise ValueError("Credential too large")
    credential_id = _unb64(credential.get("id"), maximum=2048)
    if _unb64(credential.get("rawId"), maximum=2048) != credential_id or credential.get("type") != "public-key":
        raise ValueError("Invalid credential identifier")
    client_data = json.loads(_unb64(credential["response"]["clientDataJSON"]))
    if client_data.get("crossOrigin", False) is not False or client_data.get("topOrigin") is not None:
        raise ValueError("Cross-origin ceremony is forbidden")
    return credential_id


def _view(credential):
    return {"id": credential.id, "name": credential.name, "created_at": credential.created_at,
        "last_used_at": credential.last_used_at, "transports": credential.transports,
        "device_type": credential.device_type, "backed_up": credential.backed_up}


def _audit(db, user, action, identifier=None):
    db.add(AuditLog(actor_id=user.id, action=action, entity_type="passkey", entity_id=identifier))


@router.post("/login/options")
def login_options(body: EmptyInput, request: Request, response: Response, db: Session = Depends(get_db)):
    origin, rp_id = _require_origin(request)
    reserve_attempt(db, request, "login_options")
    row, challenge, lifetime = _new_challenge(db, response, purpose="login", origin=origin, rp_id=rp_id)
    options = generate_authentication_options(rp_id=rp_id, challenge=challenge, timeout=lifetime * 1000,
        user_verification=UserVerificationRequirement.REQUIRED)
    return {"challenge_id": row.id, "public_key": json.loads(options_to_json(options)), "expires_in": lifetime}


@router.post("/login/verify")
def login_verify(body: VerificationInput, request: Request, response: Response, db: Session = Depends(get_db)):
    _require_origin(request)
    reserve_attempt(db, request, "login_verify")
    challenge = consume_challenge(db, request, body.challenge_id, "login")
    try:
        raw_id = _credential_shape(body.credential)
        credential = db.scalar(select(PasskeyCredential).where(PasskeyCredential.credential_id == _b64(raw_id),
            PasskeyCredential.revoked_at.is_(None)).with_for_update().execution_options(populate_existing=True))
        user = db.get(User, credential.user_id) if credential else None
        if not user or user.disabled:
            raise ValueError("Credential unavailable")
        handle = _unb64(body.credential["response"].get("userHandle"), maximum=128)
        if not hmac.compare_digest(handle, UUID(user.id).bytes):
            raise ValueError("User binding mismatch")
        verified = verify_authentication_response(credential=body.credential,
            expected_challenge=_unb64(challenge["challenge"]), expected_origin=challenge["origin"], expected_rp_id=challenge["rp_id"],
            credential_public_key=_unb64(credential.public_key), credential_current_sign_count=credential.sign_count,
            require_user_verification=True)
        if (not verified.user_verified or verified.credential_id != raw_id
                or verified.credential_device_type.value != credential.device_type):
            raise ValueError("User verification missing")
        user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
        if not user or user.disabled:
            raise ValueError("Account no longer available")
        credential.sign_count, credential.last_used_at = verified.new_sign_count, utcnow()
        credential.backed_up = verified.credential_backed_up
        session, token = create_session(db, user, passkey_credential_id=credential.id)
        _audit(db, user, "passkey_login", credential.id)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(401, "通行密钥验证失败，请重试或使用密码登录") from None
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure, samesite="strict",
                        max_age=settings.session_hours * 3600, path="/")
    response.delete_cookie(CHALLENGE_COOKIE, path=COOKIE_PATH)
    return {"user": {"id": user.id, "username": user.username, "role": user.role}, "csrf_token": session.csrf_token}


@router.get("")
def list_passkeys(identity=Depends(authenticated), db: Session = Depends(get_db)):
    rows = db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == identity[0].id,
        PasskeyCredential.revoked_at.is_(None)).order_by(PasskeyCredential.created_at))
    return {"items": [_view(row) for row in rows]}


@router.post("/register/options")
def register_options(body: RegistrationInput, request: Request, response: Response,
                     identity=Depends(authenticated), db: Session = Depends(get_db)):
    origin, rp_id = _require_origin(request)
    reserve_attempt(db, request, "register")
    user, session = identity
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "当前密码不正确")
    active = list(db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id, PasskeyCredential.revoked_at.is_(None))))
    if len(active) >= 10:
        raise HTTPException(409, "最多保留 10 个通行密钥，请先撤销不再使用的密钥")
    row, challenge, lifetime = _new_challenge(db, response, purpose="register", origin=origin, rp_id=rp_id, user=user, session=session)
    options = generate_registration_options(rp_id=rp_id, rp_name=settings.passkey_rp_name,
        user_id=UUID(user.id).bytes, user_name=user.username, user_display_name=user.username,
        challenge=challenge, timeout=lifetime * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=_unb64(item.credential_id)) for item in active])
    return {"challenge_id": row.id, "public_key": json.loads(options_to_json(options)), "expires_in": lifetime}


@router.post("/register/verify")
def register_verify(body: RegistrationVerificationInput, request: Request, response: Response,
                    identity=Depends(authenticated), db: Session = Depends(get_db)):
    _require_origin(request)
    reserve_attempt(db, request, "register_verify")
    user, session = identity
    challenge = consume_challenge(db, request, body.challenge_id, "register", user=user, session=session)
    try:
        raw_id = _credential_shape(body.credential)
        verified = verify_registration_response(credential=body.credential,
            expected_challenge=_unb64(challenge["challenge"]), expected_origin=challenge["origin"], expected_rp_id=challenge["rp_id"],
            require_user_presence=True, require_user_verification=True)
        if verified.credential_id != raw_id:
            raise ValueError("Attested credential identifier mismatch")
        # Serialize registrations and credential changes without blocking audit
        # actor FK checks from a concurrent passkey/session revocation.
        user = db.scalar(select(User).where(User.id == user.id).with_for_update(key_share=True).execution_options(populate_existing=True))
        current_session = db.scalar(select(UserSession).where(UserSession.id == session.id,
            UserSession.user_id == user.id).with_for_update().execution_options(populate_existing=True)) if user else None
        if (not user or user.disabled or not current_session
                or current_session.expires_at.replace(tzinfo=timezone.utc) <= utcnow()):
            raise ValueError("Registration session no longer valid")
        count = db.scalar(select(func.count()).select_from(PasskeyCredential).where(
            PasskeyCredential.user_id == user.id, PasskeyCredential.revoked_at.is_(None)))
        if count >= 10 or user.disabled:
            raise ValueError("Account cannot register another credential")
        transports = body.credential.get("response", {}).get("transports", [])
        allowed = {"usb", "nfc", "ble", "internal", "hybrid", "smart-card"}
        transports = [value for value in transports if isinstance(value, str) and value in allowed][:8] if isinstance(transports, list) else []
        item = PasskeyCredential(user_id=user.id, credential_id=_b64(verified.credential_id),
            public_key=_b64(verified.credential_public_key), sign_count=verified.sign_count, name=body.name.strip() or "通行密钥",
            transports=transports, aaguid=str(verified.aaguid), device_type=verified.credential_device_type.value,
            backed_up=verified.credential_backed_up)
        db.add(item); db.flush()
        _audit(db, user, "passkey_registered", item.id)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(400, "通行密钥注册失败，请重新开始") from None
    response.delete_cookie(CHALLENGE_COOKIE, path=COOKIE_PATH)
    return {"item": _view(item)}


@router.delete("/{credential_id}")
def revoke_passkey(credential_id: str, body: PasswordInput, request: Request,
                   identity=Depends(authenticated), db: Session = Depends(get_db)):
    _require_origin(request)
    reserve_attempt(db, request, "revoke")
    user = identity[0]
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "当前密码不正确")
    item = db.scalar(select(PasskeyCredential).where(PasskeyCredential.id == credential_id,
        PasskeyCredential.user_id == user.id, PasskeyCredential.revoked_at.is_(None)).with_for_update())
    if not item:
        raise HTTPException(404, "通行密钥不存在")
    item.revoked_at = utcnow()
    db.execute(delete(UserSession).where(UserSession.passkey_credential_id == item.id))
    _audit(db, user, "passkey_revoked", item.id)
    db.commit()
    return {"ok": True}
