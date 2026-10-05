"""Hash-only, revocable userscript capability: submit bounded BV lists, nothing else."""
from contextlib import nullcontext
from datetime import timedelta, timezone
import hashlib
import re
import secrets
import threading
from uuid import uuid4
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (AuditLog, IntegrationToken, Job, MediaVariant, Setting, SourceAccount,
                        User, Video, VideoPart, utcnow)
from app.schemas import IngestPolicy
from app.security import require_admin

router = APIRouter(prefix="/api/v1", tags=["userscript-integration"])
SCOPE = "ingest.submit"
MAX_BATCH = 50
MINUTE_REQUESTS = 10
DAY_VIDEOS = 500
MAX_ACTIVE_TOKENS = 20
_sqlite_mutex = threading.RLock()
Bvid = Annotated[str, Field(pattern=r"^BV[A-Za-z0-9]{10}$", min_length=12, max_length=12)]


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    account_id: str = Field(min_length=1, max_length=36)
    expires_days: int = Field(default=90, ge=1, le=365)
    policy: IngestPolicy = Field(default_factory=IngestPolicy)


class TokenUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    account_id: str | None = Field(default=None, min_length=1, max_length=36)
    policy: IngestPolicy | None = None


class VideoSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bvids: list[Bvid] = Field(min_length=1, max_length=MAX_BATCH)


def _utc(moment):
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def _token_view(item, account_name):
    return {**{name: getattr(item, name) for name in ("id", "name", "account_id", "scope", "expires_at", "revoked_at",
            "created_at", "last_used_at", "policy")}, "account_name": account_name}


def _account(db, identifier):
    account = db.scalar(select(SourceAccount).where(SourceAccount.id == identifier).with_for_update(read=True)
                        .execution_options(populate_existing=True))
    if not account or account.status in {"disabled", "invalid", "expired"}:
        raise HTTPException(409, "采集账号不可用，请在后台更新或验证账号")
    return account


def _frozen_policy(db, policy):
    saved = db.get(Setting, "ingest")
    values = {**IngestPolicy().model_dump(), **(saved.value or {} if saved else {}),
              **policy.model_dump(exclude_unset=True)}
    return IngestPolicy.model_validate(values).model_dump()


@router.get("/admin/integrations/userscript-tokens")
def list_tokens(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                _=Depends(require_admin), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(IntegrationToken))
    rows = db.execute(select(IntegrationToken, SourceAccount.name).join(SourceAccount,
        SourceAccount.id == IntegrationToken.account_id).order_by(IntegrationToken.created_at.desc(), IntegrationToken.id)
        .offset((page - 1) * page_size).limit(page_size))
    return {"items": [_token_view(item, name) for item, name in rows], "total": count, "page": page, "page_size": page_size}


@router.post("/admin/integrations/userscript-tokens", status_code=201)
def create_token(body: TokenCreate, user=Depends(require_admin), db: Session = Depends(get_db)):
    # Match account-disable serialization without taking a FK-incompatible lock.
    owner = db.scalar(select(User).where(User.id == user.id).with_for_update(key_share=True)
                       .execution_options(populate_existing=True))
    if not owner or owner.disabled or owner.role != "admin":
        raise HTTPException(403, "管理员身份已失效")
    now = utcnow()
    count = db.scalar(select(func.count()).select_from(IntegrationToken).where(IntegrationToken.user_id == owner.id,
        IntegrationToken.revoked_at.is_(None), IntegrationToken.expires_at > now))
    if count >= MAX_ACTIVE_TOKENS:
        raise HTTPException(409, "有效集成令牌已达上限，请先撤销不再使用的令牌")
    account = _account(db, body.account_id)
    secret = "tu_ingest_" + secrets.token_urlsafe(32)
    item = IntegrationToken(user_id=owner.id, account_id=account.id, name=body.name.strip() or "油猴脚本",
        token_hash=hashlib.sha256(secret.encode()).hexdigest(), scope=SCOPE,
        policy=_frozen_policy(db, body.policy), expires_at=now + timedelta(days=body.expires_days))
    db.add(item); db.flush()
    db.add(AuditLog(actor_id=owner.id, action="create_userscript_token", entity_type="integration_token", entity_id=item.id,
        details={"account_id": account.id, "scope": SCOPE, "expires_days": body.expires_days}))
    db.commit()
    return {"item": _token_view(item, account.name), "token": secret}


