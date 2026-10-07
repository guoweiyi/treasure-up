"""Durable credential rotation. Only short account writes share the API mutex."""
import hashlib
from datetime import timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ingest.client import parse_cookies
from app.ingest.errors import IngestDeferred, IngestError
from app.ingest.passport import BiliPassport, validate_refresh_token
from app.ingest.runner import _lock, credential_lock
from app.jobs import enqueue
from app.models import Job, SourceAccount, utcnow
from app.security import decrypt_secret, encrypt_secret

CHECK_INTERVAL = timedelta(hours=24)
MAX_CONFIRM_FAILURES = 5
MESSAGES = {
    "refresh_uncertain": "上次刷新结果未能确认，请重新扫码授权；原有采集凭据已保留。",
    "account_mismatch": "授权的 B站账号与当前账号不一致，请使用原账号扫码。",
    "invalid_refresh_token": "刷新令牌无效，请重新扫码或填写匹配当前 Cookie 的令牌。",
    "login_required": "B站要求重新登录，请重新扫码授权。",
    "invalid_cookie": "凭据不完整或无法读取，请重新扫码授权。",
    "rate_limited": "B站暂时限制请求，稍后自动检查。",
    "network_error": "连接 B站超时，稍后自动检查。",
    "confirm_failed": "新凭据已保存，正在等待 B站确认；不会重复刷新。",
    "confirm_exhausted": "新凭据已保存，自动确认暂已停止，可点击检查续期重试。",
}


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def generation(account):
    value = account.secret_encrypted + "\0" + (account.refresh_token_encrypted or "")
    return hashlib.sha256(value.encode()).hexdigest()


def account_view(account):
    result = {k: getattr(account, k) for k in (
        "id", "name", "uid", "status", "last_verified_at", "created_at", "next_request_at",
        "next_video_at", "cooldown_until", "risk_failures", "auto_refresh_enabled",
        "last_refresh_check_at", "last_refreshed_at", "next_refresh_at")}
    phase = account.refresh_phase
    if not account.refresh_token_encrypted:
        phase = "unsupported"
    elif not account.auto_refresh_enabled or account.status == "disabled":
        phase = "paused"
    result.update(has_refresh_token=bool(account.refresh_token_encrypted), refresh_status=phase,
                  refresh_message=MESSAGES.get(account.refresh_error_code,
                    "暂未完成续期检查，稍后重试。" if account.refresh_error_code else None))
    if account.refresh_failures >= MAX_CONFIRM_FAILURES and phase == "error":
        result["refresh_message"] = "连续检查失败，自动重试已停止。请检查网络后点击「检查续期」重试。"
    return result


def expected_uid(account, cookie=None):
    if account.uid:
        return str(account.uid)
    try:
        cookies = parse_cookies(cookie if cookie is not None else decrypt_secret(account.secret_encrypted))
        values = {c.value for c in cookies if c.name == "DedeUserID"}
        return values.pop() if len(values) == 1 else None
    except (ValueError, IngestError):
        return None


def reset_refresh(account, token):
    account.refresh_token_encrypted = encrypt_secret(validate_refresh_token(token)) if token else None
    account.refresh_pending_encrypted = None
    account.refresh_phase = "ready" if token else "unsupported"
    account.refresh_failures = 0
    account.refresh_error_code = None
    account.last_refresh_check_at = account.last_refreshed_at = None
    account.next_refresh_at = utcnow() if token else None


def install_credential(account, credential):
    """Caller holds the account mutex and validates the intended UID/generation."""
    account.secret_encrypted = encrypt_secret(credential.cookie_text)
    reset_refresh(account, credential.refresh_token)
    account.uid = credential.uid
    account.status = "valid"
    account.last_verified_at = utcnow()
    account.auto_refresh_enabled = True


