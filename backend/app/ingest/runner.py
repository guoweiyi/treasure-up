"""Resumable ingestion using durable SQL checkpoints and immutable assets.

No source request is made by importing this module. run_job is worker-only.
"""
import hashlib
import json
import math
import re
import tempfile
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from app.config import settings
from app.models import (AssetRef, CaptureRun, Collection, CollectionItem, Comment,
    CommentAsset, CommentVersion, Creator, DanmakuSnapshot, Job, MediaVariant, OutboxEvent,
    PlatformUser, SourceAccount, SourceSubscription, SubtitleTrack, UserSnapshot,
    Video, VideoCreator, VideoPart)
from app.security import decrypt_secret
from app.storage.service import ingest_file
from .client import BiliClient, clean_raw, safe_source_url
from .errors import IngestError, PartialCaptureError
from .media import archive_media, cleanup_media_scratch, ensure_playback_variant
from .protobuf import decode_danmaku


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
            raise IngestError("相同账号或视频已有采集任务，稍后重试", code="resource_busy")
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def _ref(db, asset_id, entity, entity_id, purpose):
    query = select(AssetRef).where(AssetRef.asset_id == asset_id, AssetRef.entity_type == entity, AssetRef.entity_id == entity_id, AssetRef.purpose == purpose)
    if not db.scalar(query):
        db.add(AssetRef(asset_id=asset_id, entity_type=entity, entity_id=entity_id, purpose=purpose))


class Context:
    def __init__(self, db, job, client):
        self.db, self.job, self.client = db, job, client
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
        self.run = db.get(CaptureRun, self.cp.get("run_id")) if self.cp.get("run_id") else None
        if not self.run:
            self.run = CaptureRun(scope={"kind": job.kind, "account_id": job.account_id}, status="running")
            db.add(self.run)
            db.flush()
            self.cp["run_id"] = self.run.id
        self.run.status = "running"
        self.run.finished_at = None

    def guard(self):
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
        if self.pages >= self.budget:
            return False
        self.pages += 1
        return True

    def image(self, url, entity, entity_id, purpose="avatar"):
        if not url or not self.policy.get("images", True):
            return
        url = safe_source_url("https:" + url if url.startswith("//") else url)
        item = {"url": url, "entity": entity, "id": entity_id, "purpose": purpose}
        if item not in self.cp["images"]:
            self.cp["images"].append(item)

    def bytes_asset(self, data, kind, mime):
        settings.scratch_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="treasure-data-", dir=settings.scratch_dir) as temp:
            path = Path(temp) / "payload"
            path.write_bytes(data)
            return ingest_file(self.db, path, kind=kind, mime_type=mime, profile_id=self.policy.get("storage_profile_id"))


def _user(ctx, raw, *, creator=False):
    if not isinstance(raw, dict):
        return None, None, None
    uid = _id(raw.get("mid"), required=False)
    if uid == "0":
        return None, None, None
    db = ctx.db
    user = db.scalar(select(PlatformUser).where(PlatformUser.uid == uid))
    if not user:
        user = PlatformUser(uid=uid, display_name="", signature="", raw={})
        db.add(user)
        db.flush()
    name = raw.get("uname", raw.get("name", user.display_name))
    signature = raw.get("sign", user.signature)
    avatar = raw.get("avatar", raw.get("face", (user.raw or {}).get("avatar_url")))
    avatar = safe_source_url("https:" + avatar if avatar.startswith("//") else avatar) if avatar else None
    changed = name != user.display_name or signature != user.signature or avatar != (user.raw or {}).get("avatar_url")
    user.display_name, user.signature = str(name or ""), str(signature or "")
    if avatar != (user.raw or {}).get("avatar_url"):
        user.avatar_asset_id = None
    user.raw = {"avatar_url": avatar}
    snapshot = None if changed else db.scalar(select(UserSnapshot).where(UserSnapshot.user_id == user.id).order_by(UserSnapshot.observed_at.desc()))
    if not snapshot:
        snapshot = UserSnapshot(user_id=user.id, display_name=user.display_name, signature=user.signature, avatar_asset_id=user.avatar_asset_id, observed_at=_now())
        db.add(snapshot)
        db.flush()
    if avatar and not user.avatar_asset_id:
        ctx.image(avatar, "user", user.id)
        ctx.image(avatar, "user_snapshot", snapshot.id)
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
    maximum = max(1, min(1000, int(ctx.policy.get("image_budget", 100))))
    for index, task in enumerate(pending):
        if index >= maximum:
            ctx.cp["images"] = failed + pending[index:]
            ctx.save()
            return False
        ctx.guard()
        try:
            if task["url"] in cache:
                asset = cache[task["url"]]
            else:
                body, mime = ctx.client.asset(task["url"], limit=10 * 1024 * 1024)
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
            cls = {"user": PlatformUser, "user_snapshot": UserSnapshot, "video": Video}.get(task["entity"])
            if cls:
                target = ctx.db.get(cls, task["id"])
                if target:
                    setattr(target, "cover_asset_id" if cls is Video else "avatar_asset_id", asset.id)
            elif task["entity"] == "comment":
                if not ctx.db.scalar(select(CommentAsset).where(CommentAsset.comment_id == task["id"], CommentAsset.asset_id == asset.id, CommentAsset.kind == task["purpose"])):
                    ctx.db.add(CommentAsset(comment_id=task["id"], asset_id=asset.id, kind=task["purpose"], position=task.get("position", 0)))
            _ref(ctx.db, asset.id, task["entity"], task["id"], task["purpose"])
        except IngestError as error:
            if error.blocked:
                raise
            failed.append(task)
        ctx.cp["images"] = failed + pending[index + 1:]
        ctx.save()
    if failed:
        raise PartialCaptureError("部分图片未归档，可单独重试当前任务", code="images_incomplete")
    return True


