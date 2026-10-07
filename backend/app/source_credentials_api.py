"""Administrator-only, session-bound Bilibili authorization; secrets never return."""
import json
from contextlib import contextmanager
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import catalog
from app.db import get_db
from app.ingest.client import BiliClient
from app.ingest.errors import IngestDeferred, IngestError
from app.ingest.passport import BiliPassport, Credential
from app.ingest.runner import _lock, credential_lock
from app.models import AuditLog, SourceAccount, SourceAuthorization, User, UserSession, utcnow
from app.schemas import Input
from app.security import authenticated, decrypt_secret, encrypt_secret, same_origin
from app.source_credentials import account_view, enqueue_refresh, expected_uid, generation, install_credential, utc

router = APIRouter(prefix="/api/v1/admin/accounts", dependencies=[Depends(same_origin)])
QR_SECONDS = 180
POLL_SECONDS = 3
ACTIVE = ("pending", "scanned", "validating")


class AuthorizationInput(Input):
    name: str = Field(min_length=1, max_length=200)
    account_id: str | None = Field(default=None, min_length=1, max_length=36)


def admin_session(identity=Depends(authenticated)):
    user, session = identity
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user.id, session.id


@contextmanager
def mutex(db, key):
    try:
        with _lock(db, key):
            yield
    except IngestDeferred:
        db.rollback()
        raise HTTPException(409, "此操作正在处理中，请稍后重试", headers={"Retry-After": "3"}) from None


def identity_current(db, identity):
    user_id, session_id = identity
    session = db.scalar(select(UserSession.id).join(User, User.id == UserSession.user_id).where(
        UserSession.id == session_id, UserSession.user_id == user_id, UserSession.expires_at > utcnow(),
        User.disabled.is_(False), User.role == "admin"))
    if not session:
        raise HTTPException(401, "管理员会话已失效，请重新登录")


def owned(db, identifier, identity):
    identity_current(db, identity)
    row = db.scalar(select(SourceAuthorization).where(SourceAuthorization.id == identifier,
        SourceAuthorization.user_id == identity[0], SourceAuthorization.session_id == identity[1])
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "授权已过期或不存在")
    if row.status in ACTIVE and utc(row.expires_at) <= utcnow():
        finish(row, "expired")
        db.commit()
    return row


def finish(row, status, code=None):
    row.status, row.error_code = status, code
    row.key_encrypted = row.credential_encrypted = None


def poll_view(row, *, account=None, message=None):
    result = {"id": row.id, "status": row.status, "expires_at": utc(row.expires_at),
              "poll_after_seconds": POLL_SECONDS}
    if account:
        result["account"] = account_view(account)
    if message:
        result["message"] = message
    elif row.error_code == "account_mismatch":
        result["message"] = "扫码账号与当前 B站账号不一致，请使用原账号重新扫码。"
    elif row.error_code:
        result["message"] = "授权未能完成，请重新生成二维码。"
    return result


@router.post("/authorizations", status_code=201)
def start_authorization(body: AuthorizationInput, identity=Depends(admin_session), db: Session = Depends(get_db)):
    with mutex(db, "source-authorization-user:" + identity[0]):
        count = db.scalar(select(func.count()).select_from(SourceAuthorization).where(
            SourceAuthorization.user_id == identity[0], SourceAuthorization.created_at > utcnow() - timedelta(minutes=1)))
        if count >= 5:
            raise HTTPException(429, "二维码生成过于频繁，请稍后再试", headers={"Retry-After": "60"})
        account = db.get(SourceAccount, body.account_id) if body.account_id else None
        if body.account_id and account is None:
            raise HTTPException(404, "采集账号不存在")
        if account and account.status == "disabled":
            raise HTTPException(409, "采集账号已停用")
        for previous in db.scalars(select(SourceAuthorization).where(SourceAuthorization.session_id == identity[1],
                SourceAuthorization.status.in_(ACTIVE)).with_for_update()):
            finish(previous, "cancelled")
        row = SourceAuthorization(user_id=identity[0], session_id=identity[1], name=body.name,
            account_id=body.account_id, account_generation=generation(account) if account else None,
            status="pending", expires_at=utcnow() + timedelta(seconds=QR_SECONDS))
        db.add(row)
        db.commit()
        identifier = row.id
        db.rollback()
        try:
            with BiliPassport() as passport:
                qr = passport.generate_qrcode()
            row = owned(db, identifier, identity)
            if row.status != "pending":
                raise HTTPException(409, "授权已取消或过期")
            row.key_encrypted = encrypt_secret(qr.qrcode_key)
            db.add(AuditLog(actor_id=identity[0], action="start_authorization", entity_type="source_account",
                            entity_id=body.account_id))
            db.commit()
            return {**poll_view(row), "url": qr.url}
        except (IngestError, ValueError):
            db.rollback()
            row = owned(db, identifier, identity)
            finish(row, "error", "generate_failed")
            db.commit()
            raise HTTPException(502, "暂时无法生成 B站登录二维码，请稍后再试") from None


@router.post("/authorizations/{authorization_id}/cancel")
def cancel_authorization(authorization_id: str, identity=Depends(admin_session), db: Session = Depends(get_db)):
    # A cancellation can land during a slow poll. The poll rechecks this durable
    # flag after HTTP and immediately before binding credentials.
    row = owned(db, authorization_id, identity)
    if row.status in ACTIVE:
        finish(row, "cancelled")
        db.commit()
    return poll_view(row)


