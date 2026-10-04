import logging
import time
from datetime import timedelta, timezone

from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.jobs import enqueue, recover_and_dispatch, schedule_due
from app.models import Job, Setting, utcnow
from app.worker import execute


def queue_for_kind(kind):
    if kind == "backup":
        return "backup"
    if kind == "download_media":
        return "download"
    if kind in {"create_playback", "migrate_storage", "prepare_media", "sync_video",
                "retire_storage_location", "restore_storage_location", "purge_storage_location"}:
        return "media"
    return "collector"


def schedule_backup(db):
    config = db.get(Setting, "backup")
    if not config or not config.value.get("enabled"):
        return
    last = db.scalar(select(Job).where(Job.kind == "backup").order_by(Job.created_at.desc()).limit(1))
    if last and (last.status in {"queued", "running", "paused", "blocked"} or
                 last.created_at.replace(tzinfo=timezone.utc) > utcnow() - timedelta(hours=config.value.get("interval_hours", 12))):
        return
    enqueue(db, "backup", "library")
    db.commit()


def main():
    if engine.dialect.name != "postgresql":
        raise RuntimeError("持续调度仅支持PostgreSQL；本地测试请显式单次执行任务")
    # The session lock must survive cycle transactions without retaining an
    # idle transaction. A lost connection also loses leadership: exit so the
    # process supervisor restarts us and acquires a fresh lock before dispatch.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as leader:
        if not leader.scalar(text("SELECT pg_try_advisory_lock(87213001)")):
            raise RuntimeError("已有调度器运行")
        while True:
            leader.execute(text("SELECT 1"))
            try:
                with SessionLocal() as db:
                    schedule_due(db)
                    schedule_backup(db)
                    from app.maintenance import enqueue_statistics, enqueue_media_maintenance
                    enqueue_statistics(db)
                    enqueue_media_maintenance(db)
                    recover_and_dispatch(db, lambda job_id, kind: execute.apply_async(args=[job_id],
                        queue=queue_for_kind(kind)))
            except Exception as exc:
                logging.error("Scheduler cycle failed: %s", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