def _enqueue_archive(ctx, video):
    # Cross-folder tasks share the video lock and media demand key; an active
    # archive can serve both collections without another full ingestion task.
    active = ctx.db.scalar(select(Job.id).where(Job.kind == "archive_video", Job.target_id == video.id, Job.status.in_(["queued", "running"])))
    if active:
        return
    key = f"archive:{video.id}:{ctx.run.id}"
    if not ctx.db.scalar(select(Job.id).where(Job.dedupe_key == key)):
        from app.jobs import enqueue
        enqueue(ctx.db, "archive_video", video.id, ctx.job.account_id, dict(ctx.policy), key, frozen_policy=True)


def _scan(ctx):
    collection = ctx.db.get(Collection, ctx.job.target_id)
    if not collection or collection.kind != "favorite":
        raise IngestError("收藏来源不存在或类型未支持", code="invalid_source", retryable=False)
    ctx.run.collection_id = collection.id
    if ctx.cp.get("collection_done"):
        return True
    page = int(ctx.cp.get("page", 1))
    fingerprints = list(ctx.cp.get("page_fingerprints", []))
    while ctx.request_slot():
        data = ctx.client.favorite_page(collection.source_id, page)
        if not isinstance(data, dict) or not isinstance(data.get("has_more"), (bool, int)) or data.get("has_more") not in (0, 1, True, False):
            raise IngestError("收藏分页缺少合法结束标记", code="invalid_pagination")
        items = data.get("medias")
        if items is None and not data["has_more"]:
            items = []
        if not isinstance(items, list) or (not items and data["has_more"]):
            raise IngestError("收藏分页提前返回空页", code="invalid_pagination")
        fingerprint = hashlib.sha256(json.dumps([(x.get("id"), x.get("type")) for x in items]).encode()).hexdigest()
        if items and fingerprint in fingerprints:
            raise IngestError("收藏分页重复，已保留检查点", code="pagination_loop")
        info = data.get("info") or {}
        collection.title = str(info.get("title") or collection.title)
        if (info.get("upper") or {}).get("mid"):
            collection.owner_uid = _id(info["upper"]["mid"])
        for index, raw in enumerate(items):
            rid = f"{raw.get('type', 2)}:{_id(raw.get('id'))}"
            item = ctx.db.scalar(select(CollectionItem).where(CollectionItem.collection_id == collection.id, CollectionItem.source_resource_id == rid))
            if not item:
                item = CollectionItem(collection_id=collection.id, source_resource_id=rid)
                ctx.db.add(item)
            item.position = (page - 1) * 20 + index
            item.seen_run_id = ctx.run.id
            bvid = raw.get("bvid")
            if raw.get("type", 2) == 2 and re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid or ""):
                video = ctx.db.scalar(select(Video).where(Video.bvid == bvid))
                if not video:
                    video = Video(bvid=bvid, aid=_id(raw.get("id")), title=str(raw.get("title") or ""), capture_status="pending")
                    ctx.db.add(video)
                    ctx.db.flush()
                item.video_id, item.source_state = video.id, "available"
                if ctx.policy.get("archive", True):
                    _enqueue_archive(ctx, video)
            else:
                item.source_state = "unavailable" if raw.get("type", 2) == 2 else "unsupported"
        fingerprints.append(fingerprint)
        ctx.cp.update(page=page + 1, page_fingerprints=fingerprints)
        ctx.run.counts = {"pages": page, "observed": int((ctx.run.counts or {}).get("observed", 0)) + len(items)}
        # Results, child tasks and continuation position commit together.
        if not data["has_more"]:
            for item in ctx.db.scalars(select(CollectionItem).where(CollectionItem.collection_id == collection.id)):
                if item.seen_run_id != ctx.run.id:
                    item.source_state = "not_observed"
            collection.last_scan_at = _now()
            ctx.cp["collection_done"] = True
            ctx.save()
            return True
        ctx.save()
        page += 1
    return False


