"""Browser-bound playback sessions and authenticated replica lifecycle operations."""
import hashlib
import hmac
import re
import secrets
from types import SimpleNamespace
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
from app.media_properties import merge_media_properties, with_source_file_average
from app.playback_limits import check_playback_creation, playback_creation_slot
from app.models import (Asset, AssetLocation, AuditLog, Comment, CommentAsset, CommentVersion, Creator, DanmakuSnapshot, Job,
                        MediaVariant, PlaybackSession, Setting, StorageObservation, StorageProfile,
                        SubtitleTrack, PlatformUser, UserSnapshot, Video, VideoCreator, VideoPart, VideoStatSnapshot, utcnow)
from app.security import authenticated, require_admin, optional_identity, same_origin

router = APIRouter(prefix="/api/v1")

GUEST_COOKIE = "treasure_viewer"


def playback_identity(request: Request, response: Response, identity=Depends(optional_identity)):
    if request.method not in {"GET", "HEAD"}:
        same_origin(request)
    token = request.cookies.get(GUEST_COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        token = ""
    if not identity and request.method == "POST" and request.url.path.rstrip("/").endswith("/playback-sessions"):
        # Keep existing guest sessions usable while extending the browser binding
        # to cover the full lifetime of this newly created session.
        token = token or secrets.token_urlsafe(32)
        response.set_cookie(GUEST_COOKIE, token, httponly=True, secure=settings.cookie_secure,
                            samesite="strict", max_age=4 * 3600, path="/api/v1/playback-sessions")
    return (SimpleNamespace(id=identity[0].id if identity else None,
                            guest_hash=hashlib.sha256(token.encode()).hexdigest() if token else ""), None)


def required(db, model, identifier):
    item = db.get(model, identifier)
    if item is None:
        raise HTTPException(404, "记录不存在")
    return item


def audit(db, user, action, kind, identifier, details=None):
    db.add(AuditLog(actor_id=user.id, action=action, entity_type=kind, entity_id=identifier, details=details or {}))


def session_assets(db, session):
    from app.playback import load_hls_package
    variant = required(db, MediaVariant, session.variant_id)
    if session.protocol == "hls":
        index, allowed = load_hls_package(db, variant.asset_id)
        return variant, allowed, index
    return variant, [variant.asset_id], None


def owned_session(db, session_id, user):
    session = required(db, PlaybackSession, session_id)
    owned = session.user_id == user.id if session.user_id else bool(
        getattr(user, "guest_hash", "") and hmac.compare_digest(session.state.get("guest_hash", ""), user.guest_hash))
    if not owned or session.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
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
def create_playback(body: schemas.PlaybackInput, request: Request, identity=Depends(playback_identity), db: Session = Depends(get_db)):
    digest = check_playback_creation(db, request, identity[0])
    return _create_playback(body, identity[0], db, digest)


def _create_playback(body, user, db, client_digest):
    from app.storage.routing import playback_routes, choose_route
    from app.storage.delivery import direct_profile, direct_urls
    from app.storage.base import StorageError
    part = required(db, VideoPart, body.part_id)
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
    def plan(candidate):
        session.variant_id = candidate.id
        session.protocol = "hls" if candidate.kind == "hls" else "file"
        _, asset_ids, index = session_assets(db, session)
        routes = playback_routes(db, asset_ids, user_id=user.id)
        session.profile_id = choose_route(routes, route_id=body.route_id)
        return index, routes
    try:
        index, routes = plan(selected)
    except (StorageError, HTTPException):
        # Packaging is an automatic transport preference, not a requirement to
        # copy every segment before an existing original on this node can play.
        # Keep the exact chosen source/quality; explicit HLS remains strict.
        if body.protocol != "auto" or selected.kind != "hls" or base.kind == "hls":
            raise HTTPException(503, "没有完整可用的存储节点，请检查媒体副本") from None
        selected = base
        try:
            index, routes = plan(selected)
        except (StorageError, HTTPException):
            raise HTTPException(503, "没有完整可用的存储节点，请检查媒体副本") from None
    row = db.get(Setting, "playback")
    config = schemas.PlaybackSettings.model_validate(row.value if row else {})
    try:
        direct = direct_profile(db, session.profile_id, bulk=bool(index))
    except Exception:
        raise HTTPException(503, "所选存储节点暂不可用，请重新选择") from None
    direct_url, url_expiry = None, None
    if direct:
        if index:
            # The small authenticated playlist will issue fresh segment URLs.
            url_expiry = utcnow() + timedelta(seconds=3600)
        else:
            try:
                urls, url_expiry = direct_urls(db, direct, [selected.asset_id])
                direct_url = urls[selected.asset_id]
            except Exception:
                raise HTTPException(503, "无法生成云端播放地址，请检查存储源的外网访问设置") from None
    probe_asset_id = index["segments"][0]["asset_id"] if index else selected.asset_id
    routes = [dict(route) for route in routes]
    probe_ids = []
    # Media preparation may read a remote HLS index. Hold the cross-worker quota
    # lock only for the final recheck and the short database insertion/commit.
    with playback_creation_slot(db, user, client_digest):
        db.add(session)
        db.flush()
        for route in routes:
            if len(probe_ids) < 3 and route.get("status") != "unavailable":
                route["probe_url"] = f"/api/v1/playback-sessions/{session.id}/probe/{route['id']}"
                route["probe_bytes"] = config.probe_bytes
                probe_ids.append(route["id"])
        session.state = {"guest_hash": user.guest_hash if user.id is None else "", "client_hash": client_digest,
                         "failed_profile_ids": [], "switch_count": 0, "probe_route_ids": probe_ids,
                         "probe_asset_id": probe_asset_id, "reported_routes": []}
        db.commit()
    metadata = dict(selected.metadata_json or {})
    video = required(db, Video, part.video_id)
    media_source = selected
    if index:
        media_source = next((v for v in variants if v.kind != "hls" and v.id == metadata.get("source_variant_id")), None)
        if media_source is None:
            media_source = next((v for v in variants if v.kind != "hls" and v.asset_id == metadata.get("source_asset_id")), selected)
    properties = (video.metadata_json or {}).get("media_properties", {})
    media = merge_media_properties(properties.get(media_source.id), media_source.metadata_json,
                                   properties.get(selected.id), metadata)
    if media_source.kind != "hls":
        source_asset = required(db, Asset, media_source.asset_id)
        media = with_source_file_average(media, size=source_asset.size, duration=media_source.duration)
    media.update(width=selected.width or media_source.width, height=selected.height or media_source.height,
                 video_codec=selected.video_codec or media_source.video_codec,
                 audio_codec=selected.audio_codec or media_source.audio_codec,
                 mime_type="video/mp4" if index else required(db, Asset, selected.asset_id).mime_type,
                 segment_count=len(index["segments"]) if index else None)
    url = f"/api/v1/playback-sessions/{session.id}/manifest.m3u8" if index else f"/api/v1/playback-sessions/{session.id}/assets/{selected.asset_id}"
    return {"id": session.id, "session_id": session.id, "asset_id": selected.asset_id, "variant_id": selected.id,
            "source_variant_id": media_source.id, "protocol": session.protocol, "url": direct_url or url,
            "direct": bool(direct), "url_expires_at": url_expiry,
            "delivery": "direct" if direct else ("local" if db.get(StorageProfile, session.profile_id).kind == "local" else "redirect"),
            "expires_at": session.expires_at, "routes": routes, "selected_route_id": session.profile_id,
            "media": media, "loudness": media.get("loudness"),
            "danmaku_url": f"/api/v1/parts/{body.part_id}/danmaku",
            "subtitles": [{"id": s.id, "label": s.label, "language": s.language, "is_auto": s.is_auto,
                           "url": catalog.asset_url(s.asset_id)} for s in db.scalars(select(SubtitleTrack).where(SubtitleTrack.part_id == body.part_id))]}


@router.get("/playback-sessions/{session_id}/manifest.m3u8")
def manifest(session_id: str, identity=Depends(playback_identity), db: Session = Depends(get_db)):
    from app.playback import render_manifest
    from app.storage.delivery import direct_profile, direct_urls
    from app.storage.base import StorageError
    session = owned_session(db, session_id, identity[0])
    _, allowed, index = session_assets(db, session)
    if index is None:
        raise HTTPException(404, "此会话不是分片播放")
    try:
        direct = direct_profile(db, session.profile_id, bulk=True)
        if direct:
            ttl = min(3600, max(1, int((session.expires_at.replace(tzinfo=timezone.utc) - utcnow()).total_seconds())))
            urls, _ = direct_urls(db, direct, allowed, expires_in=ttl)
            value = render_manifest(index, urls.__getitem__, allow_external=True)
        else:
            value = render_manifest(index, lambda asset_id: f"/api/v1/playback-sessions/{session.id}/assets/{asset_id}")
    except StorageError:
        raise HTTPException(503, "无法生成云端分片地址，请重试或切换存储节点") from None
    return Response(value, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "private, no-store"})