def enqueue_refresh(db, account, *, manual=False):
    # Both API requests and the scheduler serialize enqueue with account updates.
    existing = db.scalar(select(Job).where(Job.kind == "refresh_credentials", Job.account_id == account.id,
        Job.status.in_(["queued", "running"])).order_by(Job.created_at.desc()).limit(1))
    if existing:
        return existing
    if not account.refresh_token_encrypted:
        raise IngestError("请先扫码授权或填写独立刷新令牌", code="invalid_refresh_token", retryable=False)
    if account.status == "disabled":
        raise IngestError("采集账号已停用", code="account_disabled", retryable=False)
    if account.refresh_phase == "relogin_required" and not account.refresh_pending_encrypted:
        raise IngestError("请重新扫码授权或更新凭据后再检查续期", code="login_required", retryable=False)
    if manual and account.last_refresh_check_at and utc(account.last_refresh_check_at) > utcnow() - timedelta(seconds=60):
        raise IngestDeferred("刚刚检查过续期，请稍后再试", code="refresh_check_cooldown", retry_after_seconds=60)
    if not manual and (not account.auto_refresh_enabled or account.refresh_phase == "relogin_required"
                       or account.refresh_failures >= MAX_CONFIRM_FAILURES):
        return None
    job = enqueue(db, "refresh_credentials", account.id, account.id, policy={"manual": manual})
    # Each operation records its own next retry. Reserving a slot prevents a
    # scheduler restart from creating a new job every five seconds.
    account.next_refresh_at = utcnow() + timedelta(minutes=15)
    return job


def schedule_refreshes(db):
    now = utcnow()
    engine = db.get_bind()
    with Session(engine) as reader:
        ids = reader.scalars(select(SourceAccount.id).where(
            SourceAccount.refresh_token_encrypted.is_not(None), SourceAccount.auto_refresh_enabled.is_(True),
            SourceAccount.status != "disabled", SourceAccount.refresh_phase != "relogin_required",
            SourceAccount.refresh_failures < MAX_CONFIRM_FAILURES,
            or_(SourceAccount.next_refresh_at.is_(None), SourceAccount.next_refresh_at <= now))
            .order_by(SourceAccount.next_refresh_at, SourceAccount.id).limit(20)).all()
    for account_id in ids:
        try:
            with credential_lock(db, account_id), Session(engine) as writer:
                account = writer.get(SourceAccount, account_id)
                if account and (account.next_refresh_at is None or utc(account.next_refresh_at) <= now):
                    enqueue_refresh(writer, account)
                    writer.commit()
        except IngestDeferred:
            continue


