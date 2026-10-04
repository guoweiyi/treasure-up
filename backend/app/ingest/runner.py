"""Resumable ingestion using durable SQL checkpoints and immutable assets.

No source request is made by importing this module. run_job is worker-only.
"""
import hashlib
import json
import math
import re
import sqlite3
import tempfile
import threading
import time
from copy import deepcopy
from contextlib import closing, contextmanager, nullcontext, ExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (Asset, AssetRef, CaptureRun, Collection, CollectionItem, Comment,
    CommentAsset, CommentVersion, Creator, DanmakuSnapshot, Job, MediaVariant, OutboxEvent,
    PlatformUser, SourceAccount, SourceSubscription, SubtitleTrack, UserSnapshot,
    Video, VideoCreator, VideoPart, VideoStatSnapshot)
from app.security import decrypt_secret
from app.storage.service import ingest_file
from .client import BiliClient, clean_raw, safe_source_url, parse_cookies
from .errors import IngestDeferred, IngestError, PartialCaptureError
from .media import archive_media, cleanup_media_scratch, ensure_playback_variant
from .protobuf import decode_danmaku
from .throttle import AccountPacer, utc


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc) if value else None
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _id(value, *, required=True):
    result = str(value if value is not None else "")
    if not result.isdecimal() or len(result) > 32 or (required and int(result) == 0):
        if not required:
            return "0"
        raise IngestError("来源标识字段无效", code="invalid_response")
    return result


@contextmanager
def _lock(db, name):
    """A dedicated connection holds the session lock across checkpoint commits."""
    engine = db.get_bind()
    if engine.dialect.name != "postgresql":
        yield  # SQLite tests run serially; production uses PostgreSQL.
        return
    key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        if not connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            raise IngestDeferred("相同账号或视频已有采集任务，稍后重试", code="resource_busy", retry_after_seconds=30)
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def _ref(db, asset_id, entity, entity_id, purpose):
    query = select(AssetRef).where(AssetRef.asset_id == asset_id, AssetRef.entity_type == entity, AssetRef.entity_id == entity_id, AssetRef.purpose == purpose)
    if not db.scalar(query):
        db.add(AssetRef(asset_id=asset_id, entity_type=entity, entity_id=entity_id, purpose=purpose))


def _halt(error):
    return error.blocked or getattr(error, "deferred", False) or error.code in {"job_stopped", "rate_limited"}


@contextmanager
def credential_lock(db, account_id):
    """A brief credential race should not become a 30-second foreground 429."""
    with ExitStack() as stack:
        for attempt in range(50):
            try:
                stack.enter_context(_lock(db, "account:" + account_id))
                break
            except IngestDeferred as error:
                if error.code != "resource_busy":
                    raise
                if attempt == 49:
                    raise IngestDeferred("账号凭据正在更新，请重试", code="account_changed", retry_after_seconds=1) from None
                time.sleep(.01)
        yield


def update_account_identity(db, account, generation, *, nav=None, invalid=False):
    """Commit only account identity, never caller job/capture rows under mutex."""
    with credential_lock(db, account.id):
        with Session(db.get_bind()) as writer:
            current = writer.get(SourceAccount, account.id)
            if current is None or (current.id, hashlib.sha256(current.secret_encrypted.encode()).hexdigest()) != generation:
                raise IngestDeferred("账号凭据已更新，请重新验证", code="account_changed", retry_after_seconds=1)
            values = {"status": "invalid"} if invalid else {"uid": _id(nav.get("mid")), "status": "valid", "last_verified_at": _now()}
            writer.execute(update(SourceAccount).where(SourceAccount.id == current.id,
                SourceAccount.secret_encrypted == current.secret_encrypted).values(**values))
            writer.commit()
    db.refresh(account, attribute_names=["uid", "status", "last_verified_at"])


@contextmanager
def account_request_scope(db, account, client, pacer, url, *, decryptor=None):
    """Snapshot credentials under a short mutex; never hold it over HTTP.

    A rotation may overlap an in-flight request. Its immutable generation binds
    the response to the credentials actually sent; identity-sensitive callers
    must reject that response if they observe a different generation later.
    """
    host = (urlsplit(url).hostname or "").lower()
    if host != "bilibili.com" and not host.endswith(".bilibili.com"):
        db.refresh(account, attribute_names=["cooldown_until", "risk_failures"])
        try:
            yield
        except IngestError as error:
            if error.code == "rate_limited":
                pacer.failed(error)
                error.account_backoff_applied = True
            raise
        return
    pacer.guard()
    with credential_lock(db, account.id):
        db.refresh(account, attribute_names=["secret_encrypted", "status", "cooldown_until", "risk_failures"])
        if account.status in {"invalid", "expired", "disabled"}:
            raise IngestError("账号凭据已失效或停用", code="login_required", retryable=False)
        # No job guard/flush or capture commit while holding the credential lock.
        if account.cooldown_until and utc(account.cooldown_until) > pacer.clock():
            raise IngestDeferred("账号仍在源站冷却期", code="account_cooldown",
                retry_after_seconds=math.ceil((utc(account.cooldown_until) - pacer.clock()).total_seconds()))
        try:
            client.cookies = parse_cookies((decryptor or decrypt_secret)(account.secret_encrypted))
        except Exception:
            raise IngestError("账号凭据无法解密", code="invalid_cookie", retryable=False) from None
        client.credential_generation = (account.id, hashlib.sha256(account.secret_encrypted.encode()).hexdigest())
    try:
        yield
    except IngestError as error:
        if error.code == "rate_limited":
            pacer.failed(error)
            error.account_backoff_applied = True
        raise


