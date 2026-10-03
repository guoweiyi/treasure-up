"""Authenticated playback sessions and explicit replica lifecycle operations."""
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import catalog, schemas
from app.config import settings
from app.db import get_db
from app.jobs import enqueue
from app.models import (Asset, AssetLocation, AuditLog, Comment, CommentAsset, CommentVersion, Creator, DanmakuSnapshot,
                        MediaVariant, PlaybackSession, Setting, StorageObservation, StorageProfile,
                        SubtitleTrack, PlatformUser, UserSnapshot, Video, VideoCreator, VideoPart, VideoStatSnapshot, utcnow)
from app.security import authenticated, require_admin

router = APIRouter(prefix="/api/v1")


def required(db, model, identifier):
    item = db.get(model, identifier)
    if item is None:
        raise HTTPException(404, "记录不存在")
    return item


def audit(db, user, action, kind, identifier, details=None):
    db.add(AuditLog(actor_id=user.id, action=action, entity_type=kind, entity_id=identifier, details=details or {}))


def session_assets(db, session):
    from app.playback import load_hls_index, package_asset_ids
    variant = required(db, MediaVariant, session.variant_id)
    if session.protocol == "hls":
        index = load_hls_index(db, variant.asset_id)
        return variant, package_asset_ids(index), index
    return variant, [variant.asset_id], None


def owned_session(db, session_id, user):
    session = required(db, PlaybackSession, session_id)
    if session.user_id != user.id or session.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
        raise HTTPException(403, "播放会话已过期，请重新获取播放地址")
    return session


def media_response(resolved, request):
    headers = {"Cache-Control": "private, max-age=300", "ETag": '"' + resolved["sha256"] + '"',
               "Content-Type": resolved["mime_type"], "Accept-Ranges": "bytes"}
    if request.method == "HEAD":
        return Response(headers={**headers, "Content-Length": str(resolved["size"])})
    if resolved["kind"] != "local":
        return RedirectResponse(resolved["url"], status_code=307, headers={"Cache-Control": "private, no-store"})
    path = Path(resolved["path"])
    if settings.x_accel_prefix:
        try:
            key = path.resolve().relative_to(settings.media_root.resolve()).as_posix()
        except ValueError:
            raise HTTPException(503, "媒体分发目录不可用") from None
        return Response(headers={**headers, "X-Accel-Redirect": settings.x_accel_prefix.rstrip("/") + "/" + quote(key, safe="/")})
    return FileResponse(path, media_type=resolved["mime_type"], headers=headers)


