"""Bounded, resumable scheduling; processing always runs in workers."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, text, tuple_

from app.models import Job, MediaVariant, Setting, SourceAccount, Video, utcnow
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
        if not config.account_id or not db.get(SourceAccount, config.account_id):
            if manual:
                raise ValueError("请先在系统设置中选择统计更新账号")
            return _result(state, needs_account=True)
        ingest = db.get(Setting, "ingest")
        policy = IngestPolicy.model_validate(ingest.value if ingest else {}).model_dump()
        policy["refresh_danmaku"] = config.refresh_danmaku
        state = _new_batch(db, Video, Video.bvid.like("BV%"), now)
        state.update({"manual": manual, "account_id": config.account_id, "policy": policy,
                      "interval_hours": config.interval_hours, "next_available_at": now.isoformat()})

    # Never silently switch the account or policy halfway through a batch.
    if not db.get(SourceAccount, state["account_id"]):
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


def _preparation_demand(variant, config):
    package = config.package_long_videos and variant.duration >= config.min_duration_seconds
    if not package and not config.analyze_loudness:
        return None
    key = (f"prepare:v2:{variant.id}:{variant.asset_id}:{config.segment_seconds}:"
           f"{int(package)}:{int(config.analyze_loudness)}")
    return variant.id, key, {"package": package, "analyze_loudness": config.analyze_loudness,
                             "segment_seconds": config.segment_seconds}


def enqueue_variant_preparation(db, variant):
    """Prepare one explicitly created derivative; never scan/requeue history."""
    from app.jobs import enqueue
    row = db.get(Setting, "playback")
    config = PlaybackSettings.model_validate(row.value if row else {})
    demand = _preparation_demand(variant, config)
    if demand is None:
        return None
    variant_id, key, policy = demand
    return enqueue(db, "prepare_media", variant_id, policy=policy, dedupe_key=key)


def _media_page(db):
    from app.jobs import enqueue

    runtime = _runtime(db, "media_maintenance_runtime")
    state = dict(runtime.value) if runtime else {}
    now = utcnow()
    if state.get("active"):
        if state.get("next_batch_at") and _date(state["next_batch_at"]) > now:
            return _result(state, not_due=True)
    else:
        row = db.get(Setting, "playback")
        config = PlaybackSettings.model_validate(row.value if row else {})
        if not config.package_long_videos and not config.analyze_loudness:
            return _result(state, disabled=True)
        if state.get("next_run_at") and _date(state["next_run_at"]) > now:
            return _result(state, not_due=True)
        state = _new_batch(db, MediaVariant, MediaVariant.kind == "archive", now)
        state["policy"] = config.model_dump()

    config = PlaybackSettings.model_validate(state["policy"])
    rows, pending = _page(db, MediaVariant,
                          (MediaVariant.id, MediaVariant.created_at, MediaVariant.asset_id, MediaVariant.duration),
                          MediaVariant.kind == "archive", state)
    demands = []
    for variant in rows:
        demand = _preparation_demand(variant, config)
        if demand:
            demands.append(demand)
    keys = [key for _, key, _ in demands]
    existing = set(db.scalars(select(Job.dedupe_key).where(Job.dedupe_key.in_(keys)))) if keys else set()
    count = 0
    for variant_id, key, policy in demands:
        if key not in existing:
            enqueue(db, "prepare_media", variant_id, policy=policy, dedupe_key=key)
            count += 1
    _advance(state, rows, pending, count, now, timedelta(minutes=1))
    _save(db, runtime, "media_maintenance_runtime", state)
    return _result(state, count)
