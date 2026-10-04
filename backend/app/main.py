import json
import secrets
from datetime import timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.responses import JSONResponse
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import catalog, schemas
from app.config import settings
from app.db import get_db
from app.jobs import enqueue
from app.source_labels import display_source_title
from app.models import (Asset, AssetLocation, AuditLog, BackupSet, CaptureRun, Collection, CollectionItem, Comment, Creator,
                        DanmakuSnapshot, Job, MediaVariant, OutboxEvent, PlatformUser, Setting,
                        SourceAccount, SourceSubscription, StorageProfile, SubtitleTrack, User, UserSession,
                        UserSnapshot, Video, VideoAnnotation, VideoCreator, VideoPart, VideoStar, CommentAsset, WatchProgress, utcnow)
from app.security import (COOKIE_NAME, authenticated, create_session, encrypt_secret, hash_password,
                          require_admin, require_editor, reserve_password_attempt, verify_password, optional_identity)

app = FastAPI(title="Treasure Up", version="0.3.1", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[v.strip() for v in settings.allowed_hosts.split(",")])
P = "/api/v1"
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


@app.exception_handler(IntegrityError)
async def conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={"detail": "记录已存在或正在被修改，请刷新后重试"})


@app.middleware("http")
async def response_policy(request, call_next):
    if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > 1_000_000:
        return Response(status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith(P) and "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "private, no-store"
    return response


def required(db, model, identifier):
    item = db.get(model, identifier)
    if item is None:
        raise HTTPException(404, "记录不存在")
    return item


def audit(db, user, action, kind, identifier=None, details=None):
    db.add(AuditLog(actor_id=user.id, action=action, entity_type=kind, entity_id=identifier, details=details or {}))


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "记录已存在或正在被修改，请刷新后重试") from None


def like(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "version": app.version}


@app.get(P + "/server")
def server_capabilities():
    return {"application": "treasure-up", "api_version": 1, "version": app.version,
            "features": ["catalog", "private-playback", "hls", "danmaku", "comments", "watch-progress", "source-monitoring", "userscript-ingest"],
            "authentication": ["session-cookie", "webauthn"] if settings.passkeys_enabled else ["session-cookie"]}


@app.post(P + "/auth/login")
def login(body: schemas.Login, request: Request, response: Response, db: Session = Depends(get_db)):
    origin = request.headers.get("origin")
    accepted_origins = {str(request.base_url).rstrip("/")}
    # External TLS terminators may forward HTTP internally. Only the explicit
    # configured origin is accepted in addition to this request's trusted host.
    from app.passkeys import site_policy
    try:
        accepted_origins.add(site_policy()[0])
    except HTTPException:
        pass
    if origin and origin.rstrip("/") not in accepted_origins:
        raise HTTPException(403, "登录来源不匹配")
    attempt = reserve_password_attempt(db, request)
    user = db.scalar(select(User).where(User.username == body.username))
    checked_hash = user.password_hash if user else _DUMMY_HASH
    valid = verify_password(body.password, checked_hash)
    # Password changes and disabling a user revoke sessions under the same row
    # lock. Re-read after scrypt; never issue a fresh session from stale state.
    user = db.scalar(select(User).where(User.id == user.id).with_for_update()
                     .execution_options(populate_existing=True)) if user and valid else None
    if not user or user.disabled or user.password_hash != checked_hash:
        db.rollback()
        raise HTTPException(401, "用户名或密码不正确")
    attempt.succeeded = True
    session, token = create_session(db, user)
    audit(db, user, "login", "session", session.id)
    db.commit()
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure, samesite="strict",
                        max_age=settings.session_hours * 3600, path="/")
    return {"user": {"id": user.id, "username": user.username, "role": user.role}, "csrf_token": session.csrf_token}


@app.get(P + "/auth/me")
def me(identity=Depends(authenticated)):
    user, session = identity
    return {"user": {"id": user.id, "username": user.username, "role": user.role}, "csrf_token": session.csrf_token}


@app.post(P + "/auth/logout")
def logout(response: Response, identity=Depends(authenticated), db: Session = Depends(get_db)):
    db.delete(identity[1])
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get(P + "/library/stats")
def library_stats(db: Session = Depends(get_db)):
    return catalog.stats(db)


@app.get(P + "/library/settings")
def library_settings(db: Session = Depends(get_db)):
    row = db.get(Setting, "display")
    return schemas.DisplaySettings(**(row.value if row else {})).model_dump()