class Context:
    def __init__(self, db, job, client):
        self.db, self.job, self.client = db, job, client
        self.job_id, self.owner_thread = job.id, threading.get_ident()
        self.policy = job.policy or {}
        self.policy = dict(self.policy)
        for public, internal in {"download_media": "media", "fetch_comments": "comments", "fetch_danmaku": "danmaku", "fetch_subtitles": "subtitles", "include_auto_subtitles": "auto_subtitles"}.items():
            if public in self.policy:
                self.policy[internal] = self.policy[public]
        self.cp = deepcopy(job.checkpoint or {})
        self.cp["images"] = list(self.cp.get("images", []))
        self.pages = 0
        self.budget = max(1, min(10000, int(self.policy.get("max_pages", 10000)), int(self.policy.get("request_budget", self.policy.get("page_budget", 50)))))
        self.owner = job.lease_owner
        self.started = time.monotonic()
        self.progress_lock, self.last_progress = threading.Lock(), 0
        self.run = db.get(CaptureRun, self.cp.get("run_id")) if self.cp.get("run_id") else None
        if not self.run:
            self.run = CaptureRun(scope={"kind": job.kind, "account_id": job.account_id}, status="running")
            db.add(self.run)
            db.flush()
            self.cp["run_id"] = self.run.id
        self.run.status = "running"
        self.run.finished_at = None

    def guard(self):
        if threading.get_ident() != self.owner_thread:
            # yt-dlp can call progress/URL hooks from fragment threads, even
            # when separate video/audio downloads each use one fragment worker.
            # Never share the runner's SQLAlchemy Session across those threads.
            with Session(self.db.get_bind()) as reader:
                current = reader.execute(select(Job.status, Job.lease_owner, Job.lease_expires_at).where(Job.id == self.job_id)).first()
            if current is None:
                raise IngestError("采集任务已不存在", code="job_stopped", retryable=False)
            status, owner, expiry = current
            if expiry and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if status != "running" or (self.owner and owner != self.owner) or (expiry and expiry <= _now()):
                raise IngestError("任务已暂停、取消或租约失效", code="job_stopped", retryable=False)
            return
        self.db.flush()
        self.db.refresh(self.job, attribute_names=["status", "lease_owner", "lease_expires_at"])
        expiry = self.job.lease_expires_at
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if self.job.status != "running" or (self.owner and self.job.lease_owner != self.owner) or (expiry and expiry <= _now()):
            raise IngestError("任务已暂停、取消或租约失效", code="job_stopped", retryable=False)

    def save(self):
        self.job.checkpoint = deepcopy(self.cp)
        self.run.checkpoint = deepcopy(self.cp)
        self.db.commit()

    def request_slot(self):
        self.guard()
        if self.pages >= self.budget or (self.job.kind == "archive_video" and (self.pages >= 8 or time.monotonic() - self.started >= 45)):
            return False
        self.pages += 1
        return True

    def stage(self, phase, **details):
        self.cp["progress"] = {"phase": phase, "updated_at": _now().isoformat(), **details}
        self.save()

    def media_progress(self, values):
        if not self.owner:
            return
        # Fragment workers use separate Sessions; never share the runner's
        # mutable transaction across yt-dlp's callback threads.
        with self.progress_lock:
            if time.monotonic() - self.last_progress < 5:
                return
            self.last_progress = time.monotonic()
            from app.jobs import ensure_active, fence_transaction
            with Session(self.db.get_bind()) as progress_db:
                ensure_active(progress_db, self.job_id, self.owner)
                current = progress_db.get(Job, self.job_id)
                current.result = {**(current.result or {}), "progress": {**values, "updated_at": _now().isoformat()}}
                fence_transaction(progress_db, self.job_id, self.owner)
                progress_db.commit()

    def image(self, url, entity, entity_id, purpose="avatar", *, comment_asset=False):
        if not url or not self.policy.get("images", True):
            return
        url = safe_source_url("https:" + url if url.startswith("//") else url)
        item = {"url": url, "entity": entity, "id": entity_id, "purpose": purpose}
        if comment_asset:
            from .comment_capture import budget
            video = self.db.get(Video, self.run.video_id) if self.run.video_id else None
            ledger = _comment_ledger(self, video)
            key = hashlib.sha256(url.encode()).hexdigest()
            if key not in ledger:
                if sum(item.get("count", 1) for item in ledger.values()) >= budget(self.policy, "comment_asset_count_limit"):
                    self.cp["comment_assets_limited"] = True
                    return
                ledger[key] = {"status": "pending", "bytes": 0}
            if ledger[key].get("status") == "budget_skipped":
                return
            self.cp["comment_assets"] = ledger
            if video:
                video.metadata_json = {**(video.metadata_json or {}), "comment_assets": ledger}
            item["comment_asset_key"] = key
        if item not in self.cp["images"]:
            self.cp["images"].append(item)

    def bytes_asset(self, data, kind, mime):
        settings.scratch_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="treasure-data-", dir=settings.scratch_dir) as temp:
            path = Path(temp) / "payload"
            path.write_bytes(data)
            return ingest_file(self.db, path, kind=kind, mime_type=mime, profile_id=self.policy.get("storage_profile_id"))


def _user(ctx, raw, *, creator=False, full_profile=False, comment_assets=False):
    if not isinstance(raw, dict):
        return None, None, None
    uid = _id(raw.get("mid"), required=False)
    if uid == "0":
        return None, None, None
    db = ctx.db
    if creator:
        from app.library_deletion import is_suppressed
        if is_suppressed(db, uid=uid):
            return None, None, None
    user = db.scalar(select(PlatformUser).where(PlatformUser.uid == uid))
    if not user:
        user = PlatformUser(uid=uid, display_name="", signature="", raw={})
        db.add(user)
        db.flush()
    name = raw.get("uname") or raw.get("name") or user.display_name
    # Comment members and view.owner often include sign="" and face="".
    # Only an authoritative profile response may clear a previously saved bio.
    signature = raw.get("sign", user.signature) if full_profile else raw.get("sign") or user.signature
    avatar = raw.get("avatar") or raw.get("face") or (user.raw or {}).get("avatar_url")
    avatar = safe_source_url("https:" + avatar if avatar.startswith("//") else avatar) if avatar else None
    changed = name != user.display_name or signature != user.signature or avatar != (user.raw or {}).get("avatar_url")
    user.display_name, user.signature = str(name or ""), str(signature or "")
    if avatar != (user.raw or {}).get("avatar_url"):
        user.avatar_asset_id = None
    user.raw = {**(user.raw or {}), "avatar_url": avatar}
    if full_profile:
        user.raw = {**user.raw, "profile_fetched_at": _now().isoformat(), "public_profile": clean_raw(raw)}
    snapshot = None if changed else db.scalar(select(UserSnapshot).where(UserSnapshot.user_id == user.id).order_by(UserSnapshot.observed_at.desc()))
    if not snapshot:
        snapshot = UserSnapshot(user_id=user.id, display_name=user.display_name, signature=user.signature, avatar_asset_id=user.avatar_asset_id, observed_at=_now())
        db.add(snapshot)
        db.flush()
    if avatar and not user.avatar_asset_id:
        ctx.image(avatar, "user", user.id, comment_asset=comment_assets)
        ctx.image(avatar, "user_snapshot", snapshot.id, comment_asset=comment_assets)
    entity = None
    if creator:
        entity = db.scalar(select(Creator).where(Creator.user_id == user.id))
        if not entity:
            entity = Creator(user_id=user.id)
            db.add(entity)
            db.flush()
    return user, snapshot, entity