def _metadata(ctx, video):
    data = ctx.client.view(video.bvid)
    if str(data.get("bvid")) != video.bvid or not isinstance(data.get("pages"), list) or not data["pages"]:
        raise IngestError("稿件详情缺少分P数据", code="invalid_metadata")
    video.aid = _id(data.get("aid"))
    video.title, video.description = str(data.get("title") or ""), str(data.get("desc") or "")
    video.duration = float(data.get("duration") or 0)
    video.published_at = _timestamp(data.get("pubdate"))
    source_metadata = clean_raw(data)
    if (video.metadata_json or {}).get("media_properties"):
        source_metadata["media_properties"] = deepcopy(video.metadata_json["media_properties"])
    video.metadata_json, video.source_state = source_metadata, "available"
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
    user, snapshot, _ = _user(ctx, raw.get("member"))
    comment = ctx.db.scalar(select(Comment).where(Comment.video_id == video.id, Comment.rpid == rpid))
    if not comment:
        comment = Comment(video_id=video.id, rpid=rpid)
        ctx.db.add(comment)
    comment.root_rpid, comment.parent_rpid = root, parent
    comment.author_user_id = user.id if user else None
    comment.author_snapshot_id = snapshot.id if snapshot else None
    comment.content, comment.posted_at = body["message"], _timestamp(raw.get("ctime"))
    comment.like_count = max(0, int(raw.get("like") or 0))
    comment.reply_count = max(0, int(raw.get("rcount") or raw.get("count") or 0))
    comment.raw = clean_raw(raw)
    ctx.db.flush()
    if not ctx.db.scalar(select(CommentVersion.id).where(CommentVersion.comment_id == comment.id, CommentVersion.run_id == ctx.run.id)):
        ctx.db.add(CommentVersion(comment_id=comment.id, run_id=ctx.run.id, content=comment.content, like_count=comment.like_count, author_snapshot_id=comment.author_snapshot_id, raw=comment.raw))
    for picture in body.get("pictures", []) or []:
        ctx.image(picture.get("img_src"), "comment", comment.id, "image")
    for emote in (body.get("emote") or {}).values():
        ctx.image(emote.get("url"), "comment", comment.id, "emote")
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
    state = dict(ctx.cp.get("comments", {}))
    seen_offsets = list(state.get("seen_offsets", []))
    while not state.get("roots_done"):
        if not ctx.request_slot():
            return False
        offset = state.get("offset", "")
        data = ctx.client.comment_page(video.aid, offset)
        cursor, replies = data.get("cursor"), data.get("replies")
        if not isinstance(cursor, dict) or not isinstance(cursor.get("is_end"), bool):
            raise IngestError("评论分页缺少合法结束标记", code="invalid_pagination")
        if cursor.get("mode", 2) != 2:
            raise IngestError("源站未提供时间序评论，不能确认遍历范围", code="unsupported_comment_order", retryable=False)
        if replies is None and cursor["is_end"]:
            replies = []
        if not isinstance(replies, list) or (not replies and not cursor["is_end"]):
            raise IngestError("评论分页提前返回空页", code="invalid_pagination")
        unique = {}
        for raw in _pinned(data) + replies:
            unique[_id(raw.get("rpid_str", raw.get("rpid")))] = raw
        for raw in unique.values():
            root = _save_comment(ctx, video, raw, "0")
            for preview in raw.get("replies") or []:
                _save_comment(ctx, video, preview, root.rpid)
        if cursor["is_end"]:
            state["roots_done"] = True
        else:
            nxt = (cursor.get("pagination_reply") or {}).get("next_offset")
            if not isinstance(nxt, str) or not nxt or nxt == offset or nxt in seen_offsets:
                raise IngestError("评论游标缺失或重复，已保留上一检查点", code="pagination_loop")
            seen_offsets.append(nxt)
            state.update(offset=nxt, seen_offsets=seen_offsets)
        ctx.cp["comments"] = dict(state)
        ctx.save()
    while not state.get("replies_done"):
        root = ctx.db.scalar(select(Comment).join(CommentVersion, CommentVersion.comment_id == Comment.id).where(Comment.video_id == video.id, Comment.root_rpid == "0", Comment.reply_count > 0, Comment.rpid > state.get("last_root", ""), CommentVersion.run_id == ctx.run.id).order_by(Comment.rpid).limit(1))
        if root is None:
            state["replies_done"] = True
            ctx.cp["comments"] = dict(state)
            ctx.save()
            break
        if not ctx.request_slot():
            return False
        pn = int(state.get("reply_page", 1))
        data = ctx.client.replies_page(video.aid, root.rpid, pn)
        page, replies = data.get("page"), data.get("replies")
        if not isinstance(page, dict) or not all(isinstance(page.get(k), int) for k in ("num", "size", "count")) or page["num"] != pn or page["size"] <= 0 or page["count"] < 0:
            raise IngestError("楼中楼分页结构异常", code="invalid_pagination")
        if replies is None and page["count"] == 0:
            replies = []
        if not isinstance(replies, list) or (not replies and page["count"] > (pn - 1) * page["size"]):
            raise IngestError("楼中楼提前返回空页", code="invalid_pagination")
        fingerprint = hashlib.sha256(json.dumps([_id(raw.get("rpid_str", raw.get("rpid"))) for raw in replies]).encode()).hexdigest()
        prior = list(state.get("reply_fingerprints", []))
        if replies and fingerprint in prior:
            raise IngestError("楼中楼重复返回同一页", code="pagination_loop")
        for raw in replies:
            _save_comment(ctx, video, raw, root.rpid)
        prior.append(fingerprint)
        if pn * page["size"] >= page["count"]:
            state.update(last_root=root.rpid, reply_page=1, reply_fingerprints=[])
        else:
            state.update(reply_page=pn + 1, reply_fingerprints=prior)
        ctx.cp["comments"] = dict(state)
        ctx.save()
    return True


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
    unique = {}
    with tempfile.TemporaryDirectory(prefix="treasure-danmaku-", dir=settings.scratch_dir) as temp:
        for asset_id in fragments:
            path = Path(temp) / asset_id
            materialize_asset(ctx.db, asset_id, path)
            for item in json.loads(path.read_text(encoding="utf-8")):
                unique[item["id"]] = item
    merged = sorted(unique.values(), key=lambda item: (item["time"], item["id"]))
    asset = ctx.bytes_asset(json.dumps(merged, ensure_ascii=False).encode(), "danmaku", "application/json")
    snapshot.data_asset_id, snapshot.count, snapshot.status = asset.id, len(merged), "visible_traversal_complete"
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


