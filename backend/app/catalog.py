from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (Asset, CaptureRun, Collection, CollectionItem, Comment, CommentAsset, Creator,
                        Job, MediaVariant, PlatformUser, SourceSubscription, UserSnapshot, Video, VideoAnnotation,
                        VideoCreator, VideoPart, VideoStatSnapshot, VideoStar, StorageProfile, SourceAccount)


def asset_url(asset_id):
    return f"/api/v1/assets/{asset_id}" if asset_id else None


def effective(override, original):
    return original if override is None else override


def personalize_videos(db, items, identity):
    ids = [item["id"] for item in items]
    stars = set(db.scalars(select(VideoStar.video_id).where(VideoStar.user_id == identity[0].id,
                                                          VideoStar.video_id.in_(ids)))) if identity and ids else set()
    editor = bool(identity and identity[0].role in {"admin", "editor"})
    for item in items:
        if editor:
            item["annotation_starred"] = item["starred"]
        item["starred"] = item["id"] in stars
        if not editor:
            for field in ("notes", "capture_runs"):
                item.pop(field, None)


def page(db, statement, page=1, page_size=24, mapper=None, batch_mapper=None):
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    items = db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    mapped = batch_mapper(db, items) if batch_mapper else [mapper(v) for v in items] if mapper else items
    return {"items": mapped, "total": total, "page": page, "page_size": page_size}


def stats(db: Session):
    return {
        "videos": db.scalar(select(func.count()).select_from(Video)),
        "creators": db.scalar(select(func.count()).select_from(Creator)),
        "collections": db.scalar(select(func.count()).select_from(Collection)),
        "assets_bytes": db.scalar(select(func.coalesce(func.sum(Asset.size), 0))),
        "jobs_pending": db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running", "partial"]))),
    }


def creator_view(db: Session, creator: Creator):
    return creator_views(db, [creator])[0]


def creator_views(db: Session, rows):
    if not rows:
        return []
    people = {person.id: person for person in db.scalars(select(PlatformUser)
              .where(PlatformUser.id.in_({creator.user_id for creator in rows})))}
    counts = dict(db.execute(select(VideoCreator.creator_id, func.count(func.distinct(VideoCreator.video_id)))
                            .where(VideoCreator.creator_id.in_([creator.id for creator in rows]))
                            .group_by(VideoCreator.creator_id)).all())
    return [_creator_view(creator, people[creator.user_id], counts.get(creator.id, 0)) for creator in rows]


def _creator_view(creator, person, count):
    return {"id": creator.id, "uid": person.uid, "name": effective(creator.alias, person.display_name),
            "source_name": person.display_name, "avatar_url": asset_url(person.avatar_asset_id),
            "description": effective(creator.description_override, person.signature), "saved_count": count,
            "tags": creator.tags, "notes": creator.notes, "alias": creator.alias,
            "description_override": creator.description_override, "source_description": person.signature}


def video_view(db: Session, video: Video, detail=False):
    return video_views(db, [video], detail=detail)[0]


def video_card_views(db: Session, rows):
    return video_views(db, rows, card=True)


def video_views(db: Session, rows, *, detail=False, card=False):
    if not rows:
        return []
    ids = [video.id for video in rows]
    notes = {note.video_id: note for note in db.scalars(select(VideoAnnotation).where(VideoAnnotation.video_id.in_(ids)))}
    people = defaultdict(list)
    for rel, creator, person in db.execute(select(VideoCreator, Creator, PlatformUser)
            .join(Creator, Creator.id == VideoCreator.creator_id).join(PlatformUser, PlatformUser.id == Creator.user_id)
            .where(VideoCreator.video_id.in_(ids)).order_by(VideoCreator.role)):
        people[rel.video_id].append({"id": creator.id, "name": effective(creator.alias, person.display_name),
                                    "avatar_url": asset_url(person.avatar_asset_id), "role": rel.role, "role_title": rel.role_title})
    parts = defaultdict(list)
    part_videos = {}
    for part in db.scalars(select(VideoPart).where(VideoPart.video_id.in_(ids)).order_by(VideoPart.position, VideoPart.id)):
        parts[part.video_id].append(part)
        part_videos[part.id] = part.video_id
    variants = defaultdict(list)
    if part_videos:
        # Bind only page video IDs, not every part ID in potentially large multi-P archives.
        for variant in db.scalars(select(MediaVariant).join(VideoPart, VideoPart.id == MediaVariant.part_id)
                                  .where(VideoPart.video_id.in_(ids))):
            if variant.part_id in part_videos:
                variants[part_videos[variant.part_id]].append(variant)
    ranked_stats = (select(VideoStatSnapshot.id, func.row_number().over(partition_by=VideoStatSnapshot.video_id,
                           order_by=(VideoStatSnapshot.observed_at.desc(), VideoStatSnapshot.id.desc())).label("rank"))
                    .where(VideoStatSnapshot.video_id.in_(ids)).subquery())
    latest = {snapshot.video_id: snapshot for snapshot in db.scalars(select(VideoStatSnapshot)
              .join(ranked_stats, ranked_stats.c.id == VideoStatSnapshot.id).where(ranked_stats.c.rank == 1))}
    from app.capture_state import latest_capture_jobs, project_capture
    jobs = latest_capture_jobs(db, rows)
    return [_video_view(db, video, notes.get(video.id), people[video.id], parts[video.id], variants[video.id],
                        latest.get(video.id), detail=detail, card=card,
                        capture=project_capture(video, parts[video.id], variants[video.id], jobs.get(video.id, {}))) for video in rows]


