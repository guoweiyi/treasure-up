"""Authenticated, bounded capture actions from an existing creator's page."""
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingest.client import safe_source_url
from app.ingest.creator_scope import listing_creator_role, video_has_creator
from app.ingest.errors import IngestError
from app.jobs import enqueue
from app.library_deletion import is_suppressed
from app.models import (AuditLog, Collection, Creator, MediaVariant, PlatformUser, SourceAccount,
                        SourceSubscription, Video, VideoPart)
from app.paid_capture import paid_hint
from app.schemas import Input
from app.security import require_editor
from app.source_discovery import (_account, _cached, _client, _key, _remember, _response_cache_key,
                                  CACHE_SECONDS, _count, _safe_error)

router = APIRouter(prefix="/api/v1/creators", tags=["creator-capture"])
PaidFilter = Literal["all", "only", "exclude"]


class CreatorCaptureInput(Input):
    account_id: str = Field(min_length=1, max_length=36)
    bvids: list[str] = Field(min_length=1, max_length=3)
    paid: PaidFilter = "exclude"

    @field_validator("bvids", mode="before")
    @classmethod
    def bounded_batch(cls, values):
        if isinstance(values, list) and len(values) > 3:
            raise ValueError("每次最多提交 3 个视频，请分批串行提交")
        return values

    @field_validator("bvids")
    @classmethod
    def videos_valid(cls, values):
        if any(not re.fullmatch(r"BV[A-Za-z0-9]{10}", value) for value in values):
            raise ValueError("视频 BV 号无效")
        return list(dict.fromkeys(values))


def _creator(db, identifier):
    creator = db.get(Creator, identifier)
    person = db.get(PlatformUser, creator.user_id) if creator else None
    if not person:
        raise HTTPException(404, "UP 主不存在")
    if is_suppressed(db, uid=person.uid):
        raise HTTPException(409, "此 UP 主正在删除或已禁止重新采集")
    return creator, person


def _source(db, uid):
    return db.execute(select(Collection, SourceSubscription)
        .outerjoin(SourceSubscription, SourceSubscription.collection_id == Collection.id)
        .where(Collection.kind == "creator", Collection.source_id == uid)).first()


