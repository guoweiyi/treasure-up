"""Bounded, resumable scheduling; processing always runs in workers."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, text, tuple_

from app.models import Asset, Job, MediaVariant, Setting, SourceAccount, Video, utcnow
from app.schemas import IngestPolicy, PlaybackSettings, StatisticsSettings


BATCH_SIZE = 100
BATCH_DELAY_SECONDS = 5
_ACTIVE_STATUSES = ("queued", "running", "paused", "blocked")


def _date(value):
    date = datetime.fromisoformat(value) if isinstance(value, str) else value
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date


def _key(row):
    return {"created_at": _date(row.created_at).isoformat(), "id": row.id}


def _locked_schedule(db, lock_id, operation):
    # Manual requests and the scheduler can run in separate processes. A try-lock
    # avoids making queue dispatch wait behind another maintenance transaction.
    try:
        if db.get_bind().dialect.name == "postgresql":
            acquired = db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_id})
            if not acquired:
                db.commit()
                return {"queued": 0, "queued_now": 0, "busy": True}
        result = operation()
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def _runtime(db, key):
    # Refresh a long-lived caller's identity map after acquiring the lock.
    return db.scalar(select(Setting).where(Setting.key == key).execution_options(populate_existing=True))


def _save(db, row, key, state):
    if row:
        row.value = state
    else:
        db.add(Setting(key=key, value=state))


def _new_batch(db, model, condition, now):
    upper = db.execute(select(model.created_at, model.id).where(condition)
                       .order_by(model.created_at.desc(), model.id.desc()).limit(1)).first()
    return {"batch_id": str(uuid4()), "active": True, "started_at": now.isoformat(),
            "cursor": None, "upper_bound": _key(upper) if upper else None,
            "last_queued_at": now.isoformat(), "last_count": 0, "scanned": 0}


def _page(db, model, columns, condition, state):
    upper = state["upper_bound"]
    if not upper:
        return [], False
    position = tuple_(model.created_at, model.id)
    query = select(*columns).where(condition, position <= tuple_(_date(upper["created_at"]), upper["id"]))
    if state.get("cursor"):
        cursor = state["cursor"]
        query = query.where(position > tuple_(_date(cursor["created_at"]), cursor["id"]))
    # One lookahead identifies the end without a full-library count or an empty
    # follow-up transaction. The ID tie-breaker preserves equal timestamps.
    rows = list(db.execute(query.order_by(model.created_at, model.id).limit(BATCH_SIZE + 1)))
    return rows[:BATCH_SIZE], len(rows) > BATCH_SIZE


def _advance(state, rows, pending, count, now, next_cycle):
    if rows:
        state["cursor"] = _key(rows[-1])
    state["active"] = pending
    state["scanned"] += len(rows)
    state["last_count"] += count
    if count:
        state["last_queued_at"] = now.isoformat()
    state["next_batch_at"] = (now + timedelta(seconds=BATCH_DELAY_SECONDS)).isoformat() if pending else None
    state["next_run_at"] = None if pending else (now + next_cycle).isoformat()
    if not pending:
        state["completed_at"] = now.isoformat()


def _result(state=None, count=0, **extra):
    state = state or {}
    return {"queued": count, "queued_now": count, "queued_total": state.get("last_count", 0),
            "remaining_pending": bool(state.get("active")),
            **{key: state[key] for key in ("batch_id", "next_run_at", "next_batch_at", "last_queued_at",
                                           "last_count", "interval_hours") if key in state}, **extra}


def enqueue_statistics(db, *, manual=False):
    """Start one library refresh, or resume its next bounded page.

    Manual calls start/reuse a batch; the scheduler completes it even when the
    recurring schedule is disabled. queued remains the count added by this call.
    """
    return _locked_schedule(db, 87213002, lambda: _statistics_page(db, manual=manual))


def _statistics_page(db, *, manual):
    from app.jobs import enqueue

    runtime = _runtime(db, "statistics_runtime")
    state = dict(runtime.value) if runtime else {}
    now = utcnow()
    if state.get("active"):
        if manual:
            return _result(state, already_running=True)
        if state.get("next_batch_at") and _date(state["next_batch_at"]) > now:
            return _result(state, not_due=True)
    else:
        row = db.get(Setting, "statistics")
        config = StatisticsSettings.model_validate(row.value if row else {})
        if not manual and not config.enabled:
            return _result(state, disabled=True)
        if not manual and state.get("next_run_at") and _date(state["next_run_at"]) > now:
            return _result(state, not_due=True)
        account = db.get(SourceAccount, config.account_id) if config.account_id else db.scalar(
            select(SourceAccount).where(SourceAccount.status == "valid")
            .order_by(SourceAccount.last_verified_at.desc().nulls_last(), SourceAccount.created_at, SourceAccount.id).limit(1))
        if not account or account.status in {"invalid", "expired", "disabled"}:
            if manual:
                raise ValueError("请先在系统设置中选择统计更新账号")
            return _result(state, needs_account=True)
        ingest = db.get(Setting, "ingest")
        policy = IngestPolicy.model_validate(ingest.value if ingest else {}).model_dump()
        policy["refresh_danmaku"] = config.refresh_danmaku
        policy["refresh_comments"] = config.refresh_comments
        policy["refresh_tags"] = True
        # Include sources previously reported unavailable: a later successful
        # view check must be able to clear the badge without touching archives.
        state = _new_batch(db, Video, Video.bvid.like("BV%"), now)
        state.update({"manual": manual, "account_id": account.id, "policy": policy,
                      "interval_hours": config.interval_hours, "next_available_at": now.isoformat()})

    # Never silently switch the account or policy halfway through a batch.
    account = db.get(SourceAccount, state["account_id"])
    if not account or account.status in {"invalid", "expired", "disabled"}:
        return _result(state, needs_account=True)
    rows, pending = _page(db, Video, (Video.id, Video.created_at), Video.bvid.like("BV%"), state)
    targets = [video.id for video in rows]
    keys = {video.id: f"stats:{video.id}:{state['batch_id']}" for video in rows}
    active = set(db.scalars(select(Job.target_id).where(Job.kind == "refresh_stats", Job.target_id.in_(targets),
                                                       Job.status.in_(_ACTIVE_STATUSES)))) if targets else set()
    existing = set(db.scalars(select(Job.dedupe_key).where(Job.dedupe_key.in_(list(keys.values()))))) if keys else set()
    count = 0
    available = max(now, _date(state["next_available_at"]))
    interval = timedelta(seconds=state["policy"]["request_interval_seconds"])
    for video in rows:
        if video.id in active or keys[video.id] in existing:
            continue
        job = enqueue(db, "refresh_stats", video.id, state["account_id"], policy=state["policy"],
                      dedupe_key=keys[video.id], frozen_policy=True)
        job.available_at = available
        available += interval
        count += 1
    state["next_available_at"] = available.isoformat()
    _advance(state, rows, pending, count, now, timedelta(hours=state["interval_hours"]))
    _save(db, runtime, "statistics_runtime", state)
    return _result(state, count)


def enqueue_media_maintenance(db):
    return _locked_schedule(db, 87213003, lambda: _media_page(db))


def _playback_config(db):
    row = _runtime(db, "playback")
    return PlaybackSettings.model_validate(row.value if row else {})


def _preparation_demand(variant, config, *, size=0, has_package=False):
    package = config.package_long_videos and (
        has_package or variant.duration >= config.min_duration_seconds or size >= config.min_size_mb * 1024**2)
    if not package and not config.analyze_loudness:
        return None
    version = "v3" if package else "v2"
    key = (f"prepare:{version}:{variant.id}:{variant.asset_id}:{config.segment_seconds}:"
           f"{int(package)}:{int(config.analyze_loudness)}")
    return variant.id, key, {"package": package, "analyze_loudness": config.analyze_loudness,
                             "segment_seconds": config.segment_seconds, "preparation_request": "automatic"}


def _canonical_preparation_variant(db, variant):
    # Older compatible rows can be aliases of the same original file. Keep
    # automatic preparation within this part and process that asset only once.
    if variant.kind == "playback":
        original = db.scalar(select(MediaVariant).where(MediaVariant.part_id == variant.part_id,
            MediaVariant.asset_id == variant.asset_id, MediaVariant.kind == "archive")
            .order_by(MediaVariant.created_at, MediaVariant.id).limit(1))
        if original:
            return original
    return variant


def _variant_demand(db, variant, config):
    if not config.package_long_videos and not config.analyze_loudness:
        return None
    size = db.scalar(select(Asset.size).where(Asset.id == variant.asset_id))
    has_package = config.package_long_videos and db.scalar(select(MediaVariant.id).where(
        MediaVariant.part_id == variant.part_id, MediaVariant.kind == "hls",
        MediaVariant.metadata_json["source_asset_id"].as_string() == variant.asset_id).limit(1)) is not None
    return _preparation_demand(variant, config, size=size or 0, has_package=has_package)


def _enqueue_preparation(db, demand):
    from app.jobs import enqueue
    variant_id, base_key, policy = demand
    active = db.scalar(select(Job).where(Job.kind == "prepare_media", Job.target_id == variant_id,
                                        Job.status.in_(_ACTIVE_STATUSES)).order_by(Job.created_at, Job.id).limit(1))
    if active:
        return active, False
    key = base_key
    previous = db.scalar(select(Job).where(Job.dedupe_key == key))
    # Turning automatic preparation back on may explicitly request work that
    # was skipped while disabled. Failed/paused/cancelled tasks keep their state.
    while (previous and previous.status == "succeeded" and any(
            policy.get(action) and action in (previous.result or {}).get("skipped_actions", [])
            for action in ("package", "analyze_loudness"))):
        key = base_key + ":after:" + previous.id
        previous = db.scalar(select(Job).where(Job.dedupe_key == key))
    return enqueue(db, "prepare_media", variant_id, policy=policy, dedupe_key=key), previous is None


def enqueue_variant_preparation(db, variant):
    """Automatic preparation is opt-in; viewing and saving do not require it."""
    if variant.kind not in {"archive", "playback"}:
        return None
    config = _playback_config(db)
    if not config.package_long_videos and not config.analyze_loudness:
        return None
    variant = _canonical_preparation_variant(db, variant)
    demand = _variant_demand(db, variant, config)
    return _enqueue_preparation(db, demand)[0] if demand else None


def effective_preparation_policy(db, job):
    """Recheck current opt-ins for queued automatic work, including old queues."""
    policy = dict(job.policy or {})
    automatic = policy.get("preparation_request") == "automatic" or (
        policy.get("preparation_request") != "manual" and (job.dedupe_key or "").startswith("prepare:"))
    if not automatic:
        # Older manual endpoint jobs have random prepare_media:<uuid> keys.
        return policy
    variant = db.get(MediaVariant, job.target_id)
    config = _playback_config(db)
    demand = _variant_demand(db, variant, config) if variant and variant.kind in {"archive", "playback"} else None
    permitted = demand[2] if demand else {}
    for action in ("package", "analyze_loudness"):
        policy[action] = bool(policy.get(action) and permitted.get(action))
    return policy


def run_media_preparation(db, job, *, check_active):
    """Do not open, hash, copy or probe an asset for disabled automatic work."""
    from app.playback import analyze_variant, package_variant
    from app.playback.source import VerifiedSource
    requested = [action for action in ("package", "analyze_loudness") if (job.policy or {}).get(action)]
    policy = effective_preparation_policy(db, job)
    result = {"completed_actions": [], "skipped_actions": [action for action in requested if not policy.get(action)]}
    if not any(policy.get(action) for action in requested):
        return {**result, "preparation": "skipped", "reason": "automatic_preparation_disabled"}
    check_active()
    from app.ingest.runner import _lock
    from app.playback.tools import PlaybackError
    variant = db.get(MediaVariant, job.target_id)
    if not variant:
        raise PlaybackError("Media variant does not exist")
    # Manual demand must survive a later automatic-setting change. Its separate
    # task still shares one processing lock with automatic work for this file.
    with _lock(db, f"prepare:{variant.part_id}:{variant.asset_id}"), VerifiedSource(db, check_active=check_active) as source:
        if policy.get("analyze_loudness"):
            result["loudness"] = analyze_variant(db, job.target_id, check_active=check_active, source_provider=source)
            result["completed_actions"].append("analyze_loudness")
            # Preserve this independently complete result across packaging failures.
            db.commit()
        if policy.get("package"):
            # A settings change while analysis ran may have withdrawn the HLS opt-in.
            if effective_preparation_policy(db, job).get("package"):
                packaged = package_variant(db, job.target_id, check_active=check_active, source_provider=source,
                                           segment_seconds=policy.get("segment_seconds", 6))
                result["variant_id"] = packaged.id
                result["completed_actions"].append("package")
            else:
                result["skipped_actions"].append("package")
    result["preparation"] = "complete" if result["completed_actions"] else "skipped"
    return result


def _media_page(db):
    runtime = _runtime(db, "media_maintenance_runtime")
    state = dict(runtime.value) if runtime else {}
    now = utcnow()
    config = _playback_config(db)
    if not config.package_long_videos and not config.analyze_loudness:
        if state.get("active"):
            state.update(active=False, next_batch_at=None, stopped_at=now.isoformat(),
                         reason="automatic_preparation_disabled")
            _save(db, runtime, "media_maintenance_runtime", state)
        return _result(state, disabled=True)
    # A frozen batch must not keep adding work after its automatic opt-in changes.
    if state.get("policy") != config.model_dump():
        state = {}
    if state.get("active"):
        if state.get("next_batch_at") and _date(state["next_batch_at"]) > now:
            return _result(state, not_due=True)
    else:
        if state.get("next_run_at") and _date(state["next_run_at"]) > now:
            return _result(state, not_due=True)
        state = _new_batch(db, MediaVariant, MediaVariant.kind.in_(("archive", "playback")), now)
        state["policy"] = config.model_dump()
    rows, pending = _page(db, MediaVariant,
        (MediaVariant.id, MediaVariant.created_at, MediaVariant.part_id, MediaVariant.asset_id,
         MediaVariant.duration, MediaVariant.kind), MediaVariant.kind.in_(("archive", "playback")), state)
    # Batch reads stay bounded by this page, including legacy aliases. Do not
    # walk every historical task for each variant in a full-library schedule.
    asset_ids, part_ids = {row.asset_id for row in rows}, {row.part_id for row in rows}
    originals = {}
    for row in db.execute(select(MediaVariant.id, MediaVariant.part_id, MediaVariant.asset_id,
            MediaVariant.duration, MediaVariant.kind).where(MediaVariant.kind == "archive",
            MediaVariant.part_id.in_(part_ids), MediaVariant.asset_id.in_(asset_ids))
            .order_by(MediaVariant.created_at, MediaVariant.id)):
        originals.setdefault((row.part_id, row.asset_id), row)
    canonical = {}
    for row in rows:
        variant = originals.get((row.part_id, row.asset_id), row) if row.kind == "playback" else row
        canonical[variant.id] = variant
    sizes = dict(db.execute(select(Asset.id, Asset.size).where(Asset.id.in_(asset_ids))).all()) if rows else {}
    source_asset = MediaVariant.metadata_json["source_asset_id"].as_string()
    packaged = set(db.execute(select(MediaVariant.part_id, source_asset).where(
        MediaVariant.part_id.in_(part_ids), MediaVariant.kind == "hls",
        source_asset.in_(asset_ids)).distinct()).all()) if rows and config.package_long_videos else set()
    demands = [demand for variant in canonical.values() if (demand := _preparation_demand(variant, config,
        size=sizes.get(variant.asset_id, 0), has_package=(variant.part_id, variant.asset_id) in packaged))]
    keys, targets = [key for _, key, _ in demands], [identifier for identifier, _, _ in demands]
    existing = {job.dedupe_key: job for job in db.scalars(select(Job).where(Job.dedupe_key.in_(keys)))} if keys else {}
    active = set(db.scalars(select(Job.target_id).where(Job.kind == "prepare_media", Job.target_id.in_(targets),
        Job.status.in_(_ACTIVE_STATUSES)).distinct())) if targets else set()
    count = 0
    for identifier, key, policy in demands:
        if identifier in active:
            continue
        previous = existing.get(key)
        if previous is None:
            from app.jobs import enqueue
            enqueue(db, "prepare_media", identifier, policy=policy, dedupe_key=key)
            count += 1
        elif previous.status == "succeeded" and any(policy.get(action) and action in
                (previous.result or {}).get("skipped_actions", []) for action in ("package", "analyze_loudness")):
            _, created = _enqueue_preparation(db, (identifier, key, policy))
            count += created
    _advance(state, rows, pending, count, now, timedelta(minutes=1))
    _save(db, runtime, "media_maintenance_runtime", state)
    return _result(state, count)