def _images(ctx):
    """Independent durable queue; a failed asset doesn't discard comment bodies."""
    pending, failed = list(ctx.cp["images"]), []
    cache = {}
    maximum = max(1, min(100, int(ctx.policy.get("image_budget", 50))))
    for index, task in enumerate(pending):
        if index >= maximum or (index and ctx.job.kind == "archive_video" and time.monotonic() - ctx.started >= 60):
            ctx.cp["images"] = failed + pending[index:]
            ctx.save()
            return False
        ctx.guard()
        key = task.get("comment_asset_key")
        ledger = deepcopy(ctx.cp.get("comment_assets", {}))
        entry = ledger.get(key, {}) if key else {}
        from .comment_capture import budget
        remaining = budget(ctx.policy, "comment_asset_bytes_limit") - sum(item.get("bytes", 0) for item in ledger.values())
        if key and (entry.get("status") == "budget_skipped" or (remaining <= 0 and not entry.get("asset_id"))):
            ledger[key] = {"status": "budget_skipped", "bytes": 0}
            ctx.cp["comment_assets_limited"] = True
            _image_ledger(ctx, ledger)
            ctx.cp["images"] = failed + pending[index + 1:]
            ctx.save()
            continue
        try:
            saved = ctx.db.get(Asset, entry["asset_id"]) if entry.get("asset_id") else None
            if saved:
                asset = saved
            elif task["url"] in cache:
                asset = cache[task["url"]]
            else:
                url = task["url"]
                if task["purpose"] == "avatar":
                    # CDN thumbnail transformations preserve the source URL in
                    # observations while archiving a display-sized raster.
                    url = url.split("@", 1)[0] + "@160w_160h_1c.webp"
                elif task["purpose"] == "emote":
                    url = url.split("@", 1)[0] + "@96w.webp"
                elif task["purpose"] in {"attachment", "image"}:
                    url = url.split("@", 1)[0] + "@960w.webp"
                body, mime = ctx.client.asset(url, limit=min(10 * 1024 * 1024, remaining) if key else 10 * 1024 * 1024)
                if key and len(body) > remaining:
                    raise IngestError("评论素材达到字节预算", code="response_too_large", retryable=False)
                # Never serve HTML/SVG under a trusted origin as an image.
                if body.startswith(b"\x89PNG\r\n\x1a\n"):
                    mime = "image/png"
                elif body.startswith(b"\xff\xd8\xff"):
                    mime = "image/jpeg"
                elif body.startswith((b"GIF87a", b"GIF89a")):
                    mime = "image/gif"
                elif body[:4] == b"RIFF" and body[8:12] == b"WEBP":
                    mime = "image/webp"
                else:
                    raise IngestError("图片格式不受支持或响应无效", code="invalid_image", retryable=False)
                asset = ctx.bytes_asset(body, "image", mime)
                cache[task["url"]] = asset
            if key and not entry.get("asset_id"):
                if asset.size > remaining:
                    # A non-comment cover/avatar may already be in this slice's
                    # cache. Reuse must not bypass comment inventory accounting.
                    raise IngestError("评论素材达到字节预算", code="response_too_large", retryable=False)
                ledger[key] = {"status": "saved", "asset_id": asset.id, "bytes": asset.size}
                _image_ledger(ctx, ledger)
            cls = {"user": PlatformUser, "user_snapshot": UserSnapshot, "video": Video}.get(task["entity"])
            if cls:
                target = ctx.db.get(cls, task["id"])
                if target:
                    setattr(target, "cover_asset_id" if cls is Video else "avatar_asset_id", asset.id)
            elif task["entity"] == "comment":
                if not ctx.db.scalar(select(CommentAsset).where(CommentAsset.comment_id == task["id"], CommentAsset.asset_id == asset.id, CommentAsset.kind == task["purpose"])):
                    ctx.db.add(CommentAsset(comment_id=task["id"], asset_id=asset.id, kind=task["purpose"], position=task.get("position", 0)))
                comment = ctx.db.get(Comment, task["id"])
                if comment:
                    manifest = [{**item, "asset_id": asset.id} if item.get("source_url") == task["url"] and item.get("purpose") == task["purpose"] else item
                        for item in (comment.raw or {}).get("asset_manifest", [])]
                    comment.raw = {**(comment.raw or {}), "asset_manifest": manifest}
            _ref(ctx.db, asset.id, task["entity"], task["id"], task["purpose"])
        except IngestError as error:
            if _halt(error):
                raise
            if key and error.code == "response_too_large":
                ledger[key] = {"status": "budget_skipped", "bytes": 0}
                ctx.cp["comment_assets_limited"] = True
                _image_ledger(ctx, ledger)
            elif error.code == "asset_not_found":
                # A permanent CDN 404/410 must not repeatedly block an otherwise
                # usable archive. Keep an explicit record of the missing image.
                missing = dict(ctx.cp.get("unavailable_images", {}))
                identity = f"{task['entity']}:{task['id']}:{task['purpose']}:{task.get('position', 0)}"
                missing[identity] = {"entity": task["entity"], "id": task["id"], "purpose": task["purpose"], "reason": "source_missing"}
                ctx.cp["unavailable_images"] = missing
                if key:
                    ledger[key] = {"status": "unavailable", "bytes": 0}
                    _image_ledger(ctx, ledger)
            else:
                failed.append(task)
        ctx.cp["images"] = failed + pending[index + 1:]
        ctx.save()
    if failed:
        raise PartialCaptureError("部分图片未归档，可单独重试当前任务", code="images_incomplete")
    return True


def _image_ledger(ctx, ledger):
    ctx.cp["comment_assets"] = ledger
    if ctx.run.video_id:
        video = ctx.db.get(Video, ctx.run.video_id)
        video.metadata_json = {**(video.metadata_json or {}), "comment_assets": ledger}


def _comment_ledger(ctx, video):
    if video is None:
        return deepcopy(ctx.cp.get("comment_assets", {}))
    if "comment_assets" in (video.metadata_json or {}):
        return deepcopy(video.metadata_json["comment_assets"])
    # Existing comment images/avatars must consume the new budget too. Do not
    # delete or silently exempt pre-upgrade assets when a refresh starts.
    asset_ids = set(ctx.db.scalars(select(CommentAsset.asset_id).join(Comment, Comment.id == CommentAsset.comment_id)
        .where(Comment.video_id == video.id)))
    asset_ids.update(ctx.db.scalars(select(PlatformUser.avatar_asset_id).join(Comment, Comment.author_user_id == PlatformUser.id)
        .where(Comment.video_id == video.id, PlatformUser.avatar_asset_id.is_not(None))))
    asset_ids.update(ctx.db.scalars(select(UserSnapshot.avatar_asset_id).join(Comment, Comment.author_snapshot_id == UserSnapshot.id)
        .where(Comment.video_id == video.id, UserSnapshot.avatar_asset_id.is_not(None))))
    # Legacy inventories can exceed current caps. Keep only aggregate cost plus
    # real existing refs: this ledger itself remains small and never deletes.
    count, size = ctx.db.execute(select(func.count(Asset.id), func.coalesce(func.sum(Asset.size), 0)).where(Asset.id.in_(asset_ids))).one()
    ledger = {"legacy": {"status": "legacy", "bytes": size, "count": count}} if count else {}
    _image_ledger(ctx, ledger)
    return ledger