@app.get(P + "/videos")
def videos(q: str = Query("", max_length=200), creator_id: str = "", collection_id: str = "", tag: str = "",
           starred: bool = False, page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100), sort: str = "newest",
           identity=Depends(optional_identity), db: Session = Depends(get_db)):
    stmt = select(Video).outerjoin(VideoAnnotation, VideoAnnotation.video_id == Video.id)
    if q.strip():
        pattern = like(q.strip())
        creators = select(VideoCreator.video_id).join(Creator, Creator.id == VideoCreator.creator_id).join(PlatformUser, PlatformUser.id == Creator.user_id)
        creators = creators.where(or_(PlatformUser.display_name.ilike(pattern, escape="\\"), Creator.alias.ilike(pattern, escape="\\")))
        stmt = stmt.where(or_(Video.title.ilike(pattern, escape="\\"), VideoAnnotation.title_override.ilike(pattern, escape="\\"),
                             Video.bvid == q.strip(), Video.aid == q.strip().removeprefix("av"), Video.id.in_(creators),
                             cast(VideoAnnotation.tags, String).ilike(pattern, escape="\\")))
    if creator_id:
        stmt = stmt.where(Video.id.in_(select(VideoCreator.video_id).where(VideoCreator.creator_id == creator_id)))
    if collection_id:
        stmt = stmt.where(Video.id.in_(select(CollectionItem.video_id).where(CollectionItem.collection_id == collection_id)))
    if tag:
        stmt = stmt.where(cast(VideoAnnotation.tags, String).ilike(like(json.dumps(tag, ensure_ascii=False)), escape="\\"))
    if starred:
        stmt = stmt.where(Video.id.in_(select(VideoStar.video_id).where(VideoStar.user_id == identity[0].id))) if identity else stmt.where(False)
    ordering = {"newest": Video.created_at.desc(), "oldest": Video.created_at.asc(),
                "title": func.coalesce(VideoAnnotation.title_override, Video.title).asc(), "duration": Video.duration.desc(),
                "published": Video.published_at.desc().nullslast()}
    stmt = stmt.order_by(ordering.get(sort, ordering["newest"]), Video.id)
    result = catalog.page(db, stmt, page, page_size, batch_mapper=catalog.video_views)
    catalog.personalize_videos(db, result["items"], identity)
    return result


@app.get(P + "/videos/{video_id}")
def video(video_id: str, identity=Depends(optional_identity), db: Session = Depends(get_db)):
    result = catalog.video_view(db, required(db, Video, video_id), detail=True)
    catalog.personalize_videos(db, [result], identity)
    return result


@app.get(P + "/playlists")
def playlist(collection_id: str = "", creator_id: str = "", page: int = Query(1, ge=1),
             page_size: int = Query(100, ge=1, le=100), identity=Depends(optional_identity), db: Session = Depends(get_db)):
    if bool(collection_id) == bool(creator_id):
        raise HTTPException(422, "请选择一个收藏夹或 UP 主")
    playable = select(MediaVariant.id).join(VideoPart, VideoPart.id == MediaVariant.part_id).where(VideoPart.video_id == Video.id).exists()
    stmt = select(Video).where(playable)
    if collection_id:
        collection = required(db, Collection, collection_id)
        # A source can mention the same archived video more than once. Rank it
        # once by its first source position, then paginate deterministically.
        members = (select(CollectionItem.video_id, func.min(CollectionItem.position).label("position"))
                   .where(CollectionItem.collection_id == collection_id, CollectionItem.video_id.is_not(None))
                   .group_by(CollectionItem.video_id).subquery())
        stmt = stmt.join(members, members.c.video_id == Video.id).order_by(members.c.position, Video.id)
        title = display_source_title(collection)
    else:
        creator = required(db, Creator, creator_id)
        stmt = stmt.where(Video.id.in_(select(VideoCreator.video_id).where(VideoCreator.creator_id == creator_id)))
        stmt = stmt.order_by(Video.published_at.desc().nullslast(), Video.id)
        title = catalog.creator_view(db, creator)["name"]
    result = catalog.page(db, stmt, page, page_size, batch_mapper=catalog.video_views)
    catalog.personalize_videos(db, result["items"], identity)
    result["scope_title"] = title
    return result


@app.put(P + "/videos/{video_id}/star")
def star_video(video_id: str, body: schemas.StarInput, identity=Depends(authenticated), db: Session = Depends(get_db)):
    required(db, Video, video_id)
    user = identity[0]
    db.scalar(select(User).where(User.id == user.id).with_for_update(key_share=True))
    current = db.scalar(select(VideoStar).where(VideoStar.user_id == user.id, VideoStar.video_id == video_id))
    if body.starred and not current:
        db.add(VideoStar(user_id=user.id, video_id=video_id))
    elif not body.starred and current:
        db.delete(current)
    db.commit()
    return {"starred": body.starred}


