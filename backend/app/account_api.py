"""Account initialization status and password changes; passkeys live in passkeys.py."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.library_access import allow_guest_access
from app.models import AuditLog, User, UserSession, utcnow
from app.schemas import Input
from app.security import (COOKIE_NAME, authenticated, create_session, hash_password,
                          reserve_password_attempt, same_origin, secure_cookie, verify_password)

router = APIRouter(prefix="/api/v1/auth")


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {"initialized": bool(db.scalar(select(User.id).where(User.role == "admin").limit(1))),
            "allow_guest_access": allow_guest_access(db), "password_login": True,
            "passkeys_enabled": settings.passkeys_enabled}


@router.post("/password")
def change_password(body: PasswordChange, request: Request, response: Response,
                    identity=Depends(authenticated), db: Session = Depends(get_db)):
    same_origin(request)
    attempt = reserve_password_attempt(db, request)
    user = db.scalar(select(User).where(User.id == identity[0].id).with_for_update(key_share=True)
                     .execution_options(populate_existing=True))
    if not user or user.disabled:
        raise HTTPException(401, "会话已失效")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "当前密码不正确")
    user.password_hash = hash_password(body.new_password)
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    session, token = create_session(db, user)
    attempt.succeeded = True
    db.add(AuditLog(actor_id=user.id, action="change_password", entity_type="user", entity_id=user.id, details={}))
    db.commit()
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=secure_cookie(request), samesite="strict",
                        max_age=max(1, int((session.expires_at - utcnow()).total_seconds())), path="/")
    return {"user": {"id": user.id, "username": user.username, "role": user.role}, "csrf_token": session.csrf_token}