@router.post("/authorizations/{authorization_id}/poll")
def poll_authorization(authorization_id: str, identity=Depends(admin_session), db: Session = Depends(get_db)):
    with mutex(db, "source-authorization:" + authorization_id):
        row = owned(db, authorization_id, identity)
        if row.status not in ACTIVE:
            account = db.get(SourceAccount, row.account_id) if row.status == "success" else None
            return poll_view(row, account=account)
        if row.last_polled_at and utc(row.last_polled_at) > utcnow() - timedelta(seconds=POLL_SECONDS):
            return poll_view(row)
        row.last_polled_at = utcnow()
        key, encoded = row.key_encrypted, row.credential_encrypted
        db.commit()
        db.rollback()
        if not encoded:
            try:
                with BiliPassport() as passport:
                    outcome = passport.poll_qrcode(decrypt_secret(key or ""))
            except ValueError:
                raise HTTPException(503, "授权凭据无法读取，请检查部署密钥") from None
            except IngestError:
                row = owned(db, authorization_id, identity)
                return poll_view(row, message="暂时无法取得扫码状态，稍后继续检查。")
            row = owned(db, authorization_id, identity)
            if row.status not in ACTIVE:
                return poll_view(row)
            if outcome.status != "success":
                if outcome.status == "expired":
                    finish(row, "expired")
                else:
                    row.status = outcome.status
                db.commit()
                return poll_view(row)
            credential = outcome.credential
            row.credential_encrypted = encrypt_secret(json.dumps({
                "cookie_text": credential.cookie_text, "refresh_token": credential.refresh_token,
                "uid": credential.uid, "bili_jct": credential.bili_jct}))
            row.status, row.key_encrypted = "validating", None
            db.commit()  # Do not lose a consumed QR login if nav is temporarily unavailable.
            encoded = row.credential_encrypted
            db.rollback()
        try:
            credential = Credential(**json.loads(decrypt_secret(encoded)))
        except (ValueError, TypeError):
            raise HTTPException(503, "授权凭据无法读取，请重新扫码") from None
        client = BiliClient(credential.cookie_text)
        try:
            nav = client.nav()
        except IngestError as error:
            row = owned(db, authorization_id, identity)
            if row.status in ACTIVE and error.code in {"login_required", "invalid_cookie"}:
                finish(row, "error", "verify_failed")
                db.commit()
            return poll_view(row, message="正在确认 B站账号身份，请稍候。" if row.status in ACTIVE else None)
        finally:
            client.close()
        row = owned(db, authorization_id, identity)
        if row.status not in ACTIVE:
            return poll_view(row)
        account_id = row.account_id
        if str(nav.get("mid")) != credential.uid:
            finish(row, "error", "account_mismatch")
            db.commit()
            return poll_view(row)
        with credential_lock(db, account_id or "new-authorization:" + authorization_id):
            # Lock the authorization row only for the final commit. A concurrent
            # cancel must finish either before binding or after a completed login.
            db.refresh(row, with_for_update=True)
            identity_current(db, identity)
            if row.status not in ACTIVE or utc(row.expires_at) <= utcnow():
                if row.status in ACTIVE:
                    finish(row, "expired")
                    db.commit()
                return poll_view(row)
            account = db.get(SourceAccount, account_id, populate_existing=True) if account_id else None
            if account_id and (account is None or account.status == "disabled" or
                               generation(account) != row.account_generation):
                finish(row, "error", "account_changed")
                db.commit()
                return poll_view(row, message="账号凭据已被修改，请重新发起扫码授权。")
            uid = expected_uid(account) if account else None
            if uid is not None and uid != credential.uid:
                finish(row, "error", "account_mismatch")
                db.commit()
                return poll_view(row)
            if account is None:
                account = SourceAccount(name=row.name, secret_encrypted="")
                db.add(account)
            account.name = row.name
            install_credential(account, credential)
            db.flush()
            row.account_id = account.id
            finish(row, "success")
            db.add(AuditLog(actor_id=identity[0], action="authorize", entity_type="source_account", entity_id=account.id))
            db.commit()
            return poll_view(row, account=account)


@router.post("/{account_id}/refresh")
def check_refresh(account_id: str, identity=Depends(admin_session), db: Session = Depends(get_db)):
    try:
        with credential_lock(db, account_id):
            account = db.get(SourceAccount, account_id, populate_existing=True)
            if not account:
                raise HTTPException(404, "采集账号不存在")
            if account.next_refresh_at and utc(account.next_refresh_at) > utcnow() and account.refresh_error_code:
                raise HTTPException(429, "请等待下次续期检查时间，避免反复请求 B站",
                                    headers={"Retry-After": str(max(1, int((utc(account.next_refresh_at) - utcnow()).total_seconds())))})
            job = enqueue_refresh(db, account, manual=True)
            db.add(AuditLog(actor_id=identity[0], action="check_refresh", entity_type="source_account", entity_id=account.id))
            db.commit()
            return catalog.job_view(job)
    except IngestDeferred as error:
        if error.code == "refresh_check_cooldown":
            raise HTTPException(429, str(error), headers={"Retry-After": "60"}) from None
        raise HTTPException(409, "账号凭据正在更新，请稍后重试") from None
    except IngestError as error:
        raise HTTPException(422, str(error)) from None