@router.patch("/admin/integrations/userscript-tokens/{identifier}")
def update_token(identifier: str, body: TokenUpdate, user=Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(IntegrationToken).where(IntegrationToken.id == identifier).with_for_update())
    if not item:
        raise HTTPException(404, "集成令牌不存在")
    if item.revoked_at or _utc(item.expires_at) <= utcnow():
        raise HTTPException(409, "令牌已经失效，请创建新令牌")
    account = _account(db, body.account_id or item.account_id)
    if body.name is not None:
        item.name = body.name.strip() or "油猴脚本"
    item.account_id = account.id
    if body.policy is not None:
        item.policy = _frozen_policy(db, body.policy)
    db.add(AuditLog(actor_id=user.id, action="update_userscript_token", entity_type="integration_token", entity_id=item.id,
        details={"fields": sorted(body.model_fields_set)}))
    db.commit()
    return _token_view(item, account.name)


@router.delete("/admin/integrations/userscript-tokens/{identifier}")
def revoke_token(identifier: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(IntegrationToken).where(IntegrationToken.id == identifier).with_for_update())
    if not item:
        raise HTTPException(404, "集成令牌不存在")
    item.revoked_at = item.revoked_at or utcnow()
    db.add(AuditLog(actor_id=user.id, action="revoke_userscript_token", entity_type="integration_token", entity_id=item.id))
    db.commit()
    return {"id": item.id, "revoked": True}


def _authenticate(db, request):
    # GM background requests can carry different Origin headers by browser.
    # The explicit capability is the sole authority: never fall back to cookies,
    # reflect Origin, or authorize CORS preflight for ordinary page fetch.
    authorization = request.headers.get("authorization", "")
    if not re.fullmatch(r"Bearer tu_ingest_[A-Za-z0-9_-]{43}", authorization):
        raise HTTPException(401, "集成令牌无效或已失效", headers={"WWW-Authenticate": "Bearer"})
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    identity = db.execute(select(IntegrationToken.id, IntegrationToken.user_id).where(IntegrationToken.token_hash == digest)).first()
    if not identity:
        raise HTTPException(401, "集成令牌无效或已失效", headers={"WWW-Authenticate": "Bearer"})
    # User -> token -> source-account is the lock order. NO KEY UPDATE blocks
    # disabling while permitting AuditLog / Job foreign-key KEY SHARE locks.
    owner = db.scalar(select(User).where(User.id == identity.user_id).with_for_update(key_share=True)
                       .execution_options(populate_existing=True))
    token = db.scalar(select(IntegrationToken).where(IntegrationToken.id == identity.id).with_for_update()
                       .execution_options(populate_existing=True))
    if not owner or owner.disabled or owner.role != "admin" or not token or token.revoked_at or token.scope != SCOPE or _utc(token.expires_at) <= utcnow():
        raise HTTPException(401, "集成令牌无效或已失效", headers={"WWW-Authenticate": "Bearer"})
    return token, _account(db, token.account_id)


@router.get("/integrations/userscript/status")
def integration_status(request: Request, db: Session = Depends(get_db)):
    token, account = _authenticate(db, request)
    return {"ok": True, "scope": SCOPE, "max_batch": MAX_BATCH, "account": {"name": account.name},
        "expires_at": token.expires_at, "policy": {key: token.policy[key] for key in
            ("quality", "download_media", "fetch_comments", "fetch_danmaku", "fetch_subtitles")}}


def _reserve(db, token, now):
    if not token.minute_started_at or _utc(token.minute_started_at) + timedelta(minutes=1) <= now:
        token.minute_started_at, token.minute_requests = now, 0
    if token.minute_requests >= MINUTE_REQUESTS:
        retry = max(1, int((_utc(token.minute_started_at) + timedelta(minutes=1) - now).total_seconds()) + 1)
        raise HTTPException(429, "提交过于频繁，请稍后重试", headers={"Retry-After": str(retry)})
    if not token.day_started_at or _utc(token.day_started_at) + timedelta(days=1) <= now:
        token.day_started_at, token.day_videos = now, 0
    token.minute_requests += 1