def _capture_policy(policy):
    defaults = {"quality": "best", "media": True, "comments": True, "danmaku": True, "subtitles": True,
                "auto_subtitles": True, "create_compatible_copy": True, "prefer_h264": False,
                "prefer_dolby_vision": True, "prefer_dolby_atmos": True}
    return {key: policy.get(key, value) for key, value in defaults.items()}


def _enqueue_archive(ctx, video):
    # Cross-folder tasks share the video lock and media demand key; an active
    # archive can serve both collections without another full ingestion task.
    if video.capture_status == "complete" and (video.metadata_json or {}).get("capture_policy") == _capture_policy(ctx.policy):
        return "reused"
    active = ctx.db.scalar(select(Job.id).where(Job.kind.in_(["archive_video", "download_media"]), Job.target_id == video.id,
                          Job.status.in_(["queued", "running", "paused", "blocked"])))
    if active:
        return "active"
    # Exhausted or cancelled work needs an explicit retry; a recurring source
    # scan must not silently reset its failure budget or undo the user's stop.
    stopped = ctx.db.scalar(select(Job.id).where(Job.kind.in_(["archive_video", "download_media"]), Job.target_id == video.id,
                           Job.status.in_(["failed", "cancelled", "partial"])))
    if stopped:
        return "needs_attention"
    key = f"archive:{video.id}:{ctx.run.id}"
    if not ctx.db.scalar(select(Job.id).where(Job.dedupe_key == key)):
        from app.jobs import enqueue
        enqueue(ctx.db, "archive_video", video.id, ctx.job.account_id, dict(ctx.policy), key, frozen_policy=True)
        return "queued"
    return "active"


def _scan(ctx):
    from app.source_monitoring import scan_source
    return scan_source(ctx, user_snapshot=_user, enqueue_archive=_enqueue_archive, now=_now)


def _metadata(ctx, video):
    data = ctx.client.view(video.bvid)
    if str(data.get("bvid")) != video.bvid or not isinstance(data.get("pages"), list) or not data["pages"]:
        raise IngestError("稿件详情缺少分P数据", code="invalid_metadata")
    from app.library_deletion import check_video_allowed
    check_video_allowed(ctx.db, video, uid=str((data.get("owner") or {}).get("mid") or ""))
    video.aid = _id(data.get("aid"))
    video.title, video.description = str(data.get("title") or ""), str(data.get("desc") or "")
    video.duration = float(data.get("duration") or 0)
    video.published_at = _timestamp(data.get("pubdate"))
    source_metadata = clean_raw(data)
    for key in ("media_properties", "source_quality", "ingest_state", "capture_policy", "comment_capture", "comment_assets"):
        if key in (video.metadata_json or {}):
            source_metadata[key] = deepcopy(video.metadata_json[key])
    video.metadata_json, video.source_state = source_metadata, "available"
    from .media import record_access
    record_access(video, data)
    if isinstance(data.get("stat"), dict):
        _save_stats(ctx, video, data["stat"])
    ctx.image(data.get("pic"), "video", video.id, "cover")
    for role, member in [("owner", data.get("owner"))] + [("staff", x) for x in data.get("staff", [])]:
        user, snapshot, creator = _user(ctx, member, creator=True)
        if creator:
            relation = ctx.db.scalar(select(VideoCreator).where(VideoCreator.video_id == video.id, VideoCreator.creator_id == creator.id, VideoCreator.role == role))
            if not relation:
                relation = VideoCreator(video_id=video.id, creator_id=creator.id, role=role)
                ctx.db.add(relation)
            relation.role_title = str(member.get("title") or "UP主")
    current = []
    for number, raw in enumerate(data["pages"], start=1):
        cid = _id(raw.get("cid"))
        part = ctx.db.scalar(select(VideoPart).where(VideoPart.video_id == video.id, VideoPart.cid == cid))
        if not part:
            part = VideoPart(video_id=video.id, cid=cid)
            ctx.db.add(part)
        part.position = int(raw.get("page") or number)
        part.title, part.duration = str(raw.get("part") or ""), float(raw.get("duration") or 0)
        ctx.db.flush()
        current.append(part.id)
    ctx.cp["part_ids"] = current
    ctx.cp["metadata_done"] = True
    ctx.save()


def _save_stats(ctx, video, raw):
    existing = ctx.db.scalar(select(VideoStatSnapshot).where(VideoStatSnapshot.video_id == video.id,
                                                             VideoStatSnapshot.run_id == ctx.run.id))
    if existing:
        return existing
    counts = {key: value if type(value := raw.get(key)) is int and value >= 0 else None
              for key in ("view", "like", "coin", "favorite", "share", "reply", "danmaku")}
    snapshot = VideoStatSnapshot(video_id=video.id, run_id=ctx.run.id, observed_at=_now(), counts=counts)
    ctx.db.add(snapshot)
    ctx.db.flush()
    return snapshot


def _refresh_stats(ctx, video):
    if not ctx.cp.get("stats_done"):
        ctx.guard()
        data = ctx.client.view(video.bvid)
        if data.get("bvid") != video.bvid or not isinstance(data.get("stat"), dict):
            raise IngestError("稿件统计数据无效", code="invalid_stats")
        if video.aid and str(data.get("aid")) != video.aid:
            raise IngestError("统计返回稿件与保存记录不匹配", code="invalid_stats")
        if ctx.policy.get("refresh_danmaku", False):
            if not isinstance(data.get("pages"), list):
                raise IngestError("统计刷新缺少当前分P信息", code="invalid_metadata")
            cids = {_id(page.get("cid")) for page in data["pages"]}
            ctx.cp["stats_part_ids"] = list(ctx.db.scalars(select(VideoPart.id).where(
                VideoPart.video_id == video.id, VideoPart.cid.in_(cids))).all())
            # This identifies the source, without refreshing titles or people.
            if not video.aid:
                video.aid = _id(data.get("aid"))
        snapshot = _save_stats(ctx, video, data["stat"])
        ctx.cp["stats_snapshot_id"], ctx.cp["stats_done"] = snapshot.id, True
        ctx.run.scope = {**(ctx.run.scope or {}), "stats": True,
                         "danmaku": bool(ctx.policy.get("refresh_danmaku", False)), "media": False}
        ctx.save()
    if ctx.policy.get("refresh_danmaku", False):
        for part_id in ctx.cp.get("stats_part_ids", []):
            part = ctx.db.get(VideoPart, part_id)
            if not _danmaku(ctx, video, part):
                return False
    return True