def refresh_account(db, account_id, *, manual=False, check_active=None, passport_factory=None):
    engine = db.get_bind()

    def guard():
        if check_active:
            check_active()
        db.rollback()

    def read():
        with Session(engine, expire_on_commit=False) as reader:
            account = reader.get(SourceAccount, account_id)
            if account is None:
                raise IngestError("采集账号不存在", code="not_found", retryable=False)
            reader.expunge(account)
            return account

    def write(expected, change, *, allow_disabled=False):
        with credential_lock(db, account_id), Session(engine, expire_on_commit=False) as writer:
            account = writer.get(SourceAccount, account_id)
            if account is None or generation(account) != expected:
                raise IngestDeferred("凭据已被更新，本次续期已停止", code="account_changed", retry_after_seconds=1)
            if account.status == "disabled" and not allow_disabled:
                raise IngestError("采集账号已停用", code="account_disabled", retryable=False)
            if not manual and not account.auto_refresh_enabled and not allow_disabled:
                raise IngestError("自动续期已暂停", code="refresh_paused", retryable=False)
            change(account)
            writer.commit()
            writer.expunge(account)
            return account

    def failure(expected, code, *, phase="error", pending=False):
        def change(account):
            account.refresh_failures += 1
            account.refresh_phase = phase
            account.refresh_error_code = code
            if phase == "relogin_required" or account.refresh_failures >= MAX_CONFIRM_FAILURES:
                account.next_refresh_at = None
                if pending:
                    account.refresh_error_code = "confirm_exhausted"
            else:
                delay = min(360, 15 * 2 ** min(account.refresh_failures - 1, 5))
                account.next_refresh_at = utcnow() + timedelta(minutes=delay)
        write(expected, change, allow_disabled=True)
        return {"account_id": account_id, "refresh_status": phase, "error_code": code}

    guard()
    # Refresh serialization is independent from the short cookie mutex used by
    # ordinary API traffic and downloads. No account/database row lock spans HTTP.
    with _lock(db, "credential-refresh:" + account_id):
        account = read()
        if account.status == "disabled":
            raise IngestError("采集账号已停用", code="account_disabled", retryable=False)
        if not manual and not account.auto_refresh_enabled:
            return {"account_id": account_id, "refresh_status": "paused"}
        if not account.refresh_token_encrypted:
            return {"account_id": account_id, "refresh_status": "unsupported"}
        if account.refresh_phase == "relogin_required" and not account.refresh_pending_encrypted:
            return {"account_id": account_id, "refresh_status": "relogin_required",
                    "error_code": account.refresh_error_code}
        expected = generation(account)
        if account.cooldown_until and utc(account.cooldown_until) > utcnow():
            raise IngestDeferred("账号仍在 B站冷却期，稍后检查续期", retry_after_seconds=max(1,
                int((utc(account.cooldown_until) - utcnow()).total_seconds())))
        try:
            cookie = decrypt_secret(account.secret_encrypted)
            token = decrypt_secret(account.refresh_token_encrypted)
            old_token = decrypt_secret(account.refresh_pending_encrypted) if account.refresh_pending_encrypted else None
        except ValueError:
            return failure(expected, "invalid_cookie", phase="relogin_required")
        if account.refresh_phase == "refreshing" and not old_token:
            # A previous process died after declaring an outgoing rotation but
            # before saving its result. Reissuing that POST is not safe.
            return failure(expected, "refresh_uncertain", phase="relogin_required")
        with (passport_factory or BiliPassport)() as passport:
            if not old_token:
                def checking(row):
                    row.refresh_phase, row.refresh_error_code = "checking", None
                write(expected, checking)
                guard()
                try:
                    info = passport.refresh_info(cookie)
                except IngestError as error:
                    return failure(expected, error.code, phase="relogin_required" if error.code in
                        {"login_required", "invalid_refresh_token", "invalid_cookie"} else "error")
                if not info.refresh:
                    def ready(row):
                        row.refresh_phase, row.refresh_error_code, row.refresh_failures = "ready", None, 0
                        row.last_refresh_check_at, row.next_refresh_at = utcnow(), utcnow() + CHECK_INTERVAL
                    write(expected, ready)
                    return {"account_id": account_id, "refresh_status": "ready", "rotated": False}

                def before_rotate():
                    guard()
                    def rotating(row):
                        row.refresh_phase = "refreshing"
                        row.last_refresh_check_at = utcnow()
                    write(expected, rotating)

                try:
                    fresh = passport.refresh(cookie, token, timestamp_ms=info.timestamp_ms, before_rotate=before_rotate)
                except IngestDeferred:
                    raise
                except IngestError as error:
                    if error.code in {"account_disabled", "refresh_paused"}:
                        raise
                    ambiguous = bool(getattr(error, "mutation_started", False) and
                                     getattr(error, "mutation_uncertain", True))
                    return failure(expected, "refresh_uncertain" if ambiguous else error.code,
                        phase="relogin_required" if ambiguous or error.code in
                            {"login_required", "invalid_refresh_token", "invalid_cookie"} else "error")
                uid = expected_uid(account, cookie)
                if uid is None or fresh.uid != uid:
                    return failure(expected, "account_mismatch", phase="relogin_required")

                def persist(row):
                    # Save BOTH generations atomically before confirming. Even a
                    # cancelled worker must not throw away a successful rotation.
                    row.secret_encrypted = encrypt_secret(fresh.cookie_text)
                    row.refresh_token_encrypted = encrypt_secret(fresh.refresh_token)
                    row.refresh_pending_encrypted = encrypt_secret(token)
                    row.refresh_phase, row.refresh_error_code, row.refresh_failures = "pending_confirm", None, 0
                    row.last_refreshed_at, row.next_refresh_at = utcnow(), utcnow()
                    row.uid = fresh.uid
                    if row.status != "disabled":
                        row.status = "valid"
                        row.last_verified_at = utcnow()
                account = write(expected, persist, allow_disabled=True)
                expected, cookie, old_token = generation(account), fresh.cookie_text, token

            guard()
            # Credential edits/disable may have happened during the preceding
            # request. Do not confirm on behalf of a now-replaced generation.
            write(expected, lambda row: None)
            try:
                passport.confirm_refresh(cookie, old_token)
            except IngestError:
                return failure(expected, "confirm_failed", phase="pending_confirm", pending=True)

            def confirmed(row):
                row.refresh_pending_encrypted = None
                row.refresh_phase, row.refresh_error_code, row.refresh_failures = "ready", None, 0
                row.last_refresh_check_at, row.next_refresh_at = utcnow(), utcnow() + CHECK_INTERVAL
            write(expected, confirmed, allow_disabled=True)
            return {"account_id": account_id, "refresh_status": "ready", "rotated": True}