def _archive(ctx, video):
    if not ctx.cp.get("metadata_done"):
        ctx.guard()
        _metadata(ctx, video)
    errors = []
    completed = list(ctx.cp.get("media_parts", []))
    playback_completed = dict(ctx.cp.get("playback_parts", {}))
    archives = dict(ctx.cp.get("media_variants", {}))
    for part_id in ctx.cp["part_ids"]:
        part = ctx.db.get(VideoPart, part_id)
        if ctx.policy.get("media", True) and part_id not in completed:
            try:
                variant, reused = archive_media(ctx.db, ctx.client, video, part, ctx.policy, guard=ctx.guard)
                _ref(ctx.db, variant.asset_id, "media_variant", variant.id, "archive")
                completed.append(part_id)
                ctx.cp["media_parts"] = completed
                archives[part_id] = variant.id
                ctx.cp["media_variants"] = archives
                ctx.save()
                cleanup_media_scratch(part.id, variant.format_key)
            except IngestError as error:
                if error.code == "job_stopped" or error.blocked:
                    raise
                errors.append(error)
        if ctx.policy.get("media", True) and part_id in completed and ctx.policy.get("create_compatible_copy", True):
            try:
                variant = ctx.db.get(MediaVariant, archives[part_id]) if part_id in archives else ctx.db.scalar(
                    select(MediaVariant).where(MediaVariant.part_id == part_id, MediaVariant.kind == "archive").order_by(MediaVariant.created_at.desc()).limit(1))
                if variant is None:
                    raise IngestError("原始归档检查点对应的媒体不存在", code="missing_archive", retryable=False)
                if playback_completed.get(part_id) != variant.id:
                    playback, reused = ensure_playback_variant(ctx.db, part, variant, ctx.policy, guard=ctx.guard)
                    _ref(ctx.db, playback.asset_id, "media_variant", playback.id, "playback")
                    playback_completed[part_id] = variant.id
                    ctx.cp["playback_parts"] = playback_completed
                    ctx.save()
            except IngestError as error:
                if error.code == "job_stopped" or error.blocked:
                    raise
                errors.append(error)
        if ctx.policy.get("danmaku", True):
            try:
                if not _danmaku(ctx, video, part):
                    return False
            except IngestError as error:
                if error.code == "job_stopped" or error.blocked:
                    raise
                errors.append(error)
        if ctx.policy.get("subtitles", True):
            try:
                _subtitles(ctx, video, part)
            except IngestError as error:
                if error.code == "job_stopped" or error.blocked:
                    raise
                errors.append(error)
    if ctx.policy.get("profiles", True) and not ctx.cp.get("profiles_done"):
        try:
            ids = ctx.db.scalars(select(PlatformUser.uid).join(Creator, Creator.user_id == PlatformUser.id).join(VideoCreator, VideoCreator.creator_id == Creator.id).where(VideoCreator.video_id == video.id)).all()
            for uid in set(ids):
                ctx.guard()
                _user(ctx, ctx.client.profile(uid), creator=True)
            ctx.cp["profiles_done"] = True
            ctx.save()
        except IngestError as error:
            if error.code == "job_stopped" or error.blocked:
                raise
            errors.append(error)
    if ctx.policy.get("comments", True):
        try:
            if not _comments(ctx, video):
                return False
        except IngestError as error:
            if error.code == "job_stopped" or error.blocked:
                raise
            errors.append(error)
    try:
        if not _images(ctx):
            return False
    except IngestError as error:
        if error.code == "job_stopped" or error.blocked:
            raise
        errors.append(error)
    if errors:
        ctx.job.result = {"components": [error.code for error in errors]}
        raise PartialCaptureError("部分归档组件尚未完成：" + ", ".join(dict.fromkeys(error.code for error in errors)), retryable=any(error.retryable for error in errors), blocked=any(error.blocked for error in errors))
    return True


