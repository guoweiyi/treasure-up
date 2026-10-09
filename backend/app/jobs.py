"""Database task ledger. Redis messages are disposable delivery hints."""
import threading
import hashlib
import re
from datetime import timedelta
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import event, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Job, JobAttempt, OutboxEvent, Setting, SourceSubscription, Video, utcnow


class LeaseLost(RuntimeError):
    """The worker must stop without publishing any more database mutations."""


def _owned(job_id, owner, now=None):
    return (Job.id == job_id, Job.lease_owner == owner, Job.status == "running",
            Job.lease_expires_at > (now if now is not None else utcnow()))


def _lease_clock(db, fallback=None):
    # CURRENT_TIMESTAMP is the transaction start in PostgreSQL; use the live clock
    # so a long-running transaction cannot commit against an already expired lease.
    return func.clock_timestamp() if db.get_bind().dialect.name == "postgresql" else (fallback or utcnow())


def ensure_active(db, job_id, owner):
    """Fresh ownership check; identity-map state is not lease evidence."""
    if not owner:
        raise LeaseLost("Task lease is no longer owned")
    with db.no_autoflush:
        row = db.execute(select(Job.checkpoint).where(*_owned(job_id, owner, _lease_clock(db)))).first()
    if row is None:
        raise LeaseLost("Task lease expired, changed, or was cancelled")
    return deepcopy(row[0] or {})


def fence_transaction(db, job_id, owner, *, checkpoint=None):
    """Acquire a job-row lock with a CAS immediately before transaction commit.

    Holding this lock over network work would block the independent heartbeat, so
    it is intentionally acquired only at the commit/checkpoint boundary.
    """
    if not owner:
        raise LeaseLost("Task lease is no longer owned")
    values = {"lease_expires_at": Job.lease_expires_at}
    if checkpoint is not None:
        values["checkpoint"] = deepcopy(checkpoint)
    with db.no_autoflush:
        result = db.execute(update(Job).where(*_owned(job_id, owner, _lease_clock(db))).values(**values)
                            .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise LeaseLost("Task lease expired, changed, or was cancelled")


def save_checkpoint(db, job_id, owner, checkpoint):
    try:
        fence_transaction(db, job_id, owner, checkpoint=checkpoint)
        db.commit()
    except Exception:
        db.rollback()
        raise


def renew_lease(db, job_id, owner):
    now = utcnow()
    clock = _lease_clock(db, now)
    changed = db.execute(update(Job).where(*_owned(job_id, owner, clock))
                         .values(lease_expires_at=clock + timedelta(seconds=settings.lease_seconds))
                         .execution_options(synchronize_session=False))
    db.commit()
    return changed.rowcount == 1


def enqueue(db: Session, kind: str, target_id: str, account_id=None, policy=None, dedupe_key=None, *, frozen_policy=False) -> Job:
    key = dedupe_key or f"{kind}:{uuid4()}"
    existing = db.scalar(select(Job).where(Job.dedupe_key == key))
    if existing:
        return existing
    if kind == "archive_video":
        bvid = target_id if re.fullmatch(r"BV[A-Za-z0-9]{10}", target_id) else db.scalar(select(Video.bvid).where(Video.id == target_id))
        if bvid:
            if db.get_bind().dialect.name == "postgresql":
                lock = int.from_bytes(hashlib.sha256(("archive-submit:" + bvid).encode()).digest()[:8], "big", signed=True)
                # A source scan can enqueue many BVs in source order. Never wait
                # while holding its earlier keys against an ordered script batch.
                if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock}):
                    from app.ingest.errors import IngestDeferred
                    raise IngestDeferred("该视频正在被另一请求提交，请稍后重试", code="enqueue_busy", retry_after_seconds=1)
            # A source scan may have created the catalog row while we acquired
            # the lock. Normalize only after re-reading that mapping.
            video_id = db.scalar(select(Video.id).where(Video.bvid == bvid))
            targets = [bvid] + ([video_id] if video_id else [])
            active = db.scalar(select(Job).where(Job.kind.in_(["archive_video", "download_media"]),
                Job.target_id.in_(targets), Job.status.in_(["queued", "running", "paused", "blocked"]),
                Job.checkpoint["deleted_by"].as_string().is_(None)).order_by(Job.created_at.desc(), Job.id).limit(1))
            if active:
                return active
            target_id = video_id or bvid
    frozen = deepcopy(policy or {})
    if kind in {"scan_collection", "archive_video", "refresh_comments", "refresh_stats", "verify_account"} and not frozen_policy:
        from app.schemas import IngestPolicy
        system = db.get(Setting, "ingest")
        frozen = {**IngestPolicy().model_dump(), **(system.value or {} if system else {}), **frozen}
        # Validate public fields, while preserving trusted internal task options.
        public = IngestPolicy.model_validate({key: value for key, value in frozen.items() if key in IngestPolicy.model_fields})
        frozen.update(public.model_dump())
    job = Job(kind=kind, target_id=target_id, account_id=account_id, policy=frozen, dedupe_key=key)
    db.add(job)
    db.flush()
    db.add(OutboxEvent(job_id=job.id))
    db.flush()
    return job


