import hashlib
import hmac
import secrets
from datetime import timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User, UserSession, utcnow

COOKIE_NAME = "treasure_session"


def encrypt_secret(text: str) -> str:
    key = settings.encryption_key()
    if not key:
        raise ValueError("请先配置独立的 TREASURE_SECRET_KEY")
    return Fernet(key.encode()).encrypt(text.encode()).decode()


def decrypt_secret(text: str) -> str:
    try:
        return Fernet(settings.encryption_key().encode()).decrypt(text.encode()).decode()
    except (ValueError, InvalidToken) as exc:
        raise ValueError("凭据无法解密，请检查部署密钥") from exc


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise ValueError("密码长度须为12至256个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, n, r, p, salt, digest = encoded.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(candidate.hex(), digest)
    except (ValueError, TypeError):
        return False


def create_session(db: Session, user: User):
    token = secrets.token_urlsafe(48)
    session = UserSession(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                          csrf_token=secrets.token_urlsafe(32), expires_at=utcnow() + timedelta(hours=settings.session_hours))
    db.add(session)
    db.flush()
    return session, token


def authenticated(request: Request, db: Session = Depends(get_db)) -> tuple[User, UserSession]:
    token = request.cookies.get(COOKIE_NAME, "")
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hashlib.sha256(token.encode()).hexdigest())) if token else None
    if not session or session.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
        raise HTTPException(401, "请先登录")
    user = db.get(User, session.user_id)
    if not user or user.disabled:
        raise HTTPException(401, "会话已失效")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf_token):
            raise HTTPException(403, "操作校验失败，请刷新后重试")
    return user, session


def require_admin(identity=Depends(authenticated)) -> User:
    user, _ = identity
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def require_editor(identity=Depends(authenticated)) -> User:
    user, _ = identity
    if user.role not in {"admin", "editor"}:
        raise HTTPException(403, "需要编辑权限")
    return user
