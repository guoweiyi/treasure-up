"""Explicit local-library deletion, with durable suppression and resumable GC.

No source-site mutation is performed. Job checkpoints are the deletion ledger;
small Setting tombstones stop automatic reimports until an administrator clears
them after a successful deletion. Backups and other live references win over GC.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (Asset, AssetLocation, AssetRef, AuditLog, Base, CaptureRun, Collection,
    CollectionItem, Comment, CommentAsset, CommentVersion, Creator, DanmakuSnapshot, Job,
    MediaVariant, PlatformUser, PlaybackSession, Setting, SourceSubscription, StorageObservation,
    StorageProfile, SubtitleTrack, UserSnapshot, Video, VideoAnnotation, VideoCreator, VideoPart,
    VideoStar, WatchProgress, VideoStatSnapshot, utcnow)
from app.security import require_admin
from app.ingest.errors import IngestDeferred, IngestError, PartialCaptureError

router = APIRouter(prefix="/api/v1/admin", tags=["library-deletion"])
VIDEO_JOBS = {"archive_video", "download_media", "refresh_comments", "refresh_stats", "sync_video"}
VARIANT_JOBS = {"create_playback", "prepare_media"}
ACTIVE = {"queued", "running", "paused", "blocked", "partial"}
ENTITY_NAMES = {Video: "video", VideoPart: "video_part", MediaVariant: "media_variant",
    DanmakuSnapshot: "danmaku_snapshot", SubtitleTrack: "subtitle_track", Comment: "comment",
    PlatformUser: "user", UserSnapshot: "user_snapshot", Creator: "creator", CaptureRun: "capture_run"}


def _marker(kind, identifier):
    return f"library_deleted_{kind}:{identifier}"


def is_suppressed(db, *, bvid=None, uid=None):
    keys = [_marker(kind, value) for kind, value in (("video", bvid), ("creator", uid)) if value]
    return bool(keys and db.scalar(select(Setting.key).where(Setting.key.in_(keys)).limit(1)))


def check_video_allowed(db, video=None, *, bvid=None, uid=None):
    if is_suppressed(db, bvid=bvid or (video.bvid if video else None), uid=uid):
        raise IngestError("内容已由管理员删除；需先明确允许重新导入", code="library_deleted", retryable=False)


def check_job_allowed(db, job):
    if job.kind.startswith("delete_"):
        return
    if (job.checkpoint or {}).get("deleted_by"):
        raise IngestError("此任务对应内容已删除，不能恢复旧检查点", code="library_deleted", retryable=False)
    video = None
    if job.kind in VIDEO_JOBS:
        video = db.get(Video, job.target_id)
        check_video_allowed(db, video, bvid=None if video else job.target_id)
    elif job.kind in VARIANT_JOBS:
        video = db.scalar(select(Video).join(VideoPart, VideoPart.video_id == Video.id)
            .join(MediaVariant, MediaVariant.part_id == VideoPart.id).where(MediaVariant.id == job.target_id))
        check_video_allowed(db, video)
    elif job.kind == "scan_collection":
        source = db.get(Collection, job.target_id)
        if source:
            check_video_allowed(db, uid=source.source_id if source.kind == "creator" else None)
            deletion = db.get(Job, (source.monitor_state or {}).get("deletion_job_id")) if (source.monitor_state or {}).get("deletion_job_id") else None
            if deletion and deletion.status != "succeeded":
                raise IngestError("来源正在删除相关归档，请等待删除完成", code="library_deleted", retryable=False)


def _ids(db, model, condition):
    return set(db.scalars(select(model.id).where(condition)))


def _scope(db, kind, identifier):
    if kind == "video":
        target = db.get(Video, identifier)
        if not target:
            raise HTTPException(404, "视频不存在")
        videos, collaborations, creator_ids, user_ids = {target.id}, set(), set(), set()
        label, uid = target.title or target.bvid, None
    else:
        target = db.get(Creator, identifier)
        user = db.get(PlatformUser, target.user_id) if target else None
        if not user:
            raise HTTPException(404, "UP 主不存在")
        videos = set(db.scalars(select(VideoCreator.video_id).where(VideoCreator.creator_id == target.id, VideoCreator.role == "owner")))
        collaborations = set(db.scalars(select(VideoCreator.video_id).where(VideoCreator.creator_id == target.id))) - videos
        creator_ids, user_ids, label, uid = {target.id}, {user.id}, target.alias or user.display_name or user.uid, user.uid
    parts = _ids(db, VideoPart, VideoPart.video_id.in_(videos))
    variants = _ids(db, MediaVariant, MediaVariant.part_id.in_(parts))
    comments = _ids(db, Comment, Comment.video_id.in_(videos))
    versions = _ids(db, CommentVersion, CommentVersion.comment_id.in_(comments))
    user_ids.update(db.scalars(select(Comment.author_user_id).where(Comment.id.in_(comments), Comment.author_user_id.is_not(None))))
    user_ids.update(db.scalars(select(UserSnapshot.user_id).join(Comment, Comment.author_snapshot_id == UserSnapshot.id)
        .where(Comment.id.in_(comments))))
    user_ids.update(db.scalars(select(UserSnapshot.user_id).join(CommentVersion, CommentVersion.author_snapshot_id == UserSnapshot.id)
        .where(CommentVersion.id.in_(versions))))
    # Shared people and their historical snapshots survive as authors elsewhere.
    retained_users = set(db.scalars(select(Comment.author_user_id).where(Comment.author_user_id.in_(user_ids), Comment.id.not_in(comments))))
    retained_users.update(db.scalars(select(Creator.user_id).where(Creator.user_id.in_(user_ids), Creator.id.not_in(creator_ids))))
    retained_users.update(db.scalars(select(UserSnapshot.user_id).join(CommentVersion,
        CommentVersion.author_snapshot_id == UserSnapshot.id).where(UserSnapshot.user_id.in_(user_ids), CommentVersion.id.not_in(versions))))
    retained_users.update(db.scalars(select(UserSnapshot.user_id).join(Comment,
        Comment.author_snapshot_id == UserSnapshot.id).where(UserSnapshot.user_id.in_(user_ids), Comment.id.not_in(comments))))
    orphan_users = user_ids - retained_users
    entities = {Video: videos, VideoPart: parts, MediaVariant: variants, Comment: comments,
        CommentVersion: versions, Creator: creator_ids, PlatformUser: orphan_users,
        UserSnapshot: _ids(db, UserSnapshot, UserSnapshot.user_id.in_(orphan_users)),
        DanmakuSnapshot: _ids(db, DanmakuSnapshot, DanmakuSnapshot.part_id.in_(parts)),
        SubtitleTrack: _ids(db, SubtitleTrack, SubtitleTrack.part_id.in_(parts)),
        CaptureRun: _ids(db, CaptureRun, CaptureRun.video_id.in_(videos)),
        CommentAsset: _ids(db, CommentAsset, CommentAsset.comment_id.in_(comments))}
    sources = set(db.scalars(select(CollectionItem.collection_id).where(CollectionItem.video_id.in_(videos))))
    if uid:
        sources.update(db.scalars(select(Collection.id).where(Collection.kind == "creator", Collection.source_id == uid)))
    bvids = set(db.scalars(select(Video.bvid).where(Video.id.in_(videos))))
    jobs = _ids(db, Job, or_((Job.kind.in_(VIDEO_JOBS) & Job.target_id.in_(videos | bvids)),
        (Job.kind.in_(VARIANT_JOBS) & Job.target_id.in_(variants)),
        ((Job.kind == "scan_collection") & Job.target_id.in_(sources))))
    return {"kind": kind, "target_id": identifier, "label": label, "uid": uid, "entities": entities,
        "video_ids": videos, "collaboration_ids": collaborations, "source_ids": sources, "job_ids": jobs, "bvids": bvids}


def _asset_columns():
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if any(foreign.target_fullname == "assets.id" for foreign in column.foreign_keys):
                yield table, column


def _candidate_assets(db, scope):
    assets = set()
    entities = scope["entities"]
    tables = {model.__table__.name: identifiers for model, identifiers in entities.items()}
    for table, column in _asset_columns():
        if table.name in tables:
            assets.update(value for value in db.scalars(select(column).where(table.c.id.in_(tables[table.name]))) if value)
    for model, name in ENTITY_NAMES.items():
        assets.update(db.scalars(select(AssetRef.asset_id).where(AssetRef.entity_type == name, AssetRef.entity_id.in_(entities.get(model, set())))))
    for raw in db.scalars(select(DanmakuSnapshot.raw_asset_ids).where(DanmakuSnapshot.id.in_(entities[DanmakuSnapshot]))):
        assets.update(raw or [])
    # HLS index assets own their init, segments, and original-source dependency.
    pending = assets.copy()
    while pending:
        children = set(db.scalars(select(AssetRef.asset_id).where(AssetRef.entity_type == "asset", AssetRef.entity_id.in_(pending)))) - assets
        assets.update(children)
        pending = children
    return assets


def _live_assets(db, candidates, entities=None):
    """Find direct live references in batches, including legacy protobuf arrays."""
    if not candidates:
        return set()
    excluded = {model.__table__.name: ids for model, ids in (entities or {}).items()}
    used = set()
    for table, column in _asset_columns():
        if table.name in {"asset_locations", "asset_refs", "storage_observations"}:
            continue
        query = select(column).where(column.in_(candidates))
        if table.name in excluded:
            query = query.where(table.c.id.not_in(excluded[table.name]))
        used.update(db.scalars(query))
    names = {ENTITY_NAMES[model]: ids for model, ids in (entities or {}).items() if model in ENTITY_NAMES}
    for asset_id, entity_type, entity_id in db.execute(select(AssetRef.asset_id, AssetRef.entity_type, AssetRef.entity_id).where(AssetRef.asset_id.in_(candidates))):
        if entity_type == "asset" and entity_id in candidates:
            continue  # Dependencies are propagated after the live roots are known.
        if entity_id not in names.get(entity_type, set()):
            used.add(asset_id)
    # Legacy imports may have raw IDs without AssetRef rows. Do not collect them.
    query = select(DanmakuSnapshot.raw_asset_ids).where(DanmakuSnapshot.id.not_in(excluded.get("danmaku_snapshots", set())))
    if len(candidates) <= 100:
        query = query.where(or_(*(cast(DanmakuSnapshot.raw_asset_ids, String).contains(identifier) for identifier in candidates)))
    for raw in db.scalars(query):
        used.update(set(raw or []) & candidates)
    frontier = used.copy()
    while frontier:
        children = set(db.scalars(select(AssetRef.asset_id).where(AssetRef.entity_type == "asset", AssetRef.entity_id.in_(frontier), AssetRef.asset_id.in_(candidates)))) - used
        used.update(children)
        frontier = children
    return used


def preview(db, kind, identifier, *, scope=None):
    from app.backup import protected_asset_ids
    scope = scope if scope is not None else _scope(db, kind, identifier)
    assets = _candidate_assets(db, scope)
    shared = _live_assets(db, assets, scope["entities"])
    protected = assets & protected_asset_ids(db)
    # The destructive entity scope is stable across harmless progress updates.
    # Adding another owner video/source or collaboration requires fresh consent.
    token_data = [kind, identifier, sorted(scope["video_ids"]), sorted(scope["collaboration_ids"]), sorted(scope["source_ids"])]
    token = hashlib.sha256(json.dumps(token_data, separators=(",", ":")).encode()).hexdigest()
    size = db.scalar(select(func.coalesce(func.sum(Asset.size), 0)).where(Asset.id.in_(assets)))
    return {"kind": kind, "target_id": identifier, "label": scope["label"],
        "video_count": len(scope["video_ids"]), "collaboration_count": len(scope["collaboration_ids"]),
        "comment_count": len(scope["entities"][Comment]), "part_count": len(scope["entities"][VideoPart]),
        "asset_count": len(assets), "asset_bytes": int(size or 0), "shared_asset_count": len(shared),
        "backup_protected_asset_count": len(protected), "source_count": len(scope["source_ids"]),
        "active_job_count": db.scalar(select(func.count(Job.id)).where(Job.id.in_(scope["job_ids"]), Job.status.in_(ACTIVE))),
        "preview_token": token, "semantics": "删除本地归档和不再引用的文件；停用相关同步源。共享人物、共享文件及备份保护资源保留。"
            + ("仅删除此 UP 主作为 owner 的视频；参与合作的其他视频保留并移除其署名。" if kind == "creator" else "")}


def request_deletion(db, kind, identifier, token, actor):
    from app.ingest.runner import _lock
    from app.jobs import enqueue
    with _lock(db, "delete:" + kind + ":" + identifier):
        previous = db.scalar(select(Job).where(Job.dedupe_key == f"delete:{kind}:{identifier}"))
        if previous:
            return {"job_id": previous.id, "status": previous.status, "preview": previous.policy["preview"]}
        scope = _scope(db, kind, identifier)
        summary = preview(db, kind, identifier, scope=scope)
        if not hmac.compare_digest(summary["preview_token"], token):
            raise HTTPException(409, "删除范围已经变化，请重新预检并确认")
        job = enqueue(db, "delete_" + kind, identifier, policy={"preview": summary}, dedupe_key=f"delete:{kind}:{identifier}")
        keys = [_marker("video", bvid) for bvid in scope["bvids"]]
        if scope["uid"]:
            keys.append(_marker("creator", scope["uid"]))
        for key in keys:
            current = db.get(Setting, key)
            if current and current.value.get("job_id") != job.id:
                raise HTTPException(409, "内容已在另一删除任务中，请等待该任务完成")
            if not current:
                db.add(Setting(key=key, value={"job_id": job.id}))
        for source in db.scalars(select(Collection).where(Collection.id.in_(scope["source_ids"]))):
            source.enabled = False
            source.monitor_state = {**(source.monitor_state or {}), "deletion_job_id": job.id}
        db.execute(update(SourceSubscription).where(SourceSubscription.collection_id.in_(scope["source_ids"])).values(enabled=False))
        # The job row is also the worker's commit fence. Once this commits, an
        # in-flight producer cannot commit another asset/ref/checkpoint.
        db.execute(update(Job).where(Job.id.in_(scope["job_ids"]), Job.status.in_(ACTIVE)).values(
            status="cancelled", error="管理员已请求删除相关本地归档", finished_at=utcnow()))
        job.checkpoint = {"phase": "deletion_wait", "video_ids": sorted(scope["video_ids"]),
            "source_ids": sorted(scope["source_ids"]), "producer_job_ids": sorted(scope["job_ids"]),
            "marker_keys": keys, "asset_ids": [], "deleted_video_ids": [], "asset_results": {},
            "creator_id": identifier if kind == "creator" else None}
        db.add(AuditLog(actor_id=actor.id, action="request_library_deletion", entity_type=kind, entity_id=identifier,
            details={"job_id": job.id, **summary}))
        db.commit()
        return {"job_id": job.id, "status": "queued", "preview": summary}


def _delete_entities(db, scope):
    entities, videos = scope["entities"], scope["video_ids"]
    candidates = _candidate_assets(db, scope)
    for model, name in ENTITY_NAMES.items():
        db.execute(delete(AssetRef).where(AssetRef.entity_type == name, AssetRef.entity_id.in_(entities.get(model, set()))))
    for model, condition in (
        (PlaybackSession, PlaybackSession.variant_id.in_(entities[MediaVariant])),
        (WatchProgress, WatchProgress.part_id.in_(entities[VideoPart])),
        (CollectionItem, CollectionItem.video_id.in_(videos)),
        (VideoStar, VideoStar.video_id.in_(videos)), (VideoAnnotation, VideoAnnotation.video_id.in_(videos)),
        (VideoStatSnapshot, VideoStatSnapshot.video_id.in_(videos)),
        (VideoCreator, or_(VideoCreator.video_id.in_(videos), VideoCreator.creator_id.in_(entities[Creator]))),
    ):
        db.execute(delete(model).where(condition))
    # Explicit order also works in schemas without cascading foreign keys.
    for model in (CommentAsset, CommentVersion, Comment, DanmakuSnapshot, SubtitleTrack,
                  MediaVariant, CaptureRun, VideoPart, Video, Creator, UserSnapshot, PlatformUser):
        db.execute(delete(model).where(model.id.in_(entities.get(model, set()))))
    db.flush()
    return candidates


@contextmanager
def _gc_scope(db, sha):
    from app.backup import asset_gc_guard
    from app.storage.locking import content_lock
    with asset_gc_guard(db), content_lock(db, sha):
        try:
            yield
        except BaseException:
            # A failed commit must roll back while backup snapshots and content
            # publishers are still excluded, not after the guards are released.
            db.rollback()
            raise


def _gc_asset(db, asset_id, guard, publish):
    from app.backup import protected_asset_ids
    from app.storage.base import ObjectMissing, StorageError, content_key
    from app.storage.service import get_adapter
    from app.storage.lifecycle import _same_physical_object
    sha = db.scalar(select(Asset.sha256).where(Asset.id == asset_id))
    if not sha:
        return publish("deleted")
    with _gc_scope(db, sha):
        guard()
        asset = db.scalar(select(Asset).where(Asset.id == asset_id).with_for_update().execution_options(populate_existing=True))
        if not asset:
            return publish("deleted")
        if asset_id in _live_assets(db, {asset_id}):
            return publish("shared")
        if asset_id in protected_asset_ids(db):
            return publish("backup_protected")
        locations = list(db.scalars(select(AssetLocation).where(AssetLocation.asset_id == asset_id)))
        for location in locations:
            if location.state == "deleted":
                continue
            guard()
            if location.object_key != content_key(asset.sha256):
                raise StorageError("Only registered content-addressed objects can be removed")
            profile = db.get(StorageProfile, location.storage_profile_id)
            if not profile or not profile.enabled:
                raise StorageError("Enable the storage node before retrying deletion")
            # Do not unlink an aliased object owned by another registered asset.
            for other, node in db.execute(select(AssetLocation, StorageProfile).join(StorageProfile,
                    StorageProfile.id == AssetLocation.storage_profile_id).where(AssetLocation.asset_id != asset_id,
                    AssetLocation.object_key == location.object_key, AssetLocation.state != "deleted")):
                if _same_physical_object(location, profile, other, node):
                    raise StorageError("Another asset aliases this physical object")
            adapter = get_adapter(profile)
            try:
                adapter.head(location.object_key, version_id=location.version_id)
            except ObjectMissing:
                pass  # Physical delete succeeded before an interrupted DB commit.
            else:
                adapter.delete(location.object_key, version_id=location.version_id)
                try:
                    adapter.head(location.object_key, version_id=location.version_id)
                except ObjectMissing:
                    pass
                else:
                    raise StorageError("Object deletion was not confirmed")
            location.state = "deleted"
        guard()
        db.execute(delete(AssetRef).where(AssetRef.entity_type == "asset", AssetRef.entity_id == asset_id))
        db.execute(delete(StorageObservation).where(StorageObservation.asset_id == asset_id))
        db.execute(delete(AssetLocation).where(AssetLocation.asset_id == asset_id))
        db.execute(delete(Asset).where(Asset.id == asset_id))
        db.flush()
        # Keep both guards until the DB removal and job checkpoint commit.
        return publish("deleted")


def run_deletion(db, job):
    from app.ingest.runner import _lock
    from app.jobs import ensure_active, save_checkpoint
    cp = deepcopy(job.checkpoint)
    guard = lambda: ensure_active(db, job.id, job.lease_owner)
    def save():
        save_checkpoint(db, job.id, job.lease_owner, cp)
    guard()
    # Cancelled processes keep their lease until their guarded exit. A crashed
    # one must expire before rows/objects are reclaimed, never merely be hidden.
    busy = db.scalar(select(Job.id).where(Job.id.in_(cp["producer_job_ids"]), Job.lease_owner.is_not(None), Job.lease_expires_at > utcnow()).limit(1))
    if busy:
        raise IngestDeferred("等待采集或播放处理进程停止后删除", code="deletion_wait", retry_after_seconds=5)
    with _lock(db, "delete-job:" + job.id):
        pending = [value for value in cp["video_ids"] if value not in cp["deleted_video_ids"]]
        for video_id in pending[:1]:
            with _lock(db, "video:" + video_id):
                guard()
                video = db.get(Video, video_id)
                if video:
                    scope = _scope(db, "video", video_id)
                    cp["asset_ids"] = sorted(set(cp["asset_ids"]) | _delete_entities(db, scope))
                cp["deleted_video_ids"].append(video_id)
                cp["phase"] = "deletion_records"
                save()
        if len(cp["deleted_video_ids"]) < len(cp["video_ids"]):
            return {"continuation": True, "phase": cp["phase"], "deleted_videos": len(cp["deleted_video_ids"]), "total_videos": len(cp["video_ids"])}
        if not cp.get("relations_done"):
            creator = db.get(Creator, cp["creator_id"]) if cp.get("creator_id") else None
            if creator:
                scope = _scope(db, "creator", creator.id)
                if scope["video_ids"]:
                    raise IngestError("删除期间出现新的 UP 稿件，请重新核验范围", code="deletion_scope_changed", retryable=False)
                cp["asset_ids"] = sorted(set(cp["asset_ids"]) | _delete_entities(db, scope))
            # Preserve the audit/attempt ledger, remove stale payloads that can
            # contain deleted comments or asset IDs, and prevent manual resume.
            db.execute(update(Job).where(Job.id.in_(cp["producer_job_ids"])).values(
                checkpoint={"deleted_by": job.id}, result={"deleted_by": job.id}))
            cp["relations_done"], cp["phase"] = True, "deletion_assets"
            save()
        remaining = set(cp["asset_ids"]) - set(cp["asset_results"])
        # Index assets must be collected before their HLS children. A shared
        # root keeps its edges, so its dependency assets remain live as well.
        parents = set(db.scalars(select(AssetRef.asset_id).where(AssetRef.entity_type == "asset", AssetRef.entity_id.in_(remaining), AssetRef.asset_id.in_(remaining))))
        ordered = sorted(remaining - parents) or sorted(remaining)
        for asset_id in ordered[:50]:
            guard()
            def publish(outcome):
                cp["asset_results"][asset_id] = outcome
                cp.pop("last_error", None)
                save()
                return outcome
            try:
                _gc_asset(db, asset_id, guard, publish)
            except Exception as error:
                from app.jobs import LeaseLost
                if isinstance(error, (LeaseLost, IngestDeferred)):
                    raise
                db.rollback()
                cp["asset_results"].pop(asset_id, None)
                cp["phase"], cp["last_error"] = "deletion_assets", "存储清理未完成；请检查存储节点后重试"
                save()
                raise PartialCaptureError(cp["last_error"], code="deletion_storage_failed") from None
        done = len(cp["asset_results"]) == len(cp["asset_ids"])
        cp["phase"] = "deletion_complete" if done else "deletion_assets"
        save()
        counts = {key: sum(value == key for value in cp["asset_results"].values()) for key in ("deleted", "shared", "backup_protected")}
        return {"continuation": not done, "phase": cp["phase"], "deleted_videos": len(cp["deleted_video_ids"]),
            "total_videos": len(cp["video_ids"]), "deleted_assets": counts["deleted"],
            "retained_shared_assets": counts["shared"], "retained_backup_assets": counts["backup_protected"],
            "processed_assets": len(cp["asset_results"]), "total_assets": len(cp["asset_ids"]),
            "collaborations_detached": job.policy["preview"]["collaboration_count"], "source_data_unchanged": True}


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool
    preview_token: str = Field(min_length=64, max_length=64)


@router.get("/videos/{identifier}/deletion-preview")
def video_preview(identifier: str, _=Depends(require_admin), db: Session = Depends(get_db)):
    return preview(db, "video", identifier)


@router.get("/creators/{identifier}/deletion-preview")
def creator_preview(identifier: str, _=Depends(require_admin), db: Session = Depends(get_db)):
    return preview(db, "creator", identifier)


def _request(db, kind, identifier, body, user):
    if not body.confirm:
        raise HTTPException(422, "请明确确认删除")
    return request_deletion(db, kind, identifier, body.preview_token, user)


@router.post("/videos/{identifier}/deletion", status_code=202)
def delete_video(identifier: str, body: DeleteRequest, user=Depends(require_admin), db: Session = Depends(get_db)):
    return _request(db, "video", identifier, body, user)


@router.post("/creators/{identifier}/deletion", status_code=202)
def delete_creator(identifier: str, body: DeleteRequest, user=Depends(require_admin), db: Session = Depends(get_db)):
    return _request(db, "creator", identifier, body, user)


@router.post("/deletions/{job_id}/allow-reimport")
def allow_reimport(job_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.kind not in {"delete_video", "delete_creator"}:
        raise HTTPException(404, "删除任务不存在")
    if job.status != "succeeded":
        raise HTTPException(409, "删除任务尚未完成，不能解除重新导入限制")
    for key in job.checkpoint.get("marker_keys", []):
        marker = db.get(Setting, key)
        if marker and marker.value.get("job_id") == job.id:
            db.delete(marker)
    job.result = {**(job.result or {}), "reimport_allowed": True}
    db.add(AuditLog(actor_id=user.id, action="allow_library_reimport", entity_type="job", entity_id=job.id))
    db.commit()
    return {"allowed": True, "restored": False, "sources_remain_disabled": True}
