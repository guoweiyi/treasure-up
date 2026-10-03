from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (Asset, CaptureRun, Collection, CollectionItem, Comment, CommentAsset, Creator,
                        Job, MediaVariant, PlatformUser, UserSnapshot, Video, VideoAnnotation,
                        VideoCreator, VideoPart)


def asset_url(asset_id):
    return f"/api/v1/assets/{asset_id}" if asset_id else None


def effective(override, original):
    return original if override is None else override


def page(db, statement, page=1, page_size=24, mapper=None):
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    items = db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [mapper(v) for v in items] if mapper else items, "total": total, "page": page, "page_size": page_size}


def stats(db: Session):
    return {
        "videos": db.scalar(select(func.count()).select_from(Video)),
        "creators": db.scalar(select(func.count()).select_from(Creator)),
        "collections": db.scalar(select(func.count()).select_from(Collection)),
        "assets_bytes": db.scalar(select(func.coalesce(func.sum(Asset.size), 0))),
        "jobs_pending": db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running", "partial"]))),
    }


def creator_view(db: Session, creator: Creator):
    person = db.get(PlatformUser, creator.user_id)
    count = db.scalar(select(func.count(func.distinct(VideoCreator.video_id))).where(VideoCreator.creator_id == creator.id))
    return {"id": creator.id, "uid": person.uid, "name": effective(creator.alias, person.display_name),
            "source_name": person.display_name, "avatar_url": asset_url(person.avatar_asset_id),
            "description": effective(creator.description_override, person.signature), "saved_count": count,
            "tags": creator.tags, "notes": creator.notes, "alias": creator.alias,
            "description_override": creator.description_override, "source_description": person.signature}


def video_view(db: Session, video: Video, detail=False):
    note = db.scalar(select(VideoAnnotation).where(VideoAnnotation.video_id == video.id))
    people = []
    for rel in db.scalars(select(VideoCreator).where(VideoCreator.video_id == video.id).order_by(VideoCreator.role)):
        creator = db.get(Creator, rel.creator_id)
        person = db.get(PlatformUser, creator.user_id)
        people.append({"id": creator.id, "name": effective(creator.alias, person.display_name),
                       "avatar_url": asset_url(person.avatar_asset_id), "role": rel.role})
    parts = db.scalars(select(VideoPart).where(VideoPart.video_id == video.id).order_by(VideoPart.position, VideoPart.id)).all()
    variants = db.scalars(select(MediaVariant).where(MediaVariant.part_id.in_([p.id for p in parts]))).all() if parts else []
    result = {"id": video.id, "bvid": video.bvid, "title": effective(note.title_override if note else None, video.title),
              "source_title": video.title, "description": effective(note.description_override if note else None, video.description),
              "duration": video.duration, "cover_url": asset_url(video.cover_asset_id), "creators": people,
              "tags": note.tags if note else [], "starred": note.starred if note else False,
              "parts_count": len(parts), "playable": bool(variants), "capture_status": video.capture_status,
              "created_at": video.created_at}
    if detail:
        result.update(notes=note.notes if note else "", source_state=video.source_state,
                      media_properties=(video.metadata_json or {}).get("media_properties", {}),
                      title_override=note.title_override if note else None,
                      description_override=note.description_override if note else None,
                      source_description=video.description,
                      parts=[{"id": p.id, "cid": p.cid, "position": p.position, "title": p.title, "duration": p.duration,
                              "variants": [{"id": v.id, "quality": v.quality, "kind": v.kind, "width": v.width,
                                            "height": v.height, "video_codec": v.video_codec, "audio_codec": v.audio_codec}
                                           for v in variants if v.part_id == p.id]} for p in parts],
                      capture_runs=[{"id": r.id, "status": r.status, "counts": r.counts, "end_reason": r.end_reason,
                                     "started_at": r.started_at, "finished_at": r.finished_at}
                                    for r in db.scalars(select(CaptureRun).where(CaptureRun.video_id == video.id)
                                                        .order_by(CaptureRun.created_at.desc()).limit(10))])
    return result


def comment_view(db, comment: Comment):
    user = db.get(PlatformUser, comment.author_user_id) if comment.author_user_id else None
    snapshot = db.get(UserSnapshot, comment.author_snapshot_id) if comment.author_snapshot_id else None
    creator = db.scalar(select(Creator).where(Creator.user_id == user.id)) if user else None
    avatar = snapshot.avatar_asset_id if snapshot else user.avatar_asset_id if user else None
    return {"id": comment.id, "rpid": comment.rpid, "root_rpid": comment.root_rpid, "parent_rpid": comment.parent_rpid,
            "content": comment.content, "posted_at": comment.posted_at, "like_count": comment.like_count,
            "reply_count": comment.reply_count,
            "author": {"uid": user.uid if user else None,
                       "name": snapshot.display_name if snapshot else user.display_name if user else "未知作者",
                       "avatar_url": asset_url(avatar), "creator_id": creator.id if creator else None},
            "images": [asset_url(a.asset_id) for a in db.scalars(select(CommentAsset).where(CommentAsset.comment_id == comment.id)
                                                              .order_by(CommentAsset.position))]}


def collection_view(db, collection):
    return {"id": collection.id, "title": collection.title, "kind": collection.kind,
            "source_id": collection.source_id, "enabled": collection.enabled,
            "saved_count": db.scalar(select(func.count()).select_from(CollectionItem)
                                     .where(CollectionItem.collection_id == collection.id, CollectionItem.video_id.is_not(None)))}


def job_view(job):
    return {name: getattr(job, name) for name in ("id", "kind", "target_id", "account_id", "status", "policy", "checkpoint",
                                                  "result", "error", "attempts", "max_attempts", "available_at",
                                                  "created_at", "started_at", "finished_at")}


def backup_view(backup):
    return {name: getattr(backup, name) for name in ("id", "status", "manifest", "object_key", "error", "snapshot_at", "completed_at")}