@router.api_route("/playback-sessions/{session_id}/assets/{asset_id}", methods=["GET", "HEAD"])
def playback_asset(session_id: str, asset_id: str, request: Request, identity=Depends(playback_identity), db: Session = Depends(get_db)):
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
def playback_probe(session_id: str, profile_id: str, identity=Depends(playback_identity), db: Session = Depends(get_db)):
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
def observation(session_id: str, body: schemas.PlaybackObservationInput, identity=Depends(playback_identity), db: Session = Depends(get_db)):
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
    if identity[0].id:
        db.add(StorageObservation(profile_id=body.route_id, asset_id=session.state.get("probe_asset_id"), user_id=identity[0].id,
                                 scope="browser_delivery", latency_ms=body.latency_ms, elapsed_ms=body.elapsed_ms,
                                 bytes_read=body.bytes_read, succeeded=body.succeeded))
    # Guest observations stay in their own short-lived session, never global routing data.
    session.state = {**session.state, "reported_routes": [*reported, body.route_id],
                     "measurements": {**session.state.get("measurements", {}), body.route_id: body.model_dump()}}
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


@router.post("/admin/variants/{variant_id}/compatible", status_code=202)
def compatible(variant_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    """Explicitly prepare a saved original; viewing never starts a conversion."""
    variant = required(db, MediaVariant, variant_id)
    if variant.kind != "archive":
        raise HTTPException(422, "请选择视频原档生成兼容副本")
    # Do not duplicate automatic work or implicitly resume an operator pause.
    job = db.scalar(select(Job).where(Job.kind == "create_playback", Job.target_id == variant.id,
        Job.status.in_(("queued", "running", "paused", "blocked"))).order_by(Job.created_at.desc(), Job.id).limit(1))
    if job is None:
        config = db.get(Setting, "ingest")
        policy = schemas.IngestPolicy.model_validate(config.value if config else {}).model_dump()
        # A new processing profile can recover old explicitly unsupported HDR
        # results without resetting the successful historical task or archive.
        job = enqueue(db, "create_playback", variant.id, policy=policy, frozen_policy=True,
                      dedupe_key=f"compatible-v3:{variant.id}:{variant.asset_id}")
    audit(db, user, "prepare_compatible", "media_variant", variant.id, {"job_id": job.id})
    db.commit()
    return catalog.job_view(job)


@router.get("/videos/{video_id}/statistics")
def statistics(video_id: str, page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
               db: Session = Depends(get_db)):
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
