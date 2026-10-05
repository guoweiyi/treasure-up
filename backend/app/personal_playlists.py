"""Authenticated personal lists. Membership never grants extra media privileges."""
from contextlib import contextmanager, nullcontext
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import Integer, cast, delete, func, insert, literal, select
from sqlalchemy.orm import Session

from app import catalog
from app.db import get_db
from app.models import MediaVariant, PersonalPlaylist, PersonalPlaylistItem, User, Video, VideoAnnotation, VideoPart, VideoStar, utcnow
from app.schemas import Input
from app.security import authenticated

router = APIRouter(prefix="/api/v1/me")
_sqlite_lock = threading.RLock()


class PlaylistInput(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("片单名称不能为空")
        return value.strip()


class PlaylistUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        return PlaylistInput.nonblank(value) if value is not None else value


class ItemInput(Input):
    note: str | None = Field(default=None, max_length=2000)
    watched: bool | None = None


class MoveInput(Input):
    target_playlist_id: str = Field(min_length=1, max_length=36)


@contextmanager
def _mutation(db, user_id):
    # Serialize all a user's list mutations in one order, including cross-list
    # moves. NO KEY UPDATE remains compatible with session/audit foreign keys.
    with _sqlite_lock if db.get_bind().dialect.name != "postgresql" else nullcontext():
        user = db.scalar(select(User).where(User.id == user_id).with_for_update(key_share=True)
                         .execution_options(populate_existing=True))
        if not user or user.disabled:
            raise HTTPException(401, "会话已失效")
        try:
            yield
            db.commit()
        except Exception:
            db.rollback()
            raise


def _watch_later(db, user_id):
    """Called under the user lock; also supports create_all development DBs."""
    row = db.scalar(select(PersonalPlaylist).where(PersonalPlaylist.user_id == user_id,
                                                  PersonalPlaylist.system_key == "watch_later"))
    if row is None:
        row = PersonalPlaylist(user_id=user_id, name="稍后看", system_key="watch_later")
        db.add(row)
        db.flush()
        db.execute(insert(PersonalPlaylistItem).from_select(
            ["id", "created_at", "updated_at", "playlist_id", "video_id", "note", "watched"],
            select(VideoStar.id, VideoStar.created_at, VideoStar.updated_at, literal(row.id),
                   VideoStar.video_id, literal(""), literal(False)).where(VideoStar.user_id == user_id)))
    return row


def _sync_star(db, user_id, video_id, starred):
    current = db.scalar(select(VideoStar).where(VideoStar.user_id == user_id, VideoStar.video_id == video_id))
    if starred and current is None:
        db.add(VideoStar(user_id=user_id, video_id=video_id))
    elif not starred and current is not None:
        db.delete(current)


def set_star(db, user_id, video_id, starred):
    """The legacy star API and default list share the same serialized mutation."""
    with _mutation(db, user_id):
        if db.get(Video, video_id) is None:
            raise HTTPException(404, "视频不存在")
        row = _watch_later(db, user_id)
        item = _item(db, row.id, video_id)
        if starred and item is None:
            db.add(PersonalPlaylistItem(playlist_id=row.id, video_id=video_id))
        elif not starred and item is not None:
            db.delete(item)
        _sync_star(db, user_id, video_id, starred)
        row.updated_at = utcnow()
    return {"starred": starred}


def _owned(db, identifier, user_id):
    row = db.scalar(select(PersonalPlaylist).where(PersonalPlaylist.id == identifier, PersonalPlaylist.user_id == user_id))
    if row is None:
        raise HTTPException(404, "片单不存在")
    return row


def _item(db, playlist_id, video_id):
    return db.scalar(select(PersonalPlaylistItem).where(PersonalPlaylistItem.playlist_id == playlist_id,
                                                       PersonalPlaylistItem.video_id == video_id))


def _views(db, rows):
    counts = {key: (total, unseen) for key, total, unseen in db.execute(select(
        PersonalPlaylistItem.playlist_id, func.count(),
        func.sum(cast(~PersonalPlaylistItem.watched, Integer)))
        .where(PersonalPlaylistItem.playlist_id.in_([row.id for row in rows])).group_by(PersonalPlaylistItem.playlist_id))}
    return [{"id": row.id, "name": row.name, "description": row.description,
        "kind": row.system_key or "custom", "item_count": counts.get(row.id, (0, 0))[0],
        "unwatched_count": counts.get(row.id, (0, 0))[1], "created_at": row.created_at, "updated_at": row.updated_at} for row in rows]


@router.get("/playlists")
def playlists(identity=Depends(authenticated), db: Session = Depends(get_db)):
    user_id = identity[0].id
    if db.scalar(select(PersonalPlaylist.id).where(PersonalPlaylist.user_id == user_id, PersonalPlaylist.system_key == "watch_later")) is None:
        with _mutation(db, user_id):
            _watch_later(db, user_id)
    rows = list(db.scalars(select(PersonalPlaylist).where(PersonalPlaylist.user_id == user_id)
        .order_by(PersonalPlaylist.system_key.desc().nullslast(), PersonalPlaylist.created_at, PersonalPlaylist.id)))
    return {"items": _views(db, rows), "total": len(rows)}


@router.post("/playlists", status_code=201)
def create_playlist(body: PlaylistInput, identity=Depends(authenticated), db: Session = Depends(get_db)):
    with _mutation(db, identity[0].id):
        count = db.scalar(select(func.count()).select_from(PersonalPlaylist).where(PersonalPlaylist.user_id == identity[0].id,
                                                                                PersonalPlaylist.system_key.is_(None)))
        if count >= 100:
            raise HTTPException(409, "最多创建 100 个个人片单")
        row = PersonalPlaylist(user_id=identity[0].id, **body.model_dump())
        db.add(row)
    return _views(db, [row])[0]


@router.patch("/playlists/{playlist_id}")
def edit_playlist(playlist_id: str, body: PlaylistUpdate, identity=Depends(authenticated), db: Session = Depends(get_db)):
    with _mutation(db, identity[0].id):
        row = _owned(db, playlist_id, identity[0].id)
        for key, value in body.model_dump(exclude_none=True).items():
            if key == "name" and row.system_key:
                raise HTTPException(409, "默认稍后看片单保留名称")
            setattr(row, key, value)
    return _views(db, [row])[0]


@router.delete("/playlists/{playlist_id}")
def remove_playlist(playlist_id: str, identity=Depends(authenticated), db: Session = Depends(get_db)):
    with _mutation(db, identity[0].id):
        row = _owned(db, playlist_id, identity[0].id)
        if row.system_key:
            raise HTTPException(409, "默认稍后看片单不能删除")
        db.execute(delete(PersonalPlaylistItem).where(PersonalPlaylistItem.playlist_id == row.id))
        db.delete(row)
    return {"deleted": True}


@router.get("/playlists/{playlist_id}/videos")
def playlist_videos(playlist_id: str, page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100),
                    q: str = Query("", max_length=200), watched: Literal["all", "watched", "unwatched"] = "all",
                    playable_only: bool = False,
                    identity=Depends(authenticated), db: Session = Depends(get_db)):
    row = _owned(db, playlist_id, identity[0].id)
    stmt = select(Video, PersonalPlaylistItem).join(PersonalPlaylistItem,
        PersonalPlaylistItem.video_id == Video.id).where(PersonalPlaylistItem.playlist_id == row.id)
    if playable_only:
        stmt = stmt.where(select(MediaVariant.id).join(VideoPart, VideoPart.id == MediaVariant.part_id)
                          .where(VideoPart.video_id == Video.id).exists())
    if watched != "all":
        stmt = stmt.where(PersonalPlaylistItem.watched.is_(watched == "watched"))
    if q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.outerjoin(VideoAnnotation, VideoAnnotation.video_id == Video.id).where(
            func.coalesce(VideoAnnotation.title_override, Video.title).ilike(pattern, escape="\\"))
    stmt = stmt.order_by(PersonalPlaylistItem.created_at.desc(), PersonalPlaylistItem.id)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    # Materialize each video and its membership in the same statement. Another
    # tab can move/remove the membership while catalog hydration is running.
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    members = {video.id: {"note": item.note, "watched": item.watched,
        "added_at": item.created_at, "updated_at": item.updated_at} for video, item in rows}
    result = {"items": catalog.video_views(db, [video for video, _ in rows]),
              "total": total, "page": page, "page_size": page_size}
    catalog.personalize_videos(db, result["items"], identity)
    for video in result["items"]:
        video["playlist_item"] = members[video["id"]]
    return {**result, "scope_title": row.name}