def claim(db: Session, job_id: str, owner: str, *, attempt=None):
    now = utcnow()
    clock = _lease_clock(db, now)
    result = db.execute(update(Job).where(Job.id == job_id, Job.status == "queued", Job.available_at <= clock)
                        .values(status="running", lease_owner=owner, lease_expires_at=clock + timedelta(seconds=settings.lease_seconds),
                                started_at=clock, attempts=Job.attempts + 1, error=None)
                        .execution_options(synchronize_session=False))
    if result.rowcount == 1 and attempt is not None:
        # A crash or failed insert cannot leave a claimed task without its
        # attempt ledger. The row update and attempt become durable together.
        db.add(attempt)
    db.commit()
    db.expire_all()
    return db.get(Job, job_id) if result.rowcount == 1 else None


def heartbeat(job_id, owner, stop):
    while not stop.wait(max(5, settings.lease_seconds // 3)):
        try:
            with SessionLocal() as db:
                if not renew_lease(db, job_id, owner):
                    return
        except Exception:
            # A failed heartbeat never claims success; expired leases are recovered by the scheduler.
            pass


def run_job_id(job_id: str):
    owner = str(uuid4())
    with SessionLocal() as db:
        attempt = JobAttempt(job_id=job_id)
        job = claim(db, job_id, owner, attempt=attempt)
        if job is None:
            return {"claimed": False}
        attempt_id = attempt.id
        stop = threading.Event()
        pulse = threading.Thread(target=heartbeat, args=(job.id, owner, stop), daemon=True)
        pulse.start()
        outcome, result, error, retry_after_seconds, deferred = "succeeded", {}, None, 0, False
        job_id, attempt_count = job.id, job.attempts

        def guard_commit(session):
            # Releasing a savepoint does not publish the outer transaction. Locking
            # here would keep the job row locked over later network operations and
            # prevent the separate heartbeat from renewing the lease.
            if not session.in_nested_transaction():
                fence_transaction(session, job_id, owner)

        # Covers commits made inside ingestion and backup modules as well as jobs.py.
        event.listen(db, "before_commit", guard_commit)
        try:
            from app.library_deletion import check_job_allowed, run_deletion
            check_job_allowed(db, job)
            if job.kind in {"delete_video", "delete_creator"}:
                result = run_deletion(db, job)
            elif job.kind == "refresh_credentials":
                from app.source_credentials import refresh_account
                result = refresh_account(db, job.account_id or job.target_id,
                    manual=bool(job.policy.get("manual")),
                    check_active=lambda: ensure_active(db, job_id, owner))
            elif job.kind == "backup":
                from app.backup import create_backup
                backup = create_backup(db, check_active=lambda: ensure_active(db, job_id, owner))
                result = {"backup_id": backup.id, "status": backup.status}
                if backup.status != "complete":
                    raise RuntimeError("备份未完成，请检查备份记录")
            elif job.kind == "migrate_storage":
                result = migrate_job(db, job, owner=owner)
            elif job.kind == "sync_video":
                result = sync_video_job(db, job, owner=owner)
            elif job.kind == "probe_storage":
                from app.storage.service import probe_profile
                ensure_active(db, job_id, owner)
                result = probe_profile(db, job.target_id)
            elif job.kind in {"retire_storage_location", "restore_storage_location", "purge_storage_location"}:
                from app.storage.lifecycle import retire_location, restore_location, purge_location
                ensure_active(db, job_id, owner)
                operation = {"retire_storage_location": retire_location, "restore_storage_location": restore_location,
                             "purge_storage_location": purge_location}[job.kind]
                if job.kind == "purge_storage_location":
                    result = operation(db, job.target_id, check_active=lambda: ensure_active(db, job_id, owner))
                else:
                    location = operation(db, job.target_id)
                    result = {"location_id": location.id, "state": location.state}
            elif job.kind == "prepare_media":
                from app.maintenance import run_media_preparation
                result = run_media_preparation(db, job, check_active=lambda: ensure_active(db, job_id, owner))
            else:
                from app.ingest.runner import run_job
                result = run_job(db, job) or {}
            if result.get("continuation"):
                outcome = "queued"
            # Do not leave uncommitted module writes to an unfenced session close.
            db.commit()
        except Exception as exc:
            db.rollback()
            # Only explicitly sanitized domain messages are shown to users.
            safe = exc.__class__.__module__.startswith(("app.ingest", "app.storage", "app.backup", "app.playback"))
            error = str(exc)[:1000] if safe else f"任务未完成（{type(exc).__name__}），请检查配置与运行记录"
            blocked = getattr(exc, "blocked", False)
            retryable = getattr(exc, "retryable", False)
            deferred = bool(getattr(exc, "deferred", False))
            retry_after_seconds = max(0, min(86400, float(getattr(exc, "retry_after_seconds", 0) or 0)))
            outcome = "lease_lost" if isinstance(exc, LeaseLost) else "queued" if deferred else "blocked" if blocked else "queued" if retryable and attempt_count < job.max_attempts else "failed"
            if exc.__class__.__name__ == "PartialCaptureError" and outcome == "failed":
                outcome = "partial"
        finally:
            event.remove(db, "before_commit", guard_commit)
            stop.set()
            pulse.join(timeout=2)
        with SessionLocal() as final:
            now = utcnow()
            values = {"status": outcome, "error": error, "finished_at": None if outcome == "queued" else now,
                      "lease_owner": None, "lease_expires_at": None}
            if result:
                values["result"] = result
            if outcome == "queued":
                values["available_at"] = now + timedelta(seconds=max(retry_after_seconds,
                    min(900, 15 * max(1, attempt_count)) if error else 2))
                if not error:
                    # A successful budget slice is not a failed retry attempt.
                    values["attempts"] = 0
                elif deferred:
                    # Waiting for a shared account slot is not an upstream failure.
                    values["attempts"] = max(0, attempt_count - 1)
            changed = None
            if outcome != "lease_lost":
                changed = final.execute(update(Job).where(*_owned(job_id, owner, _lease_clock(final, now))).values(**values)
                                        .execution_options(synchronize_session=False))
            if changed is not None and changed.rowcount == 1:
                attempt_status = outcome
                if outcome == "queued":
                    final.add(OutboxEvent(job_id=job_id))
            else:
                # A cancel/pause retains its requested state; a newer owner is never touched.
                stopped = final.execute(update(Job).where(Job.id == job_id, Job.lease_owner == owner,
                                        Job.status.in_(["paused", "cancelled"]))
                                        .values(lease_owner=None, lease_expires_at=None)
                                        .execution_options(synchronize_session=False))
                current_status = final.scalar(select(Job.status).where(Job.id == job_id))
                attempt_status = current_status if stopped.rowcount == 1 else "lease_lost"
            # Restart recovery may already have closed this attempt. A stale
            # worker cannot overwrite the recorded cancellation/lost-lease cause.
            final.execute(update(JobAttempt).where(JobAttempt.id == attempt_id, JobAttempt.finished_at.is_(None))
                .values(status=attempt_status, error=error, finished_at=utcnow())
                .execution_options(synchronize_session=False))
            final_status = final.scalar(select(JobAttempt.status).where(JobAttempt.id == attempt_id))
            final.commit()
        return {"status": final_status, "result": result if final_status not in {"lease_lost", "cancelled", "paused"} else {}}


def migrate_job(db, job, *, owner=None):
    from app.models import AssetLocation
    from app.storage.service import migrate_asset
    owner, job_id = owner or job.lease_owner, job.id
    checkpoint = ensure_active(db, job_id, owner)
    if "asset_ids" in checkpoint:
        ids = checkpoint["asset_ids"]
    else:
        requested = job.policy.get("asset_ids")
        stmt = select(AssetLocation.asset_id).where(AssetLocation.storage_profile_id == job.target_id,
                                                   AssetLocation.state == "ready").distinct().order_by(AssetLocation.asset_id)
        if requested is not None:
            stmt = stmt.where(AssetLocation.asset_id.in_(requested))
        ids = list(db.scalars(stmt))
        checkpoint = {**checkpoint, "asset_ids": ids, "total": len(ids)}
        save_checkpoint(db, job_id, owner, checkpoint)
    done = set(checkpoint.get("completed", []))
    for asset_id in ids:
        checkpoint = ensure_active(db, job_id, owner)
        if asset_id in done:
            continue
        def save_upload(state):
            checkpoints = ensure_active(db, job_id, owner)
            uploads = dict(checkpoints.get("uploads", {}))
            uploads[asset_id] = state
            checkpoints["uploads"] = uploads
            save_checkpoint(db, job_id, owner, checkpoints)
        migrate_asset(db, asset_id, job.policy["target_profile_id"],
                      checkpoint=checkpoint.get("uploads", {}).get(asset_id), on_checkpoint=save_upload)
        done.add(asset_id)
        checkpoint = ensure_active(db, job_id, owner)
        uploads = dict(checkpoint.get("uploads", {}))
        uploads.pop(asset_id, None)
        save_checkpoint(db, job_id, owner, {**checkpoint, "uploads": uploads, "completed": sorted(done), "total": len(ids)})
    return {"completed": len(done), "total": len(ids), "old_locations_retained": True}


def sync_video_job(db, job, *, owner):
    from app.storage.service import migrate_asset
    from app.storage.lifecycle import dependency_asset_ids
    checkpoint = ensure_active(db, job.id, owner)
    if "asset_ids" not in checkpoint:
        ids = sorted(dependency_asset_ids(db, job.policy["asset_ids"]))
        checkpoint = {**checkpoint, "asset_ids": ids, "total": len(ids)}
        save_checkpoint(db, job.id, owner, checkpoint)
    ids, done = checkpoint["asset_ids"], set(checkpoint.get("completed", []))
    for asset_id in ids:
        if asset_id in done:
            continue
        checkpoint = ensure_active(db, job.id, owner)
        def save_upload(state):
            current = ensure_active(db, job.id, owner)
            current["uploads"] = {**current.get("uploads", {}), asset_id: state}
            save_checkpoint(db, job.id, owner, current)
        migrate_asset(db, asset_id, job.policy["target_profile_id"],
                      checkpoint=checkpoint.get("uploads", {}).get(asset_id), on_checkpoint=save_upload)
        done.add(asset_id)
        current = ensure_active(db, job.id, owner)
        uploads = dict(current.get("uploads", {}))
        uploads.pop(asset_id, None)
        save_checkpoint(db, job.id, owner, {**current, "completed": sorted(done), "uploads": uploads})
    return {"completed": len(done), "total": len(ids), "originals_retained": True}


def schedule_due(db):
    now = utcnow()
    for source in db.scalars(select(SourceSubscription).where(SourceSubscription.enabled.is_(True),
                            or_(SourceSubscription.next_run_at.is_(None), SourceSubscription.next_run_at <= now))
                            .with_for_update(skip_locked=True)):
        active = db.scalar(select(Job.id).where(Job.kind == "scan_collection", Job.target_id == source.collection_id,
                           Job.status.in_(["queued", "running", "paused", "blocked"])).limit(1))
        if not active:
            enqueue(db, "scan_collection", source.collection_id, source.account_id, source.policy)
        source.next_run_at = now + timedelta(minutes=source.interval_minutes)
    db.commit()


def _recover_expired(db, now):
    # Only project scheduling fields: checkpoints can contain large comment
    # samples or multipart ledgers and are deliberately left untouched.
    expired = db.execute(select(Job.id, Job.status, Job.attempts, Job.max_attempts).where(
        or_(Job.status == "running", Job.status.in_(["paused", "cancelled"]) & Job.lease_owner.is_not(None)),
        or_(Job.lease_expires_at <= _lease_clock(db, now), Job.lease_expires_at.is_(None)))
        .order_by(Job.lease_expires_at.asc().nulls_first(), Job.id).limit(100)
        .with_for_update(skip_locked=True)).all()
    groups = {}
    for row in expired:
        status = ("queued" if row.attempts < row.max_attempts else "failed") if row.status == "running" else row.status
        groups.setdefault(status, []).append(row.id)
    for status, ids in groups.items():
        values = {"status": status, "lease_owner": None, "lease_expires_at": None}
        if status in {"queued", "failed"}:
            values.update(error="执行进程租约到期，已保存检查点", finished_at=now if status == "failed" else None)
        elif status == "cancelled":
            values["finished_at"] = func.coalesce(Job.finished_at, now)
        db.execute(update(Job).where(Job.id.in_(ids)).values(**values).execution_options(synchronize_session=False))
        db.execute(update(JobAttempt).where(JobAttempt.job_id.in_(ids), JobAttempt.status == "running",
            JobAttempt.finished_at.is_(None)).values(status="lease_lost" if status in {"queued", "failed"} else status,
            error="执行进程租约到期，已保存检查点", finished_at=now).execution_options(synchronize_session=False))
        if status == "queued":
            db.add_all(OutboxEvent(job_id=identity) for identity in ids)
    db.commit()


def recover_and_dispatch(db, send):
    now = utcnow()
    _recover_expired(db, now)
    # New continuation/retry events obey available_at without the lost-message
    # fallback delay. A conditional DB claim rejects duplicate delivery hints.
    last_publication = select(func.max(OutboxEvent.published_at)).where(OutboxEvent.job_id == Job.id).correlate(Job).scalar_subquery()
    unpublished = select(OutboxEvent.id).where(OutboxEvent.job_id == Job.id,
                                             OutboxEvent.published_at.is_(None)).correlate(Job).exists()
    eligible = select(Job.id, Job.kind).where(Job.status == "queued", Job.available_at <= now,
                                or_(unpublished, last_publication.is_(None),
                                    last_publication <= now - timedelta(seconds=60)))
    batch = db.execute(eligible.order_by(Job.available_at, Job.created_at, Job.id).limit(100)).all()
    events = {job.id: [] for job in batch}
    if batch:
        for identity, job_id in db.execute(select(OutboxEvent.id, OutboxEvent.job_id).where(
            OutboxEvent.job_id.in_(events), OutboxEvent.published_at.is_(None))):
            events[job_id].append(identity)
        missing = [OutboxEvent(job_id=job_id) for job_id, ids in events.items() if not ids]
        if missing:
            db.add_all(missing)
            db.flush()
            for item in missing:
                events[item.job_id].append(item.id)
    # Release the connection before contacting Redis. These immutable IDs are
    # the publication boundary: events created by a fast worker belong to the
    # next slice and must not be acknowledged by the current delivery.
    db.commit()
    delivered = []
    try:
        for job in batch:
            send(job.id, job.kind)
            delivered.extend(events[job.id])
    finally:
        if delivered:
            # One small acknowledgement transaction, including partial broker
            # failure. If this commit fails, hints may repeat; claim() is the
            # single execution gate, so no work is lost.
            db.execute(update(OutboxEvent).where(OutboxEvent.id.in_(delivered),
                OutboxEvent.published_at.is_(None)).values(published_at=utcnow())
                .execution_options(synchronize_session=False))
            db.commit()