@app.get(P + "/creators")
def creators(q: str = Query("", max_length=200), page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100),
             db: Session = Depends(get_db)):
    stmt = select(Creator).join(PlatformUser, PlatformUser.id == Creator.user_id)
    if q.strip():
        pattern = like(q.strip())
        history = select(UserSnapshot.user_id).where(UserSnapshot.display_name.ilike(pattern, escape="\\"))
        stmt = stmt.where(or_(Creator.alias.ilike(pattern, escape="\\"), PlatformUser.display_name.ilike(pattern, escape="\\"),
                             PlatformUser.signature.ilike(pattern, escape="\\"), Creator.description_override.ilike(pattern, escape="\\"),
                             PlatformUser.uid == q.strip(), PlatformUser.id.in_(history)))
    result = catalog.page(db, stmt.order_by(Creator.created_at.desc(), Creator.id), page, page_size, batch_mapper=catalog.creator_views)
    for row in result["items"]:
        row.pop("notes", None)
    return result


@app.get(P + "/creators/{creator_id}")
def creator(creator_id: str, identity=Depends(optional_identity), db: Session = Depends(get_db)):
    item = required(db, Creator, creator_id)
    result = catalog.creator_view(db, item)
    result.update(alias=item.alias, description_override=item.description_override)
    if not identity or identity[0].role not in {"admin", "editor"}:
        result.pop("notes", None)
    return result


@app.get(P + "/collections")
def collections(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100),
                db: Session = Depends(get_db)):
    return catalog.page(db, select(Collection).order_by(Collection.created_at.desc(), Collection.id), page, page_size, batch_mapper=catalog.collection_views)


@app.get(P + "/videos/{video_id}/comments")
def comments(video_id: str, root: str = "", q: str = Query("", max_length=200), page: int = Query(1, ge=1),
             page_size: int = Query(20, ge=1, le=100), sort: str = Query("likes", pattern="^(likes|newest)$"), db: Session = Depends(get_db)):
    required(db, Video, video_id)
    stmt = select(Comment).where(Comment.video_id == video_id)
    if root:
        stmt = stmt.where(Comment.root_rpid == root, Comment.rpid != root)
    elif not q:
        stmt = stmt.where(or_(Comment.root_rpid == "0", Comment.root_rpid == "", Comment.root_rpid == Comment.rpid))
    if q:
        stmt = stmt.where(Comment.content.ilike(like(q), escape="\\"))
    ordering = (Comment.like_count.desc(), Comment.posted_at.desc(), Comment.id) if sort == "likes" else (Comment.posted_at.desc(), Comment.id)
    return catalog.page(db, stmt.order_by(*ordering), page, page_size, batch_mapper=catalog.comment_views)


@app.api_route(P + "/assets/{asset_id}", methods=["GET", "HEAD"])
def asset(asset_id: str, request: Request, identity=Depends(optional_identity), db: Session = Depends(get_db)):
    from app.storage.service import resolve_asset
    required(db, Asset, asset_id)
    if not identity:
        public = db.scalar(select(or_(
            select(Video.id).where(Video.cover_asset_id == asset_id).exists(),
            select(MediaVariant.id).where(MediaVariant.asset_id == asset_id).exists(),
            select(PlatformUser.id).where(PlatformUser.avatar_asset_id == asset_id).exists(),
            select(UserSnapshot.id).where(UserSnapshot.avatar_asset_id == asset_id).exists(),
            select(CommentAsset.id).where(CommentAsset.asset_id == asset_id).exists(),
            select(SubtitleTrack.id).where(SubtitleTrack.asset_id == asset_id).exists())))
        if not public:
            raise HTTPException(404, "此文件不属于可浏览的内容")
    try:
        resolved = resolve_asset(db, asset_id)
    except Exception:
        raise HTTPException(503, "归档文件暂不可访问") from None
    if request.method == "HEAD":
        return Response(headers={"Content-Length": str(resolved["size"]), "Content-Type": resolved["mime_type"],
                                 "Accept-Ranges": "bytes"})
    if resolved["kind"] != "local":
        return RedirectResponse(resolved["url"], status_code=307)
    path = Path(resolved["path"])
    if settings.x_accel_prefix:
        try:
            key = path.resolve().relative_to(settings.media_root.resolve()).as_posix()
        except ValueError:
            raise HTTPException(503, "本地分发目录尚未配置") from None
        return Response(headers={"X-Accel-Redirect": settings.x_accel_prefix.rstrip("/") + "/" + quote(key, safe="/"),
                                 "Content-Type": resolved["mime_type"]})
    return FileResponse(path, media_type=resolved["mime_type"], headers={"Content-Disposition": "inline"})