@router.put("/playlists/{playlist_id}/videos/{video_id}")
def save_item(playlist_id: str, video_id: str, body: ItemInput, identity=Depends(authenticated), db: Session = Depends(get_db)):
    with _mutation(db, identity[0].id):
        row = _owned(db, playlist_id, identity[0].id)
        if not db.get(Video, video_id):
            raise HTTPException(404, "视频不存在")
        item = _item(db, row.id, video_id)
        if item is None:
            item = PersonalPlaylistItem(playlist_id=row.id, video_id=video_id)
            db.add(item)
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(item, key, value)
        if row.system_key == "watch_later":
            _sync_star(db, identity[0].id, video_id, True)
        row.updated_at = utcnow()
    return {"playlist_id": row.id, "video_id": video_id, "note": item.note, "watched": item.watched}


@router.delete("/playlists/{playlist_id}/videos/{video_id}")
def remove_item(playlist_id: str, video_id: str, identity=Depends(authenticated), db: Session = Depends(get_db)):
    with _mutation(db, identity[0].id):
        row = _owned(db, playlist_id, identity[0].id)
        item = _item(db, row.id, video_id)
        if item:
            db.delete(item)
            row.updated_at = utcnow()
        if row.system_key == "watch_later":
            _sync_star(db, identity[0].id, video_id, False)
    return {"removed": True}