def _save_comment(ctx, video, raw, expected_root=None):
    rpid = _id(raw.get("rpid_str", raw.get("rpid")))
    root = _id(raw.get("root_str", raw.get("root", 0)), required=False)
    parent = _id(raw.get("parent_str", raw.get("parent", 0)), required=False)
    if expected_root is not None and root != expected_root:
        raise IngestError("楼中楼返回了不匹配的根评论", code="invalid_comments")
    if raw.get("oid") is not None and _id(raw["oid"]) != video.aid:
        raise IngestError("评论不属于当前稿件", code="invalid_comments")
    body = raw.get("content")
    if not isinstance(body, dict) or not isinstance(body.get("message"), str):
        raise IngestError("评论正文结构无效", code="invalid_comments")
    user, snapshot, _ = _user(ctx, raw.get("member"), comment_assets=True)
    comment = ctx.db.scalar(select(Comment).where(Comment.video_id == video.id, Comment.rpid == rpid))
    if not comment:
        comment = Comment(video_id=video.id, rpid=rpid)
        ctx.db.add(comment)
    prior_manifest = {(item.get("purpose"), item.get("token"), item.get("source_url")): item.get("asset_id")
        for item in (comment.raw or {}).get("asset_manifest", []) if isinstance(item, dict) and item.get("asset_id")}
    comment.root_rpid, comment.parent_rpid = root, parent
    comment.author_user_id = user.id if user else None
    comment.author_snapshot_id = snapshot.id if snapshot else None
    comment.content, comment.posted_at = body["message"], _timestamp(raw.get("ctime"))
    comment.like_count = max(0, int(raw.get("like") or 0))
    comment.reply_count = max(0, int(raw.get("rcount") or raw.get("count") or 0))
    from .comment_capture import compact
    comment.raw = compact(raw)
    ctx.db.flush()
    if not ctx.db.scalar(select(CommentVersion.id).where(CommentVersion.comment_id == comment.id, CommentVersion.run_id == ctx.run.id)):
        ctx.db.add(CommentVersion(comment_id=comment.id, run_id=ctx.run.id, content=comment.content, like_count=comment.like_count, author_snapshot_id=comment.author_snapshot_id, raw=comment.raw))
    manifest = []
    for purpose, token, url in [("attachment", None, p.get("img_src")) for p in (body.get("pictures") or [])[:20]] + [
            ("emote", token, emote.get("url")) for token, emote in list((body.get("emote") or {}).items())[:100]]:
        if url:
            url = safe_source_url("https:" + url if url.startswith("//") else url)
            entry = {"purpose": purpose, "token": token, "source_url": url}
            if (purpose, token, url) in prior_manifest:
                entry["asset_id"] = prior_manifest[(purpose, token, url)]
            manifest.append(entry)
            ctx.image(url, "comment", comment.id, purpose, comment_asset=True)
    comment.raw = {**comment.raw, "asset_manifest": manifest}
    return comment


def _pinned(data):
    results = list(data.get("top_replies") or [])
    top = data.get("top") or {}
    if isinstance(top, dict):
        for item in top.values():
            if isinstance(item, dict) and item.get("rpid"):
                results.append(item)
    return results


def _comments(ctx, video):
    from .comment_capture import collect
    return collect(ctx, video, _save_comment, _pinned)


def _danmaku(ctx, video, part):
    state = dict(ctx.cp.get("danmaku", {}))
    checkpoint = dict(state.get(part.id, {}))
    if checkpoint.get("done"):
        return True
    if not math.isfinite(part.duration) or part.duration <= 0:
        raise IngestError("分P时长无效，无法确认弹幕覆盖范围", code="invalid_duration", retryable=False)
    expected = max(1, math.ceil(part.duration / 360))
    if expected > 10000:
        raise IngestError("弹幕分段数超过单视频上限", code="invalid_duration", retryable=False)
    snapshot = ctx.db.scalar(select(DanmakuSnapshot).where(DanmakuSnapshot.part_id == part.id, DanmakuSnapshot.run_id == ctx.run.id))
    if not snapshot:
        snapshot = DanmakuSnapshot(part_id=part.id, run_id=ctx.run.id, expected_segments=expected, status="running", raw_asset_ids=[])
        ctx.db.add(snapshot)
        ctx.db.flush()
    # Store normalized fragments in the checkpoint as asset IDs, never millions
    # of lines in job JSON. Final merge reads those local/remote assets below.
    fragments = list(checkpoint.get("fragments", []))
    while len(fragments) < expected:
        if not ctx.request_slot():
            return False
        raw = ctx.client.segment(video.aid, part.cid, len(fragments) + 1)
        parsed = decode_danmaku(raw)
        source = ctx.bytes_asset(raw, "danmaku_raw", "application/x-protobuf")
        converted = ctx.bytes_asset(json.dumps(parsed, ensure_ascii=False).encode(), "danmaku_fragment", "application/json")
        _ref(ctx.db, source.id, "danmaku_snapshot", snapshot.id, "raw")
        _ref(ctx.db, converted.id, "danmaku_snapshot", snapshot.id, "fragment")
        fragments.append(converted.id)
        snapshot.raw_asset_ids = [*(snapshot.raw_asset_ids or []), source.id]
        snapshot.completed_segments = len(fragments)
        snapshot.count = int(snapshot.count or 0) + len(parsed)
        checkpoint["fragments"] = fragments
        state[part.id], ctx.cp["danmaku"] = checkpoint, state
        ctx.save()
    from app.storage.service import materialize_asset
    with tempfile.TemporaryDirectory(prefix="treasure-danmaku-", dir=settings.scratch_dir) as temp:
        directory = Path(temp)
        # Memory is bounded by one source segment. The rebuildable scratch DB
        # keeps last-seen IDs and sorting off the worker's Python heap.
        with closing(sqlite3.connect(directory / "merge.sqlite")) as merged:
            merged.execute("PRAGMA journal_mode=OFF")
            merged.execute("PRAGMA synchronous=OFF")
            merged.execute("PRAGMA temp_store=FILE")
            merged.execute("CREATE TABLE items (id TEXT PRIMARY KEY, time REAL NOT NULL, body TEXT NOT NULL)")
            merged.execute("CREATE INDEX item_order ON items (time, id)")
            for asset_id in fragments:
                ctx.guard()
                path = directory / asset_id
                materialize_asset(ctx.db, asset_id, path)
                items = json.loads(path.read_text(encoding="utf-8"))
                merged.executemany("INSERT OR REPLACE INTO items (id, time, body) VALUES (?, ?, ?)",
                    ((item["id"], item["time"], json.dumps(item, ensure_ascii=False)) for item in items))
                merged.commit()
                del items
                path.unlink()
            output = directory / "danmaku.json"
            count = 0
            ctx.guard()
            with output.open("w", encoding="utf-8", newline="") as target:
                target.write("[")
                for (body,) in merged.execute("SELECT body FROM items ORDER BY time, id"):
                    if count % 1000 == 0:
                        ctx.guard()
                    target.write((", " if count else "") + body)
                    count += 1
                target.write("]")
            ctx.guard()
            asset = ingest_file(ctx.db, output, kind="danmaku", mime_type="application/json",
                                profile_id=ctx.policy.get("storage_profile_id"))
    snapshot.data_asset_id, snapshot.count, snapshot.status = asset.id, count, "visible_traversal_complete"
    _ref(ctx.db, asset.id, "danmaku_snapshot", snapshot.id, "playback")
    checkpoint["done"] = True
    state[part.id], ctx.cp["danmaku"] = checkpoint, state
    ctx.save()
    return True