@app.get(P + "/parts/{part_id}/danmaku")
def danmaku(part_id: str, db: Session = Depends(get_db)):
    from app.storage.service import read_asset_bytes
    required(db, VideoPart, part_id)
    snapshot = db.scalar(select(DanmakuSnapshot).where(DanmakuSnapshot.part_id == part_id, DanmakuSnapshot.data_asset_id.is_not(None))
                         .order_by(DanmakuSnapshot.created_at.desc()).limit(1))
    if not snapshot:
        return []
    try:
        payload = json.loads(read_asset_bytes(db, snapshot.data_asset_id))
        items = payload if isinstance(payload, list) else payload.get("items", [])
        modes = {1: 0, 2: 0, 3: 0, 5: 1, 4: 2}
        return [{**item, "mode": modes[item["mode"]]} for item in items if item.get("mode") in modes]
    except Exception:
        raise HTTPException(503, "弹幕归档暂不可读取") from None


@app.get(P + "/progress/{part_id}")
def progress(part_id: str, identity=Depends(authenticated), db: Session = Depends(get_db)):
    item = db.scalar(select(WatchProgress).where(WatchProgress.user_id == identity[0].id, WatchProgress.part_id == part_id))
    return {"position": item.position if item else 0, "duration": item.duration if item else 0}


@app.put(P + "/progress/{part_id}")
def save_progress(part_id: str, body: schemas.ProgressInput, identity=Depends(authenticated), db: Session = Depends(get_db)):
    required(db, VideoPart, part_id)
    item = db.scalar(select(WatchProgress).where(WatchProgress.user_id == identity[0].id, WatchProgress.part_id == part_id))
    if not item:
        item = WatchProgress(user_id=identity[0].id, part_id=part_id)
        db.add(item)
    item.position, item.duration = body.position, body.duration
    commit(db)
    return body


@app.patch(P + "/admin/videos/{video_id}")
def annotate_video(video_id: str, body: schemas.Annotation, user=Depends(require_editor), db: Session = Depends(get_db)):
    target = required(db, Video, video_id)
    note = db.scalar(select(VideoAnnotation).where(VideoAnnotation.video_id == video_id))
    if not note:
        note = VideoAnnotation(video_id=video_id)
        db.add(note)
    values = body.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(note, key, value)
    audit(db, user, "annotate", "video", video_id, {"fields": list(values)})
    commit(db)
    return catalog.video_view(db, target, detail=True)


@app.patch(P + "/admin/creators/{creator_id}")
def annotate_creator(creator_id: str, body: schemas.CreatorAnnotation, user=Depends(require_editor), db: Session = Depends(get_db)):
    target = required(db, Creator, creator_id)
    values = body.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(target, key, value)
    audit(db, user, "annotate", "creator", creator_id, {"fields": list(values)})
    commit(db)
    return catalog.creator_view(db, target)


def account_view(a):
    return {k: getattr(a, k) for k in ["id", "name", "uid", "status", "last_verified_at", "created_at",
                                     "next_request_at", "next_video_at", "cooldown_until", "risk_failures"]}


@app.patch(P + "/admin/accounts/{account_id}")
def update_account(account_id: str, body: schemas.AccountUpdate, user=Depends(require_admin), db: Session = Depends(get_db)):
    from app.ingest.runner import _lock
    from app.ingest.errors import IngestDeferred
    account = required(db, SourceAccount, account_id)
    try:
        with _lock(db, "account:" + account.id):
            if body.name is not None:
                account.name = body.name
            if body.cookie is not None:
                try:
                    account.secret_encrypted = encrypt_secret(body.cookie)
                except ValueError:
                    raise HTTPException(503, "采集凭据加密密钥未正确配置") from None
                account.status, account.uid, account.last_verified_at = "unverified", None, None
            audit(db, user, "update_credentials" if body.cookie else "rename", "source_account", account.id)
            commit(db)
    except IngestDeferred:
        raise HTTPException(409, "账号凭据正在更新，请稍后重试") from None
    return account_view(account)


@app.get(P + "/admin/accounts")
def accounts(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100), _=Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.page(db, select(SourceAccount).order_by(SourceAccount.created_at.desc(), SourceAccount.id), page, page_size, account_view)