@router.post("/playlists/{playlist_id}/videos/{video_id}/move")
def move_item(playlist_id: str, video_id: str, body: MoveInput, identity=Depends(authenticated), db: Session = Depends(get_db)):
    with _mutation(db, identity[0].id):
        source = _owned(db, playlist_id, identity[0].id)
        target = _owned(db, body.target_playlist_id, identity[0].id)
        item = _item(db, source.id, video_id)
        if item is None:
            raise HTTPException(404, "片单条目不存在")
        if target.id != source.id:
            existing = _item(db, target.id, video_id)
            if existing:
                # Preserve the target's note; fill only an empty note, and never
                # undo a user's watched state when combining duplicate entries.
                existing.note = existing.note or item.note
                existing.watched = existing.watched or item.watched
                db.delete(item)
            else:
                item.playlist_id = target.id
            if source.system_key == "watch_later" or target.system_key == "watch_later":
                _sync_star(db, identity[0].id, video_id, target.system_key == "watch_later")
            source.updated_at = target.updated_at = utcnow()
    return {"moved": True, "playlist_id": target.id, "video_id": video_id}


@router.get("/videos/{video_id}/playlists")
def video_memberships(video_id: str, identity=Depends(authenticated), db: Session = Depends(get_db)):
    return {"playlist_ids": list(db.scalars(select(PersonalPlaylistItem.playlist_id).join(
        PersonalPlaylist, PersonalPlaylist.id == PersonalPlaylistItem.playlist_id).where(
        PersonalPlaylist.user_id == identity[0].id, PersonalPlaylistItem.video_id == video_id)))}