def _batch_locks(db, bvids):
    if db.get_bind().dialect.name == "postgresql":
        # All BVID locks use the same connection and stable order, avoiding a
        # connection per item and cross-token reversed-batch deadlocks.
        for bvid in sorted(bvids):
            key = int.from_bytes(hashlib.sha256(("archive-submit:" + bvid).encode()).digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


@router.post("/integrations/userscript/videos", status_code=202)
def submit_videos(body: VideoSubmission, request: Request, db: Session = Depends(get_db)):
    from app.jobs import enqueue
    from app.library_deletion import is_suppressed
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "请使用 application/json 提交 BV 列表")
    # SQLite is only a test environment and does not implement FOR UPDATE.
    with _sqlite_mutex if db.get_bind().dialect.name == "sqlite" else nullcontext():
        token, account = _authenticate(db, request)
        now = utcnow()
        _reserve(db, token, now)
        bvids = list(dict.fromkeys(body.bvids))
        _batch_locks(db, bvids)
        rows = {item.bvid: item for item in db.scalars(select(Video).where(Video.bvid.in_(bvids)))}
        items = []
        for bvid in bvids:
            video = rows.get(bvid)
            result = {"bvid": bvid, "video_id": video.id if video else None, "job_id": None}
            if is_suppressed(db, bvid=bvid):
                items.append({**result, "status": "deleted"})
                continue
            targets = [bvid] + ([video.id] if video else [])
            previous = select(Job).where(Job.kind.in_(["archive_video", "download_media"]),
                Job.target_id.in_(targets), Job.checkpoint["deleted_by"].as_string().is_(None))
            active = db.scalar(previous.where(Job.status.in_(["queued", "running"]))
                .order_by(Job.created_at.desc(), Job.id).limit(1))
            if active:
                items.append({**result, "status": "active", "job_id": active.id})
                continue
            if video and video.capture_status in {"complete", "local_import"}:
                has_media = db.scalar(select(MediaVariant.id).join(VideoPart, VideoPart.id == MediaVariant.part_id)
                    .where(VideoPart.video_id == video.id, MediaVariant.kind == "archive").limit(1))
                if has_media or not token.policy.get("download_media", True):
                    items.append({**result, "status": "existing"})
                    continue
            stopped = db.scalar(previous.where(Job.status.in_(["paused", "blocked", "failed", "cancelled", "partial"]))
                .order_by(Job.created_at.desc(), Job.id).limit(1))
            if stopped:
                items.append({**result, "status": "needs_attention", "job_id": stopped.id})
                continue
            if token.day_videos >= DAY_VIDEOS:
                # Per-item refusal keeps already accepted entries useful; no
                # item beyond the durable quota can enqueue a job.
                items.append({**result, "status": "daily_limit"})
                continue
            submission_key = "userscript:" + str(uuid4())
            job = enqueue(db, "archive_video", video.id if video else bvid, account.id,
                dict(token.policy), dedupe_key=submission_key, frozen_policy=True)
            if job.dedupe_key != submission_key:
                items.append({**result, "status": "active" if job.status in {"queued", "running"} else "needs_attention", "job_id": job.id})
                continue
            if account.cooldown_until and _utc(account.cooldown_until) > now:
                job.available_at = _utc(account.cooldown_until)
            token.day_videos += 1
            items.append({**result, "status": "queued", "job_id": job.id})
        token.last_used_at = now
        counts = {status: sum(item["status"] == status for item in items) for status in
                  ("queued", "active", "existing", "needs_attention", "deleted", "daily_limit")}
        db.add(AuditLog(actor_id=token.user_id, action="userscript_submit", entity_type="integration_token", entity_id=token.id,
            details={"bvids": bvids, "counts": counts}))
        db.commit()
        return {"items": items, "counts": counts, "submitted": len(body.bvids), "unique": len(bvids)}