@router.post("/playback-sessions")
def create_playback(body: schemas.PlaybackInput, identity=Depends(authenticated), db: Session = Depends(get_db)):
    from app.storage.routing import playback_routes, select_route
    user = identity[0]
    required(db, VideoPart, body.part_id)
    variants = list(db.scalars(select(MediaVariant).where(MediaVariant.part_id == body.part_id).order_by(MediaVariant.created_at.desc())))
    base = next((v for v in variants if v.id == body.variant_id), None) if body.variant_id else next(
        (v for kind in ("archive", "playback") for v in variants if v.kind == kind), None)
    if not base:
        raise HTTPException(409, "此分 P 尚无所选播放文件")
    selected = base
    if body.protocol != "file" and base.kind != "hls":
        packaged = next((v for v in variants if v.kind == "hls" and (
            (v.metadata_json or {}).get("source_variant_id") == base.id or
            (v.metadata_json or {}).get("source_asset_id") == base.asset_id)), None)
        if packaged:
            selected = packaged
        elif body.protocol == "hls":
            raise HTTPException(409, "此版本尚未完成无损分片，请在后台准备播放文件")
    if body.protocol == "file" and selected.kind == "hls":
        source = (selected.metadata_json or {}).get("source_variant_id")
        selected = next((v for v in variants if v.id == source), None)
        if not selected:
            raise HTTPException(409, "对应原档不可用")
    session = PlaybackSession(user_id=user.id, variant_id=selected.id, protocol="hls" if selected.kind == "hls" else "file",
                              state={}, expires_at=utcnow() + timedelta(hours=4))
    _, asset_ids, index = session_assets(db, session)
    try:
        session.profile_id = select_route(db, asset_ids, route_id=body.route_id, user_id=user.id)
        routes = playback_routes(db, asset_ids, user_id=user.id)
    except Exception:
        raise HTTPException(503, "没有完整可用的存储节点，请检查媒体副本") from None
    row = db.get(Setting, "playback")
    config = schemas.PlaybackSettings.model_validate(row.value if row else {})
    probe_asset_id = asset_ids[1] if len(asset_ids) > 1 else asset_ids[0]
    routes = [dict(route) for route in routes]
    probe_ids = []
    db.add(session)
    db.flush()
    for route in routes:
        if len(probe_ids) < 3 and route.get("status") != "unavailable":
            route["probe_url"] = f"/api/v1/playback-sessions/{session.id}/probe/{route['id']}"
            route["probe_bytes"] = config.probe_bytes
            probe_ids.append(route["id"])
    session.state = {"failed_profile_ids": [], "switch_count": 0, "probe_route_ids": probe_ids,
                     "probe_asset_id": probe_asset_id, "reported_routes": []}
    db.commit()
    metadata = dict(selected.metadata_json or {})
    video = required(db, Video, required(db, VideoPart, body.part_id).video_id)
    source_info = (video.metadata_json or {}).get("media_properties", {}).get(base.id, {})
    url = f"/api/v1/playback-sessions/{session.id}/manifest.m3u8" if index else f"/api/v1/playback-sessions/{session.id}/assets/{selected.asset_id}"
    return {"id": session.id, "session_id": session.id, "asset_id": selected.asset_id, "variant_id": selected.id,
            "source_variant_id": base.id, "protocol": session.protocol, "url": url,
            "expires_at": session.expires_at, "routes": routes, "selected_route_id": session.profile_id,
            "media": {**source_info, **metadata}, "loudness": metadata.get("loudness") or (base.metadata_json or {}).get("loudness"),
            "danmaku_url": f"/api/v1/parts/{body.part_id}/danmaku",
            "subtitles": [{"id": s.id, "label": s.label, "language": s.language, "is_auto": s.is_auto,
                           "url": catalog.asset_url(s.asset_id)} for s in db.scalars(select(SubtitleTrack).where(SubtitleTrack.part_id == body.part_id))]}


@router.get("/playback-sessions/{session_id}/manifest.m3u8")
def manifest(session_id: str, identity=Depends(authenticated), db: Session = Depends(get_db)):
    from app.playback import render_manifest
    session = owned_session(db, session_id, identity[0])
    _, _, index = session_assets(db, session)
    if index is None:
        raise HTTPException(404, "此会话不是分片播放")
    value = render_manifest(index, lambda asset_id: f"/api/v1/playback-sessions/{session.id}/assets/{asset_id}")
    return Response(value, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "private, no-store"})


@router.api_route("/playback-sessions/{session_id}/assets/{asset_id}", methods=["GET", "HEAD"])
def playback_asset(session_id: str, asset_id: str, request: Request, identity=Depends(authenticated), db: Session = Depends(get_db)):
    from app.storage.routing import resolve_session_asset
    from app.storage.base import StorageError
    session = owned_session(db, session_id, identity[0])
    _, allowed, _ = session_assets(db, session)
    if asset_id not in allowed:
        raise HTTPException(404, "资产不属于当前播放会话")
    try:
        resolved = resolve_session_asset(db, session, asset_id, allowed)
    except StorageError:
        # Persist failed nodes even when no replacement is usable, otherwise each
        # fragment retry would start the same failover cycle from scratch.
        db.commit()
        raise HTTPException(503, "播放节点暂不可用") from None
    db.commit()
    return media_response(resolved, request)