@router.get("/{creator_id}/capture-options")
def capture_options(creator_id: str, response: Response, _=Depends(require_editor), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    _, person = _creator(db, creator_id)
    accounts = list(db.scalars(select(SourceAccount)
        .where(SourceAccount.status.not_in(["invalid", "expired", "disabled"]))
        .order_by(SourceAccount.last_verified_at.desc().nulls_last(), SourceAccount.created_at, SourceAccount.id)))
    source = _source(db, person.uid)
    preferred = source[1].account_id if source and source[1] else None
    available = {account.id for account in accounts}
    return {"accounts": [{"id": account.id, "name": account.name, "status": account.status} for account in accounts],
        "default_account_id": preferred if preferred in available else accounts[0].id if accounts else None,
        "source": {"id": source[0].id, "title": source[0].title} if source else None}


def _matches(paid, mode):
    return mode == "all" or paid == (mode == "only")


def _published(value):
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat() if int(value) > 0 else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _duration(value):
    if isinstance(value, (str, int)) and re.fullmatch(r"\d{1,6}(?::\d{1,2}){0,2}", str(value)):
        seconds = 0
        for piece in str(value).split(":"):
            seconds = seconds * 60 + int(piece)
        return min(seconds, 10_000_000)
    return 0


def _cover(value):
    # Returned URLs are display-only, never proxy fetch targets. Keep only the
    # Bilibili media CDN and strip transient query strings.
    from urllib.parse import urlsplit
    value = "https:" + value if isinstance(value, str) and value.startswith("//") else value
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    return safe_source_url(value) if parsed.scheme in {"http", "https"} and (host == "hdslb.com" or host.endswith(".hdslb.com")) else None


def latest_page(db, creator_id, account_id, page, paid, *, refresh=False):
    _, person = _creator(db, creator_id)
    account = _account(db, account_id)
    key = _key(account, "creator-latest", person.uid, page)
    listing = None if refresh else _cached(key)
    cached = listing is not None
    if listing is None:
        with _client(db, account) as (client, _nav):
            raw = client.creator_page(person.uid, page)
            pagination = raw.get("page") if isinstance(raw, dict) else None
            container = raw.get("list") if isinstance(raw, dict) else None
            rows = container.get("vlist") if isinstance(container, dict) else None
            if (not isinstance(pagination, dict) or type(pagination.get("pn")) is not int
                    or type(pagination.get("ps")) is not int or pagination["pn"] != page or pagination["ps"] != 30):
                raise IngestError("UP 投稿分页无效", code="invalid_pagination")
            total = _count(pagination.get("count"))
            if not isinstance(rows, list) or len(rows) > 30 or any(not isinstance(row, dict) for row in rows):
                raise IngestError("UP 投稿内容无效", code="invalid_response")
            if not rows and (page - 1) * 30 < total:
                raise IngestError("UP 投稿分页提前返回空页", code="invalid_pagination")
            items, seen = [], set()
            for row in rows:
                bvid = row.get("bvid")
                if not isinstance(bvid, str) or not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
                    continue  # An inaccessible listing can intentionally hide its BV.
                listing_creator_role(row, person.uid)
                if bvid in seen:
                    raise IngestError("UP 投稿返回重复视频", code="invalid_pagination")
                seen.add(bvid)
                items.append({"bvid": bvid, "title": str(row.get("title") or "")[:2000],
                    "published_at": _published(row.get("created")), "duration": _duration(row.get("length")),
                    "cover_url": _cover(row.get("pic")), "charging_exclusive": paid_hint(row)})
            listing = {"items": items, "total": total, "page": page, "page_size": 30,
                "has_more": page * 30 < total, "next_page": page + 1 if page * 30 < total else None}
            _remember(_response_cache_key(db, account, client, "creator-latest", person.uid, page), listing)
    saved = {video.bvid: video for video in db.scalars(select(Video).where(Video.bvid.in_([item["bvid"] for item in listing["items"]])))}
    items = []
    for item in listing["items"]:
        video = saved.get(item["bvid"])
        from app.paid_capture import is_paid
        exclusive = item["charging_exclusive"] or bool(video and is_paid(video))
        if _matches(exclusive, paid):
            items.append({**item, "charging_exclusive": exclusive, "video_id": video.id if video else None,
                "capture_status": video.capture_status if video else None})
    return {**listing, "items": items, "paid": paid, "cached": cached, "cache_seconds": CACHE_SECONDS,
        "filter_scope": "current_source_page"}


@router.get("/{creator_id}/latest")
def latest(creator_id: str, response: Response, account_id: str = Query(min_length=1, max_length=36),
           page: int = Query(1, ge=1, le=10000), paid: PaidFilter = "all", refresh: bool = False,
           _=Depends(require_editor), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return latest_page(db, creator_id, account_id, page, paid, refresh=refresh)
    except IngestError as error:
        raise _safe_error(error) from None


@router.post("/{creator_id}/capture", status_code=202)
def capture(creator_id: str, body: CreatorCaptureInput, response: Response,
            user=Depends(require_editor), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    _, person = _creator(db, creator_id)
    account = _account(db, body.account_id)
    accepted, skipped = [], []
    # Validate the authoritative owner/staff before enqueueing any work. A BV
    # from another page, forged checkbox or stale paid hint cannot broaden scope.
    with _client(db, account) as (client, _nav):
        for bvid in body.bvids:
            if is_suppressed(db, bvid=bvid):
                skipped.append({"bvid": bvid, "status": "skipped", "reason": "deleted_locally"})
                continue
            raw = client.view(bvid)
            if not isinstance(raw, dict) or not isinstance(raw.get("owner"), dict):
                raise IngestError("稿件作者信息无效", code="invalid_response")
            if raw.get("bvid") != bvid or not video_has_creator(raw, person.uid):
                raise HTTPException(422, "所选视频不属于当前 UP 主，请刷新投稿列表后重试")
            if not _matches(paid_hint(raw), body.paid):
                skipped.append({"bvid": bvid, "status": "skipped", "reason": "paid_filter"})
                continue
            accepted.append(bvid)
    queued, reused, items = 0, 0, list(skipped)
    request_id = str(uuid4())
    for bvid in sorted(accepted):
        video = db.scalar(select(Video).where(Video.bvid == bvid))
        archived = video and video.capture_status == "complete" and db.scalar(select(MediaVariant.id)
            .join(VideoPart, VideoPart.id == MediaVariant.part_id)
            .where(VideoPart.video_id == video.id, MediaVariant.kind == "archive").limit(1))
        if archived:
            items.append({"bvid": bvid, "status": "archived", "job_id": None})
            reused += 1
            continue
        try:
            key = f"creator:{creator_id}:{request_id}:{bvid}"
            job = enqueue(db, "archive_video", bvid, account.id, {
                "capture_creator_uid": person.uid, "capture_paid_filter": body.paid,
                "include_paid_videos": body.paid != "exclude", "creator_request_user": user.id}, dedupe_key=key)
        except IngestError as error:
            db.rollback()
            raise _safe_error(error) from None
        # The job can be shared with another authorized capture demand. Do not
        # rewrite that active job's selected account or frozen policy.
        if job.dedupe_key == key:
            queued += 1
        else:
            reused += 1
        items.append({"bvid": bvid, "status": job.status, "job_id": job.id})
    db.add(AuditLog(actor_id=user.id, action="capture", entity_type="creator", entity_id=creator_id,
        details={"selected": len(body.bvids), "queued": queued, "reused": reused, "paid": body.paid, "account_id": account.id}))
    db.commit()
    return {"items": items, "queued": queued, "reused": reused, "skipped": len(skipped)}
