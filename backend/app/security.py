import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import timedelta, timezone
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import LoginAttempt, User, UserSession, utcnow

COOKIE_NAME = "treasure_session"
_password_rate_mutex = threading.RLock()


@contextmanager
def password_attempt_lock(db, client_hash):
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(f"password-rate:{client_hash}".encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        yield
    else:
        with _password_rate_mutex:
            yield


def reserve_password_attempt(db, request):
    client = request.client.host if request.client else "unknown"
    digest = hashlib.sha256(client.encode()).hexdigest()
    with password_attempt_lock(db, digest):
        failures = db.scalar(select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.client_hash == digest,
            LoginAttempt.created_at >= utcnow() - timedelta(seconds=settings.login_window_seconds),
            LoginAttempt.succeeded.is_(False)))
        if failures >= settings.login_limit:
            db.rollback()
            raise HTTPException(429, "登录尝试过于频繁，请稍后再试",
                                headers={"Retry-After": str(settings.login_window_seconds)})
        # Reserve before scrypt so concurrent requests cannot all see free quota.
        attempt = LoginAttempt(client_hash=digest, succeeded=False)
        db.add(attempt)
        db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < utcnow() - timedelta(days=1)))
        db.commit()
    return attempt


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


def create_session(db: Session, user: User, *, passkey_credential_id=None):
    token = secrets.token_urlsafe(48)
    session = UserSession(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                          csrf_token=secrets.token_urlsafe(32), expires_at=utcnow() + timedelta(hours=settings.session_hours),
                          passkey_credential_id=passkey_credential_id)
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


def optional_identity(request: Request, db: Session = Depends(get_db)):
    """Public reads permit guests; a valid user's writes retain CSRF protection."""
    try:
        return authenticated(request, db)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        return None


def canonical_origin(value: str | None) -> str | None:
    if not value or len(value) > 2048 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if any(char in value for char in ("\\", "?", "#")):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in {"", "/"} or parsed.netloc.endswith(":")):
            return None
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            if len(hostname) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                               for label in hostname.split(".")):
                return None
        else:
            if "%" in hostname:
                return None
            hostname = f"[{address.compressed}]" if address.version == 6 else str(address)
        port = parsed.port
        if port == 0:
            return None
    except (ValueError, UnicodeError):
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{hostname}" + (f":{port}" if port is not None and port != default_port else "")


def trusted_request_origins(request: Request) -> set[str]:
    """TLS aliases must match the request Host, including any non-default port."""
    base = canonical_origin(str(request.base_url))
    if not base:
        return set()
    host = urlsplit(base).hostname
    request_port = urlsplit(str(request.base_url)).port
    trusted = set()
    for configured in settings.trusted_origins.split(","):
        origin = canonical_origin(configured.strip())
        if not origin:
            continue
        parsed = urlsplit(origin)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.hostname == host and (request_port == port if request_port is not None else parsed.port is None):
            trusted.add(origin)
    return trusted


def secure_cookie(request: Request) -> bool:
    if settings.cookie_secure:
        return True
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    trusted = trusted_request_origins(request)
    supplied = request.headers.get("origin")
    if supplied is None:
        return any(origin.startswith("https://") for origin in trusted)
    origin = canonical_origin(supplied)
    return bool(origin and origin.startswith("https://") and origin in trusted)


def same_origin(request: Request):
    supplied = request.headers.get("origin")
    origin = canonical_origin(supplied)
    allowed = {canonical_origin(str(request.base_url)), *trusted_request_origins(request)}
    allowed.discard(None)
    from app.passkeys import site_policy
    try:
        configured = canonical_origin(site_policy()[0])
        if configured:
            allowed.add(configured)
    except HTTPException:
        pass
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site" or (supplied is not None and origin not in allowed):
        raise HTTPException(403, "请求来源不匹配")


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