def subtitle_vtt(data):
    body = data.get("body") if isinstance(data, dict) else None
    if not isinstance(body, list):
        raise IngestError("字幕正文结构无效", code="invalid_subtitle")
    def stamp(value):
        milliseconds = round(value * 1000)
        hours, remainder = divmod(milliseconds, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        seconds, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"
    rows = ["WEBVTT", ""]
    for number, cue in enumerate(body, 1):
        try:
            start, end = float(cue["from"]), float(cue["to"])
            content = cue["content"]
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start or not isinstance(content, str):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise IngestError("字幕时间轴无效", code="invalid_subtitle") from None
        content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "").replace("\n\n", "\n")
        rows.extend([str(number), f"{stamp(start)} --> {stamp(end)}", content, ""])
    return "\n".join(rows).encode()


def _subtitles(ctx, video, part):
    completed = list(ctx.cp.get("subtitle_parts", []))
    if part.id in completed:
        return
    player = ctx.client.player(video.aid, part.cid)
    if player.get("need_login_subtitle"):
        raise IngestError("当前账号无法读取字幕", code="login_required", retryable=False)
    subtitle = player.get("subtitle")
    if not isinstance(subtitle, dict) or not isinstance(subtitle.get("subtitles"), list):
        raise IngestError("字幕接口缺少轨道清单", code="invalid_subtitle")
    for track in subtitle["subtitles"]:
        auto = str(track.get("lan", "")).startswith("ai-") or bool(track.get("ai_type"))
        if auto and not ctx.policy.get("auto_subtitles", True):
            continue
        ctx.guard()
        raw, mime = ctx.client.asset(track.get("subtitle_url", ""))
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            raise IngestError("字幕文件不是合法 JSON", code="invalid_subtitle") from None
        vtt = subtitle_vtt(data)
        source = ctx.bytes_asset(json.dumps(clean_raw(data), ensure_ascii=False).encode(), "subtitle_raw", "application/json")
        converted = ctx.bytes_asset(vtt, "subtitle", "text/vtt")
        language = str(track.get("lan") or "und")
        saved = ctx.db.scalar(select(SubtitleTrack).where(SubtitleTrack.part_id == part.id, SubtitleTrack.language == language, SubtitleTrack.is_auto == auto))
        if not saved:
            saved = SubtitleTrack(part_id=part.id, language=language, is_auto=auto)
            ctx.db.add(saved)
        saved.label = str(track.get("lan_doc") or language)
        saved.raw_asset_id, saved.asset_id = source.id, converted.id
        ctx.db.flush()
        _ref(ctx.db, source.id, "subtitle", saved.id, "raw")
        _ref(ctx.db, converted.id, "subtitle", saved.id, "playback")
    completed.append(part.id)
    ctx.cp["subtitle_parts"] = completed
    ctx.save()


def _raise_capture_errors(ctx, errors):
    if errors:
        components = list(dict.fromkeys(error.code for error in errors))
        ctx.cp["component_errors"] = components
        # Once core metadata is confirmed, non-auth component failures must not
        # starve the separately resumable media lane. Keep the parent partial;
        # its retries still repair the exact comment/danmaku checkpoints.
        if (ctx.job.kind == "archive_video" and ctx.cp.get("metadata_done") and ctx.cp.get("basics_done")
                and ctx.cp.get("part_ids") and ctx.policy.get("media", True) and not any(_halt(error) for error in errors)):
            video = ctx.db.get(Video, ctx.run.video_id)
            if video:
                _enqueue_media(ctx, video, metadata_state="partial")
                _ingest_state(ctx, video, component_errors=components)
        ctx.job.result = {"components": components, "media_job_id": ctx.cp.get("media_job_id")}
        ctx.save()
        raise PartialCaptureError("部分归档组件尚未完成：" + ", ".join(dict.fromkeys(error.code for error in errors)),
                                  retryable=any(error.retryable for error in errors),
                                  blocked=any(error.blocked for error in errors))


def _ingest_state(ctx, video, **values):
    metadata = dict(video.metadata_json or {})
    metadata["ingest_state"] = {**metadata.get("ingest_state", {}), **values, "updated_at": _now().isoformat()}
    video.metadata_json = metadata


def _playback_state(ctx, video, archive, job, status=None):
    state = (video.metadata_json or {}).get("ingest_state", {})
    children = dict(state.get("playback_jobs", {}))
    status = status or ("complete" if job.status == "succeeded" else job.status)
    children[archive.id] = {"job_id": job.id, "part_id": archive.part_id, "status": status}
    statuses = {item["status"] for item in children.values()}
    aggregate = next((value for value in ("failed", "partial", "blocked", "cancelled", "paused", "running", "queued") if value in statuses), "complete")
    _ingest_state(ctx, video, playback=aggregate, playback_job_id=job.id, playback_jobs=children)


def _profiles(ctx, video):
    if not ctx.policy.get("profiles", True) or ctx.cp.get("profiles_done"):
        return True
    ids = ctx.db.scalars(select(PlatformUser.uid).join(Creator, Creator.user_id == PlatformUser.id)
        .join(VideoCreator, VideoCreator.creator_id == Creator.id).where(VideoCreator.video_id == video.id)).all()
    completed = list(ctx.cp.get("profile_uids", []))
    for uid in sorted(set(ids)):
        if uid in completed:
            continue
        if not ctx.request_slot():
            return False
        _user(ctx, ctx.client.profile(uid), creator=True, full_profile=True)
        completed.append(uid)
        ctx.cp["profile_uids"] = completed
        ctx.save()
    ctx.cp["profiles_done"] = True
    ctx.save()
    return True