def _video_view(db, video, note, people, parts, variants, latest_stats, *, detail=False, card=False, capture=None):
    result = {"id": video.id, "bvid": video.bvid, "title": effective(note.title_override if note else None, video.title),
              "source_title": video.title, "description": effective(note.description_override if note else None, video.description),
              "duration": video.duration, "cover_url": asset_url(video.cover_asset_id), "creators": people,
              "tags": note.tags if note else [], "starred": note.starred if note else False,
              "parts_count": len(parts), "playable": bool(variants), "capture_status": video.capture_status,
              "created_at": video.created_at}
    result["stats"] = {**{key: None for key in ("view", "like", "coin", "favorite", "share", "reply", "danmaku")},
                       **(latest_stats.counts if latest_stats else {}),
                       "observed_at": latest_stats.observed_at if latest_stats else None}
    result["published_at"] = video.published_at
    result["source_quality"] = (video.metadata_json or {}).get("source_quality")
    result["ingest_state"] = (video.metadata_json or {}).get("ingest_state", {})
    if capture is not None:
        result["capture_status"], result["ingest_state"] = capture
    properties = dict((video.metadata_json or {}).get("media_properties", {}))
    for variant in variants:
        properties[variant.id] = {**properties.get(variant.id, {}), **(variant.metadata_json or {})}
    result["media_properties"] = properties
    access = (video.metadata_json or {}).get("access")
    charging = access.get("upower_exclusive") if isinstance(access, dict) else None
    result["content_features"] = {
        "charging_exclusive": charging if type(charging) is bool else (video.metadata_json or {}).get("is_upower_exclusive") is True,
        "dolby_vision": any(item.get("dolby_vision") is True for item in properties.values() if isinstance(item, dict)),
        "dolby_atmos": any(item.get("dolby_atmos") is True for item in properties.values() if isinstance(item, dict)),
    }
    if card and not detail:
        # Lists need status and feature badges, not every codec, hash and source
        # format for every part. Keep the existing full API available to clients.
        for field in ("source_title", "source_quality", "media_properties"):
            result.pop(field, None)
        result["description"] = ""
    if detail:
        result.update(notes=note.notes if note else "", source_state=video.source_state,
                      title_override=note.title_override if note else None,
                      description_override=note.description_override if note else None,
                      source_description=video.description,
                      parts=[{"id": p.id, "cid": p.cid, "position": p.position, "title": p.title, "duration": p.duration,
                              "variants": [{"id": v.id, "quality": v.quality, "kind": v.kind, "width": v.width,
                                            "height": v.height, "video_codec": v.video_codec, "audio_codec": v.audio_codec,
                                            "metadata": properties.get(v.id, {})}
                                           for v in variants if v.part_id == p.id]} for p in parts],
                      capture_runs=[{"id": r.id, "status": r.status, "counts": r.counts, "end_reason": r.end_reason,
                                     "started_at": r.started_at, "finished_at": r.finished_at}
                                    for r in db.scalars(select(CaptureRun).where(CaptureRun.video_id == video.id)
                                                        .order_by(CaptureRun.created_at.desc()).limit(10))])
    return result


def comment_view(db, comment: Comment):
    return comment_views(db, [comment])[0]


def comment_views(db, rows):
    if not rows:
        return []
    users = {user.id: user for user in db.scalars(select(PlatformUser)
             .where(PlatformUser.id.in_({comment.author_user_id for comment in rows if comment.author_user_id})))}
    snapshots = {snapshot.id: snapshot for snapshot in db.scalars(select(UserSnapshot)
                 .where(UserSnapshot.id.in_({comment.author_snapshot_id for comment in rows if comment.author_snapshot_id})))}
    creators = {creator.user_id: creator for creator in db.scalars(select(Creator).where(Creator.user_id.in_(list(users))))}
    images, emotes = defaultdict(list), defaultdict(list)
    for image in db.scalars(select(CommentAsset).where(CommentAsset.comment_id.in_([comment.id for comment in rows]))
                           .order_by(CommentAsset.position)):
        if image.kind in {"image", "attachment"}:
            images[image.comment_id].append(asset_url(image.asset_id))
        elif image.kind == "emote":
            emotes[image.comment_id].append(image.asset_id)
    return [_comment_view(comment, users.get(comment.author_user_id), snapshots.get(comment.author_snapshot_id),
                          creators.get(comment.author_user_id), images[comment.id], emotes[comment.id]) for comment in rows]