def run_job(db, job):
    """Return a safe result; continuation=True means requeue, never success."""
    account = db.get(SourceAccount, job.account_id) if job.account_id else None
    if not account:
        raise IngestError("采集任务缺少可用账号", code="login_required", retryable=False)
    try:
        secret = decrypt_secret(account.secret_encrypted)
    except Exception:
        raise IngestError("账号凭据无法解密", code="invalid_cookie", retryable=False) from None
    client = BiliClient(secret, interval=settings.source_request_interval)
    ctx = None
    try:
        with _lock(db, "account:" + account.id):
            ctx = Context(db, job, client)
            ctx.guard()
            ctx.save()
            # Public endpoints may keep responding after expiry. Verify first so
            # an expired premium session cannot silently become an anonymous job.
            nav = client.nav()
            account.uid, account.status, account.last_verified_at = _id(nav.get("mid")), "valid", _now()
            if job.kind == "verify_account":
                result = {"uid": account.uid, "logged_in": True, "vip": bool((nav.get("vip") or {}).get("status"))}
                done = True
            elif job.kind == "scan_collection":
                done, result = _scan(ctx), {"collection_id": job.target_id}
            elif job.kind in ("archive_video", "refresh_comments"):
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
                    else:
                        if not video.aid:
                            _metadata(ctx, video)
                        done = _comments(ctx, video)
                        if done:
                            done = _images(ctx)
                    if job.kind == "archive_video":
                        video.capture_status = "complete" if done else "partial"
                result = {"video_id": video.id}
            else:
                raise IngestError("采集任务类型未支持", code="unsupported_job", retryable=False)
            ctx.run.status = "visible_traversal_complete" if done else "partial"
            ctx.run.end_reason = "normal_end" if done else "page_budget"
            ctx.run.finished_at = _now() if done else None
            ctx.save()
            return {**result, "run_id": ctx.run.id, "continuation": not done}
    except IngestError as error:
        if ctx:
            ctx.run.status = "blocked" if error.blocked else "partial"
            ctx.run.end_reason = error.code
            if ctx.run.video_id:
                video = db.get(Video, ctx.run.video_id)
                if video:
                    video.capture_status = "partial"
            if error.code in ("login_required", "invalid_cookie"):
                account.status = "invalid"
            ctx.save()
        raise
    except Exception:
        db.rollback()
        raise IngestError("采集处理发生内部错误，已保留最后提交的检查点", code="internal_ingest_error") from None
    finally:
        client.close()