def _enqueue_media(ctx, video, *, metadata_state="ready"):
    from app.jobs import enqueue
    # A deterministic child survives redelivery without resetting a paused,
    # cancelled or exhausted task. Existing media checkpoints are transferred
    # once when upgrading pre-split archive tasks.
    key = "download:" + ctx.job.id
    child = ctx.db.scalar(select(Job).where(Job.dedupe_key == key))
    if not child:
        child = enqueue(ctx.db, "download_media", video.id, ctx.job.account_id,
                        dict(ctx.policy), key, frozen_policy=True)
        child.checkpoint = {key: deepcopy(ctx.cp[key]) for key in
            ("part_ids", "media_parts", "media_variants", "playback_parts") if key in ctx.cp}
        child.checkpoint = {**child.checkpoint, "metadata_parent_id": ctx.job.id}
    ctx.cp["media_job_id"] = child.id
    state = (video.metadata_json or {}).get("ingest_state", {})
    status = "complete" if child.status == "succeeded" or (state.get("media_job_id") == child.id and state.get("media") == "complete") else child.status
    _ingest_state(ctx, video, metadata=metadata_state, media=status, media_job_id=child.id)
    return child


def _archive(ctx, video):
    """Fast metadata slices; never download or transcode media in this job."""
    _ingest_state(ctx, video, metadata="running")
    if not ctx.cp.get("metadata_done"):
        ctx.stage("metadata")
        _metadata(ctx, video)
    errors = []
    if not ctx.cp.get("basics_done"):
        ctx.stage("profiles")
        try:
            if not _profiles(ctx, video):
                return False
        except IngestError as error:
            if _halt(error):
                raise
            errors.append(error)
        ctx.stage("cover_and_avatars", pending=len(ctx.cp["images"]))
        try:
            if not _images(ctx):
                _raise_capture_errors(ctx, errors)
                return False
        except IngestError as error:
            if _halt(error):
                raise
            errors.append(error)
        _raise_capture_errors(ctx, errors)
        ctx.cp["basics_done"] = True
        ctx.stage("basics_ready")
        # Let the remaining collection videos publish their cards and people
        # before this video's potentially large comment/danmaku traversal.
        if ctx.policy.get("comments", True) or ctx.policy.get("danmaku", True) or ctx.policy.get("subtitles", True):
            return False
    for part_id in ctx.cp.get("part_ids", []):
        ctx.guard()
        part = ctx.db.get(VideoPart, part_id)
        if ctx.policy.get("danmaku", True):
            ctx.stage("danmaku", part_id=part_id)
            try:
                done = _danmaku(ctx, video, part)
            except IngestError as error:
                if _halt(error):
                    raise
                errors.append(error)
            else:
                if not done:
                    _raise_capture_errors(ctx, errors)
                    return False
        if ctx.policy.get("subtitles", True) and part_id not in ctx.cp.get("subtitle_parts", []):
            if not ctx.request_slot():
                _raise_capture_errors(ctx, errors)
                return False
            ctx.stage("subtitles", part_id=part_id)
            try:
                _subtitles(ctx, video, part)
            except IngestError as error:
                if _halt(error):
                    raise
                errors.append(error)
    comments_done = True
    if ctx.policy.get("comments", True):
        ctx.stage("comments")
        try:
            comments_done = _comments(ctx, video)
        except IngestError as error:
            if _halt(error):
                raise
            errors.append(error)
    # Drain a bounded batch every slice, including when comments need another
    # cursor page; otherwise avatars can accumulate indefinitely behind roots.
    if ctx.cp["images"]:
        ctx.stage("images", pending=len(ctx.cp["images"]))
    try:
        images_done = _images(ctx)
    except IngestError as error:
        if _halt(error):
            raise
        errors.append(error)
        images_done = False
    _raise_capture_errors(ctx, errors)
    if not comments_done or not images_done:
        return False
    ctx.cp.pop("component_errors", None)
    _ingest_state(ctx, video, component_errors=[])
    if ctx.policy.get("media", True):
        _enqueue_media(ctx, video)
    else:
        _ingest_state(ctx, video, metadata="ready", media="disabled")
    ctx.stage("metadata_ready", media_job_id=ctx.cp.get("media_job_id"))
    return True


def _download(ctx, video):
    from app.jobs import enqueue
    _ingest_state(ctx, video, media="running")
    completed = list(ctx.cp.get("media_parts", []))
    archives = dict(ctx.cp.get("media_variants", {}))
    for part_id in ctx.cp.get("part_ids", []):
        ctx.guard()
        part = ctx.db.get(VideoPart, part_id)
        if not part or part.video_id != video.id:
            raise IngestError("媒体分P检查点无效", code="invalid_metadata", retryable=False)
        if part_id not in completed:
            ctx.stage("download", part_id=part_id, completed_parts=len(completed), total_parts=len(ctx.cp["part_ids"]))
            variant, reused = archive_media(ctx.db, ctx.client, video, part, ctx.policy, guard=ctx.guard)
            _ref(ctx.db, variant.asset_id, "media_variant", variant.id, "archive")
            completed.append(part_id)
            archives[part_id] = variant.id
            ctx.cp["media_parts"], ctx.cp["media_variants"] = completed, archives
            ctx.save()
            cleanup_media_scratch(part.id, variant.format_key)
        variant = ctx.db.get(MediaVariant, archives.get(part_id)) if archives.get(part_id) else None
        if variant is None:
            raise IngestError("原档检查点对应媒体不存在", code="missing_archive", retryable=False)
        if ctx.policy.get("create_compatible_copy", True):
            playback = enqueue(ctx.db, "create_playback", variant.id, policy=dict(ctx.policy),
                dedupe_key="playback:" + variant.id, frozen_policy=True)
            _playback_state(ctx, video, variant, playback)
            ctx.save()
    if not completed:
        raise IngestError("媒体任务缺少分P检查点", code="invalid_metadata", retryable=False)
    _ingest_state(ctx, video, media="complete")
    ctx.stage("media_ready", completed_parts=len(completed))
    return True


def _create_playback(db, job):
    ctx = Context(db, job, None)
    archive = db.get(MediaVariant, job.target_id)
    part = db.get(VideoPart, archive.part_id) if archive else None
    video = db.get(Video, part.video_id) if part else None
    if not archive or archive.kind != "archive" or not video:
        raise IngestError("原始归档不存在", code="missing_archive", retryable=False)
    ctx.run.video_id = video.id
    with _lock(db, "video:" + video.id):
        try:
            ctx.stage("compatible_copy", part_id=part.id)
            playback, reused = ensure_playback_variant(db, part, archive, ctx.policy, guard=ctx.guard)
            _ref(db, playback.asset_id, "media_variant", playback.id, "playback")
            _playback_state(ctx, video, archive, job, "complete")
            ctx.run.status, ctx.run.finished_at = "visible_traversal_complete", _now()
            ctx.stage("compatible_ready", variant_id=playback.id)
        except IngestError as error:
            if error.code == "job_stopped":
                raise
            ctx.run.status, ctx.run.end_reason = "partial", error.code
            _playback_state(ctx, video, archive, job, "partial" if error.retryable else "failed")
            _ingest_state(ctx, video, playback_error=error.code)
            ctx.save()
            raise
    return {"video_id": video.id, "variant_id": playback.id, "run_id": ctx.run.id}