@app.post(P + "/admin/accounts", status_code=201)
def new_account(body: schemas.AccountInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        secret = encrypt_secret(body.cookie)
    except ValueError:
        raise HTTPException(503, "采集凭据加密密钥未正确配置") from None
    item = SourceAccount(name=body.name, secret_encrypted=secret)
    db.add(item)
    db.flush()
    audit(db, user, "create", "source_account", item.id)
    commit(db)
    return account_view(item)


@app.post(P + "/admin/accounts/{account_id}/verify")
def verify_account(account_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    required(db, SourceAccount, account_id)
    job = enqueue(db, "verify_account", account_id, account_id)
    audit(db, user, "verify", "source_account", account_id)
    commit(db)
    return catalog.job_view(job)


def source_view(db, s, collection=None):
    from app.source_labels import display_source_title
    c = collection if collection is not None else required(db, Collection, s.collection_id)
    return {"id": s.id, "collection_id": s.collection_id, "source_id": c.source_id, "title": display_source_title(c),
            "account_id": s.account_id, "enabled": s.enabled, "interval_minutes": s.interval_minutes,
            "policy": s.policy, "next_run_at": s.next_run_at, "last_scan_at": c.last_scan_at,
            "kind": c.kind, "monitor": c.monitor_state or {}}


@app.post(P + "/admin/sources/resolve")
def resolve_source(body: schemas.SourceResolve, _=Depends(require_admin)):
    from app.source_links import resolve_source as parse_source
    try:
        return parse_source(body.value, body.kind)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get(P + "/admin/sources")
def sources(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100),
            kind: str = Query("", pattern="^(favorite|creator)?$"), source_id: str = Query("", max_length=32),
            _=Depends(require_admin), db: Session = Depends(get_db)):
    statement = select(SourceSubscription, Collection).join(Collection, Collection.id == SourceSubscription.collection_id)
    if kind:
        statement = statement.where(Collection.kind == kind)
    if source_id:
        statement = statement.where(Collection.source_id == source_id)
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.execute(statement.order_by(SourceSubscription.created_at.desc(), SourceSubscription.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [source_view(db, s, c) for s, c in rows], "total": total, "page": page, "page_size": page_size}


@app.post(P + "/admin/sources", status_code=201)
def new_source(body: schemas.SourceInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    required(db, SourceAccount, body.account_id)
    collection = db.scalar(select(Collection).where(Collection.kind == body.kind, Collection.source_id == body.source_id))
    if collection and db.scalar(select(SourceSubscription.id).where(SourceSubscription.collection_id == collection.id)):
        raise HTTPException(409, "该来源已添加，请编辑已有来源")
    if not collection:
        collection = Collection(source_id=body.source_id, title=body.title, kind=body.kind,
                                owner_uid=body.source_id if body.kind == "creator" else None)
        db.add(collection)
        db.flush()
    source = SourceSubscription(collection_id=collection.id, account_id=body.account_id, enabled=body.enabled,
                                interval_minutes=body.interval_minutes, policy=body.policy.model_dump(exclude_unset=True), next_run_at=utcnow())
    db.add(source)
    db.flush()
    audit(db, user, "create", "source", source.id)
    commit(db)
    return source_view(db, source)


@app.patch(P + "/admin/sources/{source_id}")
def edit_source(source_id: str, body: schemas.SourceUpdate, user=Depends(require_admin), db: Session = Depends(get_db)):
    target = required(db, SourceSubscription, source_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("account_id"):
        required(db, SourceAccount, values["account_id"])
    if "title" in values:
        required(db, Collection, target.collection_id).title = values.pop("title")
    for key, value in values.items():
        if value is not None:
            setattr(target, key, value)
    audit(db, user, "update", "source", source_id, {"fields": list(values)})
    commit(db)
    return source_view(db, target)


@app.post(P + "/admin/sources/{source_id}/scan")
def scan_source(source_id: str, body: schemas.SourceScan = Body(default=schemas.SourceScan()), user=Depends(require_admin), db: Session = Depends(get_db)):
    source = db.scalar(select(SourceSubscription).where(SourceSubscription.id == source_id).with_for_update())
    if not source:
        raise HTTPException(404, "来源不存在")
    active = db.scalar(select(Job.id).where(Job.kind == "scan_collection", Job.target_id == source.collection_id,
                       Job.status.in_(["queued", "running", "paused", "blocked"])).limit(1))
    if active:
        raise HTTPException(409, "该来源已有未结束的检查，请在任务中心查看或恢复")
    job = enqueue(db, "scan_collection", source.collection_id, source.account_id,
                  {**source.policy, "force_full_scan": body.full})
    audit(db, user, "scan", "source", source_id)
    commit(db)
    return catalog.job_view(job)


@app.get(P + "/admin/sources/{source_id}/history")
def source_history(source_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   _=Depends(require_admin), db: Session = Depends(get_db)):
    source = required(db, SourceSubscription, source_id)
    return catalog.page(db, select(CaptureRun).where(CaptureRun.collection_id == source.collection_id)
                        .order_by(CaptureRun.started_at.desc(), CaptureRun.id), page, page_size,
                        lambda row: {k: getattr(row, k) for k in ["id", "status", "counts", "end_reason", "started_at", "finished_at"]})


@app.get(P + "/creators/{creator_id}/monitor")
def creator_monitor(creator_id: str, db: Session = Depends(get_db)):
    creator = required(db, Creator, creator_id)
    person = required(db, PlatformUser, creator.user_id)
    row = db.execute(select(Collection, SourceSubscription).join(SourceSubscription, SourceSubscription.collection_id == Collection.id)
                     .where(Collection.kind == "creator", Collection.source_id == person.uid)).first()
    if not row:
        return {"subscribed": False}
    collection, source = row
    return {"subscribed": True, "enabled": source.enabled, "next_run_at": source.next_run_at,
            "last_scan_at": collection.last_scan_at, "monitor": collection.monitor_state or {}}


@app.get(P + "/admin/jobs")
def jobs(status: str = "", page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
         _=Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    return catalog.page(db, stmt.order_by(Job.created_at.desc(), Job.id), page, page_size, batch_mapper=catalog.job_views)


@app.get(P + "/admin/jobs/{job_id}")
def job_detail(job_id: str, _=Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.job_views(db, [required(db, Job, job_id)])[0]


@app.post(P + "/admin/jobs", status_code=201)
def new_job(body: schemas.JobInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    if body.account_id:
        required(db, SourceAccount, body.account_id)
    if body.kind == "scan_collection":
        required(db, Collection, body.target_id)
    if body.kind == "refresh_comments":
        required(db, Video, body.target_id)
    if body.kind == "verify_account":
        required(db, SourceAccount, body.target_id)
    if not body.account_id and body.kind != "verify_account":
        raise HTTPException(422, "请选择采集账号")
    from app.ingest.errors import IngestDeferred
    try:
        item = enqueue(db, body.kind, body.target_id, body.account_id, body.policy.model_dump(exclude_unset=True))
    except IngestDeferred as error:
        db.rollback()
        raise HTTPException(409, str(error), headers={"Retry-After": "1"}) from None
    audit(db, user, "enqueue", "job", item.id)
    commit(db)
    return catalog.job_view(item)


@app.post(P + "/admin/jobs/{job_id}/{action}")
def job_action(job_id: str, action: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    item = required(db, Job, job_id)
    allowed = {"retry": {"failed", "partial", "blocked", "cancelled"}, "resume": {"paused"},
               "pause": {"queued", "running"}, "cancel": {"queued", "running", "paused", "blocked", "partial"}}
    if action not in allowed or item.status not in allowed[action]:
        raise HTTPException(409, "当前任务状态不能执行此操作")
    if action in {"resume", "retry"}:
        if item.lease_expires_at and item.lease_expires_at.replace(tzinfo=timezone.utc) > utcnow():
            raise HTTPException(409, "执行进程仍在停止，请稍后重试")
        item.status, item.available_at, item.error = "queued", utcnow(), None
        item.finished_at, item.lease_owner, item.lease_expires_at = None, None, None
        item.attempts = 0
        db.add(OutboxEvent(job_id=item.id))
    else:
        item.status = "paused" if action == "pause" else "cancelled"
    audit(db, user, action, "job", job_id)
    commit(db)
    return catalog.job_view(item)


def storage_view(s):
    return {"id": s.id, "name": s.name, "kind": s.kind, "config": s.config,
            "has_credentials": bool(s.secret_encrypted), "is_default": s.is_default, "enabled": s.enabled, "created_at": s.created_at}


def validate_storage(body):
    permitted = {"root", "prefix", "endpoint", "public_endpoint", "region", "bucket", "addressing_style", "read_priority", "part_size"}
    if set(body.config) - permitted:
        raise HTTPException(422, "存储配置含未知字段；凭据请使用独立凭据字段")
    for key in permitted - {"read_priority", "part_size"}:
        if key in {"endpoint", "public_endpoint"} and body.config.get(key) is None:
            continue
        if key in body.config and not isinstance(body.config[key], str):
            raise HTTPException(422, "存储地址、目录和名称须为文本")
    if "read_priority" in body.config and (not isinstance(body.config["read_priority"], int) or not 0 <= body.config["read_priority"] <= 10000):
        raise HTTPException(422, "读取优先级须为0至10000的整数，数值越小越优先")
    if "part_size" in body.config and (not isinstance(body.config["part_size"], int) or not 5 * 1024**2 <= body.config["part_size"] <= 512 * 1024**2):
        raise HTTPException(422, "分片大小须为5至512MiB")
    if body.kind == "local":
        root = Path(body.config.get("root", str(settings.media_root))).resolve()
        if not root.is_relative_to(settings.media_root.resolve()):
            raise HTTPException(422, "本地存储目录必须位于已挂载媒体根目录内")
    else:
        if not body.config.get("bucket"):
            raise HTTPException(422, "请填写存储桶名称")
        from app.storage.cloud import _endpoint
        from app.storage.base import StorageError
        try:
            for key in ("endpoint", "public_endpoint"):
                if body.config.get(key):
                    _endpoint(body.config[key])
        except StorageError:
            raise HTTPException(422, "服务地址须为完整的 HTTP(S) 地址，不要包含账号、路径或参数") from None


@app.get(P + "/admin/storage")
def storage(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100), _=Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.page(db, select(StorageProfile).order_by(StorageProfile.is_default.desc(), StorageProfile.created_at, StorageProfile.id), page, page_size, storage_view)


def write_storage(db, body, item=None):
    from sqlalchemy.exc import DBAPIError
    from app.storage.service import lock_storage_configuration, storage_placement
    validate_storage(body)
    if not lock_storage_configuration(db, wait=False):
        db.rollback()
        raise HTTPException(409, "存储配置正在更新，请稍后重试")
    previous_defaults = []
    try:
        if item is not None:
            item = db.scalar(select(StorageProfile).where(StorageProfile.id == item.id).with_for_update(nowait=True)
                             .execution_options(populate_existing=True))
            if item is None:
                raise HTTPException(404, "存储位置不存在")
        if body.is_default:
            # Clearing the old default also updates its row. Lock every row
            # before mutation, without waiting while holding the writer lock.
            previous_defaults = list(db.scalars(select(StorageProfile).where(StorageProfile.is_default.is_(True))
                                     .order_by(StorageProfile.id).with_for_update(nowait=True)
                                     .execution_options(populate_existing=True)))
    except DBAPIError as exc:
        if (getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)) != "55P03":
            raise
        db.rollback()
        raise HTTPException(409, "存储位置正在登记资产，请稍后重试配置") from None
    if body.is_default and not body.enabled:
        raise HTTPException(422, "默认存储必须启用")
    if item and db.scalar(select(AssetLocation.id).where(AssetLocation.storage_profile_id == item.id).limit(1)):
        if storage_placement(body.kind, body.config) != storage_placement(item.kind, item.config):
            raise HTTPException(409, "此位置已有资产；请新建存储并执行迁移，不能修改定位配置")
    if body.kind != "local":
        credentials = body.credentials
        if credentials is None and (item is None or item.kind != body.kind or not item.secret_encrypted):
            raise HTTPException(422, "请填写此存储服务的访问凭据")
        if credentials is not None:
            secret_name = "secret_access_key" if body.kind == "s3" else "access_key_secret"
            if not all(isinstance(credentials.get(key), str) and credentials[key].strip() for key in ("access_key_id", secret_name)):
                raise HTTPException(422, "请完整填写访问密钥 ID 和访问密钥")
    if body.is_default:
        for existing in previous_defaults:
            existing.is_default = False
    if item is None:
        item = StorageProfile()
        db.add(item)
    item.name, item.kind, item.config, item.enabled, item.is_default = body.name, body.kind, body.config, body.enabled, body.is_default
    if body.kind == "local":
        item.secret_encrypted = None
    elif body.credentials is not None:
        try:
            item.secret_encrypted = encrypt_secret(json.dumps(body.credentials))
        except ValueError:
            raise HTTPException(503, "存储凭据加密密钥未正确配置") from None
    db.flush()
    return item


@app.post(P + "/admin/storage", status_code=201)
def new_storage(body: schemas.StorageInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    item = write_storage(db, body)
    audit(db, user, "create", "storage", item.id)
    commit(db)
    return storage_view(item)


@app.patch(P + "/admin/storage/{profile_id}")
def update_storage(profile_id: str, body: schemas.StorageInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    item = write_storage(db, body, required(db, StorageProfile, profile_id))
    audit(db, user, "update", "storage", item.id)
    commit(db)
    return storage_view(item)


@app.post(P + "/admin/storage/{profile_id}/probe", status_code=202)
def probe(profile_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    required(db, StorageProfile, profile_id)
    job = enqueue(db, "probe_storage", profile_id)
    audit(db, user, "probe", "storage", profile_id, {"job_id": job.id})
    commit(db)
    return catalog.job_view(job)


@app.post(P + "/admin/storage/{profile_id}/migrate")
def migrate(profile_id: str, body: schemas.MigrationInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    required(db, StorageProfile, profile_id)
    target = required(db, StorageProfile, body.target_profile_id)
    if not target.enabled or target.id == profile_id:
        raise HTTPException(422, "请选择另一个已启用的位置")
    if body.asset_ids == []:
        raise HTTPException(422, "所选资产为空；全部迁移请省略asset_ids")
    job = enqueue(db, "migrate_storage", profile_id, policy=body.model_dump())
    audit(db, user, "migrate", "storage", profile_id, {"target_profile_id": target.id})
    commit(db)
    return catalog.job_view(job)


@app.get(P + "/admin/settings")
def all_settings(_=Depends(require_admin), db: Session = Depends(get_db)):
    defaults = {"display": schemas.DisplaySettings().model_dump(), "ingest": schemas.IngestPolicy().model_dump(),
                "backup": schemas.BackupSettings().model_dump(), "playback": schemas.PlaybackSettings().model_dump(),
                "statistics": schemas.StatisticsSettings().model_dump()}
    for row in db.scalars(select(Setting).where(Setting.key.in_(list(defaults)))):
        defaults[row.key] = {**defaults[row.key], **row.value}
    return defaults


@app.patch(P + "/admin/settings")
def save_settings(body: schemas.SettingsInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        row = db.get(Setting, key)
        merged = {**(row.value if row else {}), **value}
        if key == "statistics":
            config = schemas.StatisticsSettings.model_validate(merged)
            if config.enabled and (not config.account_id or not db.get(SourceAccount, config.account_id)):
                raise HTTPException(422, "定时更新统计需要选择有效采集账号")
        if row is None:
            db.add(Setting(key=key, value=merged))
        else:
            row.value = merged
    audit(db, user, "update", "settings", details={"keys": list(body.model_fields_set)})
    commit(db)
    return all_settings(user, db)


@app.get(P + "/admin/backups")
def backups(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100), _=Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.page(db, select(BackupSet).order_by(BackupSet.created_at.desc()), page, page_size, catalog.backup_view)


@app.post(P + "/admin/backups", status_code=202)
def backup_now(user=Depends(require_admin), db: Session = Depends(get_db)):
    config = db.get(Setting, "backup")
    if not config or not config.value.get("destination") or not config.value.get("key_id"):
        raise HTTPException(409, "请先配置独立备份目录与密钥标识")
    job = enqueue(db, "backup", "library")
    audit(db, user, "enqueue", "backup", job.id)
    commit(db)
    return catalog.job_view(job)


@app.get(P + "/admin/overview")
def overview(_=Depends(require_admin), db: Session = Depends(get_db)):
    from app.backup import rpo_status
    return {"stats": catalog.stats(db), "backup_protection": rpo_status(db),
            "jobs": [catalog.job_view(j) for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(8))],
            "storage": [storage_view(s) for s in db.scalars(select(StorageProfile))],
            "backups": [catalog.backup_view(b) for b in db.scalars(select(BackupSet).order_by(BackupSet.created_at.desc()).limit(5))]}


@app.get(P + "/admin/audit")
def audit_log(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100), _=Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.page(db, select(AuditLog).order_by(AuditLog.created_at.desc()), page, page_size,
                        lambda a: {k: getattr(a, k) for k in ["id", "actor_id", "action", "entity_type", "entity_id", "details", "created_at"]})


@app.get(P + "/admin/users")
def users(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100), _=Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.page(db, select(User).order_by(User.created_at, User.id), page, page_size,
                        lambda u: {k: getattr(u, k) for k in ["id", "username", "role", "disabled", "created_at"]})


@app.post(P + "/admin/users", status_code=201)
def new_user(body: schemas.UserCreate, user=Depends(require_admin), db: Session = Depends(get_db)):
    item = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(item)
    db.flush()
    audit(db, user, "create", "user", item.id)
    commit(db)
    return {"id": item.id, "username": item.username, "role": item.role, "disabled": item.disabled}


@app.patch(P + "/admin/users/{user_id}")
def update_user(user_id: str, body: schemas.UserUpdate, user=Depends(require_admin), db: Session = Depends(get_db)):
    # No identity keys change here. NO KEY UPDATE permits audit's actor FK
    # check when two administrators edit each other, while serializing logins.
    item = db.scalar(select(User).where(User.id == user_id).with_for_update(key_share=True).execution_options(populate_existing=True))
    if not item:
        raise HTTPException(404, "记录不存在")
    if item.id == user.id and (body.disabled or body.role not in {None, "admin"}):
        raise HTTPException(409, "不能停用或降级当前管理员")
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in values.items():
        if key == "password":
            item.password_hash = hash_password(value)
        else:
            setattr(item, key, value)
    db.execute(delete(UserSession).where(UserSession.user_id == item.id))
    audit(db, user, "update", "user", item.id, {"fields": list(values)})
    commit(db)
    return {"id": item.id, "username": item.username, "role": item.role, "disabled": item.disabled}


from app.delivery_api import router as delivery_router
app.include_router(delivery_router)
from app.passkeys import router as passkeys_router
app.include_router(passkeys_router)
from app.source_discovery import router as source_discovery_router
app.include_router(source_discovery_router)
from app.library_deletion import router as library_deletion_router
app.include_router(library_deletion_router)
from app.userscript_api import router as userscript_router
app.include_router(userscript_router)