@router.get("/playback-sessions/{session_id}/probe/{profile_id}")
def playback_probe(session_id: str, profile_id: str, identity=Depends(authenticated), db: Session = Depends(get_db)):
    from app.storage.routing import resolve_on_profile
    session = owned_session(db, session_id, identity[0])
    if profile_id not in session.state.get("probe_route_ids", []):
        raise HTTPException(404, "节点不属于当前会话")
    asset = required(db, Asset, session.state["probe_asset_id"])
    if asset.size <= 0:
        raise HTTPException(409, "空资产不能用于节点测速")
    try:
        resolved = resolve_on_profile(db, asset.id, profile_id, expires_in=120)
    except Exception:
        raise HTTPException(503, "探测节点不可用") from None
    if resolved["kind"] != "local":
        # Client must use Range and cancel after the fixed byte budget, including
        # servers that ignore Range; only its own measurements affect its routes.
        return RedirectResponse(resolved["url"], status_code=307, headers={"Cache-Control": "no-store"})
    row = db.get(Setting, "playback")
    limit = schemas.PlaybackSettings.model_validate(row.value if row else {}).probe_bytes
    with Path(resolved["path"]).open("rb") as stream:
        data = stream.read(limit)
    return Response(data, status_code=206, headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
                    "Content-Range": f"bytes 0-{len(data)-1}/{asset.size}", "Accept-Ranges": "bytes", "Cache-Control": "no-store"})


@router.post("/playback-sessions/{session_id}/observations")
def observation(session_id: str, body: schemas.PlaybackObservationInput, identity=Depends(authenticated), db: Session = Depends(get_db)):
    session = owned_session(db, session_id, identity[0])
    db.scalar(select(PlaybackSession).where(PlaybackSession.id == session.id)
              .with_for_update().execution_options(populate_existing=True))
    if body.route_id not in session.state.get("probe_route_ids", []):
        raise HTTPException(422, "节点不属于当前会话")
    reported = session.state.get("reported_routes", [])
    if body.route_id in reported:
        raise HTTPException(409, "此会话已记录该节点测量")
    if body.latency_ms > body.elapsed_ms or (body.succeeded and body.bytes_read == 0):
        raise HTTPException(422, "测量字段不一致")
    db.add(StorageObservation(profile_id=body.route_id, asset_id=session.state.get("probe_asset_id"), user_id=identity[0].id,
                             scope="browser_delivery", latency_ms=body.latency_ms, elapsed_ms=body.elapsed_ms,
                             bytes_read=body.bytes_read, succeeded=body.succeeded))
    session.state = {**session.state, "reported_routes": [*reported, body.route_id]}
    db.commit()
    return {"recorded": True}


def video_asset_ids(db, video_id):
    video = required(db, Video, video_id)
    parts = list(db.scalars(select(VideoPart.id).where(VideoPart.video_id == video.id)))
    ids = set(db.scalars(select(MediaVariant.asset_id).where(MediaVariant.part_id.in_(parts))))
    if video.cover_asset_id:
        ids.add(video.cover_asset_id)
    for track in db.scalars(select(SubtitleTrack).where(SubtitleTrack.part_id.in_(parts))):
        ids.update(v for v in (track.asset_id, track.raw_asset_id) if v)
    for snapshot in db.scalars(select(DanmakuSnapshot).where(DanmakuSnapshot.part_id.in_(parts))):
        ids.update(snapshot.raw_asset_ids or [])
        if snapshot.data_asset_id:
            ids.add(snapshot.data_asset_id)
    comments = select(Comment.id).where(Comment.video_id == video.id)
    ids.update(db.scalars(select(CommentAsset.asset_id).where(CommentAsset.comment_id.in_(comments))))
    people = set(db.scalars(select(Comment.author_user_id).where(Comment.video_id == video.id)))
    people.update(db.scalars(select(Creator.user_id).join(VideoCreator, VideoCreator.creator_id == Creator.id)
                              .where(VideoCreator.video_id == video.id)))
    ids.update(a for a in db.scalars(select(PlatformUser.avatar_asset_id).where(PlatformUser.id.in_(people))) if a)
    snapshots = set(db.scalars(select(Comment.author_snapshot_id).where(Comment.video_id == video.id)))
    snapshots.update(db.scalars(select(CommentVersion.author_snapshot_id).where(CommentVersion.comment_id.in_(comments))))
    ids.update(a for a in db.scalars(select(UserSnapshot.avatar_asset_id).where(UserSnapshot.id.in_(snapshots))) if a)
    from app.storage.lifecycle import dependency_asset_ids
    return sorted(dependency_asset_ids(db, list(ids)))