def run_job(db, job):
    """Return a safe result; continuation=True means requeue, never success."""
    from app.library_deletion import check_job_allowed
    check_job_allowed(db, job)
    if job.kind == "create_playback":
        return _create_playback(db, job)
    account = db.get(SourceAccount, job.account_id) if job.account_id else None
    if not account:
        raise IngestError("采集任务缺少可用账号", code="login_required", retryable=False)
    client, ctx, pacer = None, None, None
    try:
        downloading = job.kind == "download_media"
        with _lock(db, "download-account:" + account.id) if downloading else nullcontext():
            # Only bulk downloads hold an account lane. Credential snapshots
            # separately share the short mutex used by account updates.
            with credential_lock(db, account.id):
                db.refresh(account)
                try:
                    secret = decrypt_secret(account.secret_encrypted)
                except Exception:
                    raise IngestError("账号凭据无法解密", code="invalid_cookie", retryable=False) from None
                initial_generation = (account.id, hashlib.sha256(account.secret_encrypted.encode()).hexdigest())
            client = BiliClient(secret, interval=0)
            client.credential_generation = initial_generation
            ctx = Context(db, job, client)
            client.asset_interval = max(0.05, min(5, float(ctx.policy.get("asset_interval_seconds", 0.1))))
            pacer = AccountPacer(db, account, ctx.policy, ctx.guard)
            pacer.check_cooldown()
            client.before_request, client.before_video = pacer.before_request, pacer.before_video
            client.media_progress = ctx.media_progress
            def request_context(url):
                return account_request_scope(db, account, client, pacer, url)
            def before_video():
                # download-account already serializes reservations. Never hold
                # the credential mutex over pacing, guards or capture commits.
                db.refresh(account, attribute_names=["next_video_at", "cooldown_until", "risk_failures"])
                pacer.before_video(defer=downloading)
            client.request_context, client.before_video = request_context, before_video
            ctx.guard()
            ctx.save()
            # Public endpoints may keep responding after expiry. Verify first so
            # an expired premium session cannot silently become an anonymous job.
            nav = client.nav()
            update_account_identity(db, account, client.credential_generation, nav=nav)
            if job.kind == "verify_account":
                result = {"uid": account.uid, "logged_in": True, "vip": bool((nav.get("vip") or {}).get("status"))}
                done = True
            elif job.kind == "scan_collection":
                with _lock(db, "source:" + job.target_id):
                    done = _scan(ctx)
                    if done:
                        done = _images(ctx)
                result = {"collection_id": job.target_id,
                          "monitor_state": dict((db.get(Collection, job.target_id).monitor_state or {}))}
            elif job.kind in ("archive_video", "download_media", "refresh_comments", "refresh_stats"):
                video = db.get(Video, job.target_id)
                if video is None and job.kind == "archive_video" and re.fullmatch(r"BV[A-Za-z0-9]{10}", job.target_id):
                    video = db.scalar(select(Video).where(Video.bvid == job.target_id))
                    if video is None:
                        video = Video(bvid=job.target_id)
                        db.add(video)
                        db.flush()
                if not video:
                    raise IngestError("归档稿件不存在", code="invalid_video", retryable=False)
                ctx.run.video_id = video.id
                ctx.save()
                with _lock(db, "video:" + video.id):
                    if job.kind == "archive_video":
                        done = _archive(ctx, video)
                    elif job.kind == "download_media":
                        done = _download(ctx, video)
                    elif job.kind == "refresh_stats":
                        done = _refresh_stats(ctx, video)
                    else:
                        if not video.aid:
                            _metadata(ctx, video)
                        done = _comments(ctx, video)
                        if done:
                            done = _images(ctx)
                    if job.kind in ("archive_video", "download_media"):
                        state = (video.metadata_json or {}).get("ingest_state", {})
                        media_pending = job.kind == "archive_video" and ctx.policy.get("media", True) and state.get("media") != "complete"
                        metadata_partial = job.kind == "download_media" and state.get("metadata") in {"partial", "running"}
                        video.capture_status = "partial" if not done or metadata_partial else "metadata_ready" if media_pending else "complete"
                        if done and not media_pending:
                            video.metadata_json = {**(video.metadata_json or {}), "capture_policy": _capture_policy(ctx.policy)}
                result = {"video_id": video.id, "media_job_id": ctx.cp.get("media_job_id")}
            else:
                raise IngestError("采集任务类型未支持", code="unsupported_job", retryable=False)
            ctx.run.status = "visible_traversal_complete" if done else "partial"
            ctx.run.end_reason = "normal_end" if done else "page_budget"
            comments = ctx.cp.get("comments", {})
            if done and (comments.get("end_reason") == "scan_budget" or comments.get("replies_limited") or ctx.cp.get("comment_assets_limited")):
                ctx.run.status, ctx.run.end_reason = "bounded_complete", "comment_policy_budget"
            if done and ctx.cp.get("unavailable_images"):
                ctx.run.status, ctx.run.end_reason = "bounded_complete", "source_images_unavailable"
                result["unavailable_images"] = len(ctx.cp["unavailable_images"])
            if job.kind == "scan_collection" and done:
                scan = ctx.cp.get("source_scan", {})
                ctx.run.status = "visible_traversal_complete" if scan.get("status") == "complete" else scan.get("status", "partial")
                ctx.run.end_reason = scan.get("end_reason", "normal_end")
                collection = db.get(Collection, job.target_id)
                collection.monitor_state = {**(collection.monitor_state or {}), "status": scan.get("status", "partial"),
                                            "end_reason": ctx.run.end_reason}
                result["monitor_state"] = dict(collection.monitor_state)
            ctx.run.finished_at = _now() if done else None
            if done:
                db.refresh(account, attribute_names=["cooldown_until", "risk_failures"])
                pacer.succeeded()
            ctx.save()
            return {**result, "run_id": ctx.run.id, "continuation": not done}
    except IngestError as error:
        if pacer and error.code == "rate_limited" and not getattr(error, "account_backoff_applied", False):
            pacer.failed(error)
        if ctx:
            if job.kind == "scan_collection":
                from app.source_monitoring import scan_error
                scan_error(ctx, error)
            ctx.run.status = "blocked" if error.blocked else "partial"
            ctx.run.end_reason = error.code
            if ctx.run.video_id and job.kind in ("archive_video", "download_media"):
                video = db.get(Video, ctx.run.video_id)
                if video:
                    video.capture_status = "partial"
            if error.code in ("login_required", "invalid_cookie"):
                update_account_identity(db, account, client.credential_generation, invalid=True)
            ctx.save()
        raise
    except Exception:
        db.rollback()
        raise IngestError("采集处理发生内部错误，已保留最后提交的检查点", code="internal_ingest_error") from None
    finally:
        if client is not None:
            client.close()
