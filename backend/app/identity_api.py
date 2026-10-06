"""Password-confirmed, revocable personal login tokens and account setup status."""
import hashlib
import secrets
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.library_access import allow_guest_access
from app.models import AuditLog, IdentityToken, User, UserSession, utcnow
from app.schemas import Input
from app.security import (COOKIE_NAME, authenticated, create_session, hash_password,
                          reserve_password_attempt, same_origin, verify_password)

router = APIRouter(prefix="/api/v1/auth")


class TokenLogin(Input):
    token: str = Field(min_length=20, max_length=256)


class TokenCreate(Input):
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    expires_days: int = Field(default=30, ge=1, le=90)


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


def token_view(item):
    return {key: getattr(item, key) for key in ("id", "name", "created_at", "expires_at", "last_used_at")}


def _user_lock(db, user_id):
    user = db.scalar(select(User).where(User.id == user_id).with_for_update(key_share=True)
                     .execution_options(populate_existing=True))
    if not user or user.disabled:
        raise HTTPException(401, "会话已失效")
    return user


def _response(response, user, session, token):
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure, samesite="strict",
                        max_age=max(1, int((session.expires_at - utcnow()).total_seconds())), path="/")
    return {"user": {"id": user.id, "username": user.username, "role": user.role}, "csrf_token": session.csrf_token}


def _audit(db, user, action, entity_type, entity_id=None):
    db.add(AuditLog(actor_id=user.id, action=action, entity_type=entity_type, entity_id=entity_id, details={}))


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {"initialized": bool(db.scalar(select(User.id).where(User.role == "admin").limit(1))),
            "allow_guest_access": allow_guest_access(db), "password_login": True,
            "passkeys_enabled": settings.passkeys_enabled}


@router.post("/token-login")
def token_login(body: TokenLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    same_origin(request)
    attempt = reserve_password_attempt(db, request)
    digest = hashlib.sha256(body.token.strip().encode()).hexdigest()
    hint = db.scalar(select(IdentityToken).where(IdentityToken.token_hash == digest))
    if not hint:
        raise HTTPException(401, "身份令牌无效或已过期")
    user = _user_lock(db, hint.user_id)
    item = db.scalar(select(IdentityToken).where(IdentityToken.id == hint.id)
                     .with_for_update().execution_options(populate_existing=True))
    if not item or item.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
        raise HTTPException(401, "身份令牌无效或已过期")
    session, token = create_session(db, user)
    session.identity_token_id = item.id
    session.expires_at = min(session.expires_at, item.expires_at.replace(tzinfo=timezone.utc))
    item.last_used_at = utcnow()
    attempt.succeeded = True
    _audit(db, user, "token_login", "session", session.id)
    db.commit()
    return _response(response, user, session, token)


@router.get("/identity-tokens")
def identity_tokens(identity=Depends(authenticated), db: Session = Depends(get_db)):
    items = db.scalars(select(IdentityToken).where(IdentityToken.user_id == identity[0].id)
                       .order_by(IdentityToken.created_at.desc()))
    return {"items": [{**token_view(item), "current_session": identity[1].identity_token_id == item.id} for item in items]}


@router.post("/identity-tokens", status_code=201)
def create_identity_token(body: TokenCreate, request: Request, identity=Depends(authenticated), db: Session = Depends(get_db)):
    same_origin(request)
    attempt = reserve_password_attempt(db, request)
    user = _user_lock(db, identity[0].id)
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "当前密码不正确")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请为令牌填写名称")
    # Expired sessions cannot be revived, and expired token records need not accumulate.
    db.execute(delete(IdentityToken).where(IdentityToken.user_id == user.id, IdentityToken.expires_at <= utcnow()))
    if db.scalar(select(func.count()).select_from(IdentityToken).where(IdentityToken.user_id == user.id)) >= 20:
        raise HTTPException(409, "最多保留 20 个有效令牌，请先撤销不用的令牌")
    token = "tul_" + secrets.token_urlsafe(40)
    item = IdentityToken(user_id=user.id, name=name, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                         expires_at=utcnow() + timedelta(days=body.expires_days))
    db.add(item)
    db.flush()
    attempt.succeeded = True
    _audit(db, user, "create", "identity_token", item.id)
    db.commit()
    return {"item": token_view(item), "token": token}


@router.delete("/identity-tokens/{token_id}", status_code=204)
def revoke_identity_token(token_id: str, request: Request, identity=Depends(authenticated), db: Session = Depends(get_db)):
    same_origin(request)
    user = _user_lock(db, identity[0].id)
    item = db.scalar(select(IdentityToken).where(IdentityToken.id == token_id, IdentityToken.user_id == user.id))
    if not item:
        raise HTTPException(404, "令牌不存在")
    db.execute(delete(UserSession).where(UserSession.identity_token_id == item.id))
    _audit(db, user, "revoke", "identity_token", item.id)
    db.delete(item)
    db.commit()


@router.post("/password")
def change_password(body: PasswordChange, request: Request, response: Response, identity=Depends(authenticated), db: Session = Depends(get_db)):
    same_origin(request)
    attempt = reserve_password_attempt(db, request)
    user = _user_lock(db, identity[0].id)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "当前密码不正确")
    user.password_hash = hash_password(body.new_password)
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.execute(delete(IdentityToken).where(IdentityToken.user_id == user.id))
    session, token = create_session(db, user)
    attempt.succeeded = True
    _audit(db, user, "change_password", "user", user.id)
    db.commit()
    return _response(response, user, session, token)
