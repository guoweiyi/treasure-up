"""Persistent source observation, separate from media capture and deletion.

Offset pagination cannot produce a server-side snapshot. Full scans recheck the
head and advertised total, while uncertain scans never mark old items absent.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from app.ingest.errors import IngestError
from app.models import Collection, CollectionItem, SourceSubscription, Video, VideoCreator


COUNTS = ("observed", "new_items", "new_videos", "newly_published", "newly_favorited",
          "queued", "reused", "skipped", "not_observed")


def _date(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (TypeError, ValueError, AttributeError):
        return None


def _timestamp(value):
    try:
        number = float(value)
        return datetime.fromtimestamp(number, timezone.utc).isoformat() if number > 0 else None
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _number(value, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdecimal():
        raise IngestError("来源分页字段无效", code="invalid_pagination")
    result = int(value)
    if not minimum <= result <= 10_000_000:
        raise IngestError("来源分页字段超出范围", code="invalid_pagination")
    return result


def _source_id(value):
    result = str(value or "")
    if not result.isdecimal() or len(result) > 32 or int(result) == 0:
        raise IngestError("来源资源标识无效", code="invalid_response")
    return result


def _fetch(ctx, collection, page):
    size = 20 if collection.kind == "favorite" else 30
    if collection.kind == "favorite":
        data = ctx.client.favorite_page(collection.source_id, page)
        if (not isinstance(data, dict) or not isinstance(data.get("has_more"), (bool, int))
                or data["has_more"] not in (True, False, 0, 1)):
            raise IngestError("收藏分页缺少合法结束标记", code="invalid_pagination")
        more = bool(data["has_more"])
        info = data.get("info") or {}
        if not isinstance(info, dict):
            raise IngestError("收藏来源信息无效", code="invalid_pagination")
        total = _number(info["media_count"]) if info.get("media_count") is not None else None
        owner = info.get("upper") or {}
        if isinstance(owner, dict) and owner.get("mid"):
            collection.owner_uid = _source_id(owner["mid"])
        if info.get("title"):
            collection.monitor_state = {**(collection.monitor_state or {}), "source_title": str(info["title"])}
        items = data.get("medias")
        if items is None and not more and (total in (None, 0)):
            items = []
    else:
        data = ctx.client.creator_page(collection.source_id, page)
        if not isinstance(data, dict) or not isinstance(data.get("page"), dict) or not isinstance(data.get("list"), dict):
            raise IngestError("UP 投稿分页结构无效", code="invalid_pagination")
        info = data["page"]
        total = _number(info.get("count"))
        if _number(info.get("pn"), minimum=1) != page or _number(info.get("ps"), minimum=1) != size:
            raise IngestError("UP 投稿返回了错误的页码或页大小", code="invalid_pagination")
        more = page * size < total
        items = data["list"].get("vlist")
    if not isinstance(items, list) or len(items) > size or any(not isinstance(row, dict) for row in items):
        raise IngestError("来源列表内容无效", code="invalid_pagination")
    if not items and (more or (total is not None and total > 0)):
        raise IngestError("来源分页提前返回空页", code="invalid_pagination")
    identities = []
    for row in items:
        identity = _source_id(row.get("id") if collection.kind == "favorite" else row.get("aid"))
        kind = _number(row.get("type", 2)) if collection.kind == "favorite" else 2
        identities.append(f"{kind}:{identity}")
    if len(set(identities)) != len(identities):
        raise IngestError("来源同页包含重复资源", code="invalid_pagination")
    fingerprint = hashlib.sha256(json.dumps([(identity, row.get("bvid"), row.get("fav_time"), row.get("created"))
        for identity, row in zip(identities, items)], separators=(",", ":")).encode()).hexdigest()
    return items, identities, more, total, fingerprint, size


def _publish(ctx, collection, scan, *, status="running", reason=None):
    state = dict(collection.monitor_state or {})
    # Persist the monitoring start before the first request, including scans
    # which later fail or finish unstable and cannot complete their baseline.
    if _date(state.get("baseline_started_at")) is None:
        state["baseline_started_at"] = scan["baseline_started_at"]
    state.update(version=1, status=status, scan_mode=scan["mode"], run_id=ctx.run.id,
                 last_started_at=scan["started_at"], next_page=scan["page"], counts=dict(scan["counts"]),
                 end_reason=reason)
    collection.monitor_state = state
    ctx.run.counts = {**scan["counts"], "pages": scan["page"] - 1}


def scan_error(ctx, error):
    """Expose a safe failure reason without discarding the last full scan time."""
    scan = ctx.cp.get("source_scan")
    collection = ctx.db.get(Collection, ctx.job.target_id) if scan else None
    if collection:
        _publish(ctx, collection, scan, status="blocked" if error.blocked else "partial", reason=error.code)


def _initial_scan(ctx, collection, now):
    state = collection.monitor_state or {}
    baseline_started = _date(state.get("baseline_started_at"))
    if baseline_started is None:
        # "From now" starts when the subscription is saved, not when a queued
        # collector eventually acquires its account/source locks. Direct scan
        # jobs without a subscription start when their first scan begins.
        subscribed_at = ctx.db.scalar(select(SourceSubscription.created_at).where(
            SourceSubscription.collection_id == collection.id))
        baseline_started = _date(subscribed_at) or now
    initial = not state.get("baseline_completed_at")
    last_full = _date(state.get("last_full_scan_at"))
    full_due = last_full is None or last_full + timedelta(hours=ctx.policy.get("full_scan_interval_hours", 24)) <= now
    mode = "initial" if initial else "full" if full_due or ctx.policy.get("force_full_scan") else "incremental"
    scan = {"version": 1, "mode": mode, "started_at": now.isoformat(), "page": 1, "phase": "pages",
            "counts": {key: 0 for key in COUNTS}, "fingerprints": [], "first_fingerprint": None,
            "expected_total": None, "count_changed": False, "duplicates": False, "initial_selected": 0,
            "initial_strategy": ctx.policy.get("initial_strategy", "all"),
            "baseline_started_at": baseline_started.isoformat()}
    ctx.cp["source_scan"] = scan
    return scan


def _observe(ctx, collection, scan, raw, identity, position, now, enqueue_archive):
    item = ctx.db.scalar(select(CollectionItem).where(CollectionItem.collection_id == collection.id,
                                                     CollectionItem.source_resource_id == identity))
    new_item = item is None
    if new_item:
        item = CollectionItem(collection_id=collection.id, source_resource_id=identity)
        ctx.db.add(item)
    observation = dict(item.observation or {})
    if item.seen_run_id == ctx.run.id:
        scan["duplicates"] = True
        return
    scan["counts"]["observed"] += 1
    if new_item:
        scan["counts"]["new_items"] += 1
    published = _timestamp(raw.get("pubtime") if collection.kind == "favorite" else raw.get("created"))
    favorited = _timestamp(raw.get("fav_time")) if collection.kind == "favorite" else None
    event_time = favorited if collection.kind == "favorite" else published
    baseline = _date(scan["baseline_started_at"])
    event_date = _date(event_time)
    # Keep the greatest known source event independently of the latest response:
    # an omitted timestamp or a stale page must not make the same event new again.
    # Existing observations predate this watermark, so retain their valid time.
    previous_times = [value for value in (
        _date(observation.get("event_time_watermark")),
        _date(observation.get("favorited_at" if collection.kind == "favorite" else "published_at")),
    ) if value is not None]
    old_event = max(previous_times, default=None)
    after_baseline = event_date is not None and event_date > baseline
    new_event = after_baseline and (new_item or old_event is None or event_date > old_event)
    if new_event:
        scan["counts"]["newly_favorited" if collection.kind == "favorite" else "newly_published"] += 1
    watermark = max(previous_times + ([event_date] if event_date is not None else []), default=None)
    if watermark is not None:
        observation["event_time_watermark"] = watermark.isoformat()
    observation.update(first_seen_at=observation.get("first_seen_at") or now.isoformat(), last_seen_at=now.isoformat(),
                       first_run_id=observation.get("first_run_id") or ctx.run.id,
                       published_at=published, favorited_at=favorited)
    item.position, item.seen_run_id = position, ctx.run.id
    bvid = raw.get("bvid")
    if not identity.startswith("2:") or not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid or ""):
        item.source_state = "unavailable" if identity.startswith("2:") else "unsupported"
        observation["archive_decision"] = item.source_state
        scan["counts"]["skipped"] += 1
        item.observation = observation
        return
    # attr==4 can still be playable. Listing hints never override actual playback
    # authorization or make us delete an existing media archive.
    item.source_state = "available"
    video = ctx.db.scalar(select(Video).where(Video.bvid == bvid))
    if video is None:
        video = Video(bvid=bvid, aid=identity.split(":", 1)[1], title=str(raw.get("title") or ""),
                      published_at=_date(published), capture_status="pending")
        ctx.db.add(video)
        ctx.db.flush()
        scan["counts"]["new_videos"] += 1
    item.video_id = video.id
    if scan.get("creator_id") and not ctx.db.scalar(select(VideoCreator.id).where(VideoCreator.video_id == video.id,
                VideoCreator.creator_id == scan["creator_id"], VideoCreator.role == "owner")):
        ctx.db.add(VideoCreator(video_id=video.id, creator_id=scan["creator_id"], role="owner"))

    decision = observation.get("archive_decision", "existing")
    eligible = new_item or new_event or observation.get("archive_decision") in (None, "queued", "active", "retry_pending")
    if scan["initial_strategy"] == "new_only":
        if not after_baseline:
            eligible = False
            decision = ("unknown_source_time" if not event_time else
                        "initial_baseline" if scan["mode"] == "initial" else "before_monitoring")
        elif decision in ("initial_baseline", "before_monitoring", "unknown_source_time"):
            # Also recover items skipped by an earlier initial scan, without
            # counting an unchanged, previously observed event a second time.
            eligible = True
    elif scan["mode"] == "initial" and scan["initial_strategy"] == "latest":
        if scan["initial_selected"] >= ctx.policy.get("initial_limit", 100):
            eligible, decision = False, "initial_limit"
        else:
            scan["initial_selected"] += 1
    if not ctx.policy.get("archive", True):
        eligible, decision = False, "scan_only"
    if eligible:
        decision = enqueue_archive(ctx, video)
    observation["archive_decision"] = decision
    item.observation = observation
    scan["counts"]["queued" if decision == "queued" else "reused" if decision == "reused" else "skipped"] += 1


def _finish(ctx, collection, scan, now, *, stable, full):
    status = "complete" if full and stable else "unstable" if full else "window_complete"
    reason = "verified_visible_end" if full and stable else "source_changed_during_scan" if full else "incremental_window"
    if full and stable:
        changed = ctx.db.execute(update(CollectionItem).where(CollectionItem.collection_id == collection.id,
            or_(CollectionItem.seen_run_id.is_(None), CollectionItem.seen_run_id != ctx.run.id),
            CollectionItem.source_state != "not_observed").values(source_state="not_observed"))
        scan["counts"]["not_observed"] = changed.rowcount
    _publish(ctx, collection, scan, status=status, reason=reason)
    state = dict(collection.monitor_state)
    state["last_completed_at"] = now.isoformat()
    if full and stable:
        state["last_full_scan_at"] = now.isoformat()
        state.setdefault("baseline_completed_at", now.isoformat())
        state.setdefault("baseline_started_at", scan["baseline_started_at"])
    collection.monitor_state = state
    collection.last_scan_at = now
    scan.update(phase="done", end_reason=reason, status=status)
    ctx.cp["collection_done"] = True
    ctx.save()
    return True


def scan_source(ctx, *, user_snapshot, enqueue_archive, now):
    collection = ctx.db.get(Collection, ctx.job.target_id)
    if not collection or collection.kind not in ("favorite", "creator"):
        raise IngestError("视频来源不存在或类型未支持", code="invalid_source", retryable=False)
    ctx.run.collection_id = collection.id
    if ctx.cp.get("collection_done"):
        return True
    scan = ctx.cp.get("source_scan") or _initial_scan(ctx, collection, now())
    _publish(ctx, collection, scan)
    ctx.save()
    if collection.kind == "creator" and not scan.get("profile_done"):
        if not ctx.request_slot():
            return False
        profile = ctx.client.profile(collection.source_id)
        if not isinstance(profile, dict) or str(profile.get("mid")) != collection.source_id:
            raise IngestError("UP 资料与订阅 UID 不一致", code="invalid_metadata")
        _, _, creator = user_snapshot(ctx, profile, creator=True)
        scan["creator_id"], scan["profile_done"] = creator.id, True
        collection.owner_uid = collection.source_id
        ctx.save()
    while ctx.request_slot():
        page = scan["page"]
        if page > 10000:
            raise IngestError("来源分页超过保护上限", code="pagination_limit", retryable=False)
        if scan["phase"] == "verify":
            _, _, _, total, fingerprint, _ = _fetch(ctx, collection, 1)
            stable = (fingerprint == scan["first_fingerprint"] and total == scan["expected_total"]
                      and not scan["count_changed"] and not scan["duplicates"]
                      and (total is None or scan["counts"]["observed"] == total))
            return _finish(ctx, collection, scan, now(), stable=stable, full=True)
        items, identities, more, total, fingerprint, size = _fetch(ctx, collection, page)
        if items and fingerprint in scan["fingerprints"]:
            raise IngestError("来源分页重复，已保留检查点", code="pagination_loop")
        if page == 1:
            scan["first_fingerprint"], scan["expected_total"] = fingerprint, total
        elif total != scan["expected_total"]:
            scan["count_changed"] = True
        for index, (raw, identity) in enumerate(zip(items, identities)):
            _observe(ctx, collection, scan, raw, identity, (page - 1) * size + index, now(), enqueue_archive)
        scan["fingerprints"].append(fingerprint)
        scan["page"] = page + 1
        if not more:
            scan["phase"] = "verify"
        elif scan["mode"] == "incremental" and page >= ctx.policy.get("incremental_pages", 3):
            return _finish(ctx, collection, scan, now(), stable=False, full=False)
        _publish(ctx, collection, scan)
        ctx.save()
    _publish(ctx, collection, scan, status="partial", reason="page_budget")
    ctx.save()
    return False