def _comment_view(comment, user, snapshot, creator, images, emote_ids=()):
    avatar = snapshot.avatar_asset_id if snapshot else user.avatar_asset_id if user else None
    allowed, inline, seen = set(emote_ids), [], set()
    manifest = (comment.raw or {}).get("asset_manifest", [])
    if isinstance((comment.raw or {}).get("asset_manifest"), list):
        linked = set(images)
        images = [asset_url(entry["asset_id"]) for entry in manifest
                  if isinstance(entry, dict) and entry.get("purpose") == "attachment"
                  and isinstance(entry.get("asset_id"), str) and asset_url(entry["asset_id"]) in linked]
    images = list(dict.fromkeys(images))
    for entry in manifest if isinstance(manifest, list) else []:
        if not isinstance(entry, dict):
            continue
        token = entry.get("token")
        if (entry.get("purpose") == "emote" and entry.get("asset_id") in allowed
                and isinstance(token, str) and 0 < len(token) <= 100 and token not in seen):
            inline.append({"text": token, "asset_url": asset_url(entry["asset_id"])})
            seen.add(token)
    return {"id": comment.id, "rpid": comment.rpid, "root_rpid": comment.root_rpid, "parent_rpid": comment.parent_rpid,
            "content": comment.content, "posted_at": comment.posted_at, "like_count": comment.like_count,
            "reply_count": comment.reply_count,
            "author": {"uid": user.uid if user else None,
                       "name": snapshot.display_name if snapshot else user.display_name if user else "未知作者",
                       "avatar_url": asset_url(avatar), "creator_id": creator.id if creator else None},
            "images": images, "emotes": inline}


def collection_view(db, collection):
    return collection_views(db, [collection])[0]


def collection_views(db, rows):
    if not rows:
        return []
    ids = [collection.id for collection in rows]
    subscriptions = {subscription.collection_id: subscription for subscription in db.scalars(select(SourceSubscription)
                     .where(SourceSubscription.collection_id.in_(ids)))}
    counts = {identifier: (count, cover) for identifier, count, cover in db.execute(select(CollectionItem.collection_id, func.count(), func.min(Video.cover_asset_id))
                            .outerjoin(Video, Video.id == CollectionItem.video_id)
                            .where(CollectionItem.collection_id.in_(ids), CollectionItem.video_id.is_not(None))
                            .group_by(CollectionItem.collection_id))}
    return [_collection_view(collection, subscriptions.get(collection.id), *counts.get(collection.id, (0, None))) for collection in rows]


def _collection_view(collection, subscription, count, cover=None):
    from app.source_labels import display_source_title
    return {"id": collection.id, "title": display_source_title(collection), "kind": collection.kind,
            "source_id": collection.source_id, "enabled": subscription.enabled if subscription else collection.enabled,
            "subscribed": subscription is not None, "next_run_at": subscription.next_run_at if subscription else None,
            "monitor": collection.monitor_state or {}, "last_scan_at": collection.last_scan_at,
            "saved_count": count, "cover_url": asset_url(cover)}


def job_view(job):
    return {name: getattr(job, name) for name in ("id", "kind", "target_id", "account_id", "status", "policy", "checkpoint",
                                                  "result", "error", "attempts", "max_attempts", "available_at",
                                                  "created_at", "started_at", "finished_at")}


def job_views(db, rows):
    if not rows:
        return []
    ids = [row.target_id for row in rows]
    videos = {target: video.title for video in db.scalars(select(Video).where((Video.id.in_(ids)) | (Video.bvid.in_(ids))))
              for target in (video.id, video.bvid)}
    variants = dict(db.execute(select(MediaVariant.id, Video.title).join(VideoPart, VideoPart.id == MediaVariant.part_id)
                               .join(Video, Video.id == VideoPart.video_id).where(MediaVariant.id.in_(ids))).all())
    from app.source_labels import display_source_title
    collections = {row.id: display_source_title(row) for row in db.scalars(select(Collection).where(Collection.id.in_(ids)))}
    profiles = dict(db.execute(select(StorageProfile.id, StorageProfile.name).where(StorageProfile.id.in_(ids))).all())
    accounts = dict(db.execute(select(SourceAccount.id, SourceAccount.name).where(SourceAccount.id.in_(ids))).all())
    names = {**videos, **variants, **collections, **profiles, **accounts}
    from app.capture_state import job_capture_summaries
    captures = job_capture_summaries(db, rows)
    return [{**job_view(row), "target_title": names.get(row.target_id, "归档任务"),
             **({"capture": captures[row.id]} if row.id in captures else {})} for row in rows]


def backup_view(backup):
    return {name: getattr(backup, name) for name in ("id", "status", "manifest", "object_key", "error", "snapshot_at", "completed_at")}