@router.get("/admin/videos/{video_id}/storage")
def video_storage(video_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    ids = video_asset_ids(db, video_id)
    items = []
    rows = db.execute(select(AssetLocation, Asset, StorageProfile)
                      .join(Asset, Asset.id == AssetLocation.asset_id)
                      .join(StorageProfile, StorageProfile.id == AssetLocation.storage_profile_id)
                      .where(AssetLocation.asset_id.in_(ids)).order_by(AssetLocation.created_at))
    for location, asset, profile in rows:
        items.append({"id": location.id, "asset_id": asset.id, "kind": asset.kind, "size": asset.size,
                      "profile_id": profile.id, "profile_name": profile.name, "state": location.state,
                      "verified_at": location.verified_at})
    return {"items": items, "total_assets": len(ids)}


@router.post("/admin/videos/{video_id}/sync", status_code=202)
def sync_video(video_id: str, body: schemas.VideoSyncInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    target = required(db, StorageProfile, body.target_profile_id)
    if not target.enabled:
        raise HTTPException(422, "目标存储位置已停用")
    ids = video_asset_ids(db, video_id)
    if not ids:
        raise HTTPException(409, "此视频尚无可同步资产")
    job = enqueue(db, "sync_video", video_id, policy={"asset_ids": ids, "target_profile_id": target.id})
    audit(db, user, "sync", "video", video_id, {"target_profile_id": target.id, "asset_count": len(ids)})
    db.commit()
    return catalog.job_view(job)


@router.delete("/admin/storage/locations/{location_id}", status_code=202)
def retire(location_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    required(db, AssetLocation, location_id)
    job = enqueue(db, "retire_storage_location", location_id)
    audit(db, user, "retire", "asset_location", location_id)
    db.commit()
    return catalog.job_view(job)


@router.post("/admin/storage/locations/{location_id}/restore", status_code=202)
def restore(location_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    required(db, AssetLocation, location_id)
    job = enqueue(db, "restore_storage_location", location_id)
    audit(db, user, "restore", "asset_location", location_id)
    db.commit()
    return catalog.job_view(job)


@router.post("/admin/storage/locations/{location_id}/purge", status_code=202)
def purge(location_id: str, body: schemas.PurgeLocationInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    location = required(db, AssetLocation, location_id)
    if location.state != "retired":
        raise HTTPException(409, "请先停用副本并完成其它副本校验")
    job = enqueue(db, "purge_storage_location", location_id)
    audit(db, user, "purge_requested", "asset_location", location_id)
    db.commit()
    return catalog.job_view(job)


@router.post("/admin/variants/{variant_id}/prepare", status_code=202)
def prepare(variant_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    variant = required(db, MediaVariant, variant_id)
    if variant.kind == "hls":
        raise HTTPException(422, "请选择原档或兼容副本")
    config = db.get(Setting, "playback")
    segment_seconds = schemas.PlaybackSettings.model_validate(config.value if config else {}).segment_seconds
    job = enqueue(db, "prepare_media", variant.id, policy={"package": True, "analyze_loudness": True, "segment_seconds": segment_seconds})
    audit(db, user, "prepare", "media_variant", variant.id)
    db.commit()
    return catalog.job_view(job)


@router.get("/videos/{video_id}/statistics")
def statistics(video_id: str, page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
               identity=Depends(authenticated), db: Session = Depends(get_db)):
    required(db, Video, video_id)
    return catalog.page(db, select(VideoStatSnapshot).where(VideoStatSnapshot.video_id == video_id)
                        .order_by(VideoStatSnapshot.observed_at.desc()), page, page_size,
                        lambda s: {"id": s.id, "observed_at": s.observed_at, **s.counts})


@router.post("/admin/statistics/refresh", status_code=202)
def refresh_statistics(user=Depends(require_admin), db: Session = Depends(get_db)):
    from app.maintenance import enqueue_statistics
    try:
        result = enqueue_statistics(db, manual=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, user, "refresh", "statistics", None, result)
    db.commit()
    return result
