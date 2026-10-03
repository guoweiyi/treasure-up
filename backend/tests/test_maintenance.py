from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app import jobs, maintenance
from app.models import Asset, Base, Job, MediaVariant, OutboxEvent, Setting, SourceAccount, Video, VideoPart


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'maintenance.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    clock = SimpleNamespace(now=datetime(2026, 10, 4, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(maintenance, "utcnow", lambda: clock.now)
    yield SimpleNamespace(engine=engine, sessions=sessions, clock=clock)
    engine.dispose()


def add_videos(db, count, created_at, *, start=0, media=False):
    ids = []
    for index in range(start, start + count):
        ident = f"{index:036d}"
        video = Video(id=ident, bvid=f"BV{index:010d}", created_at=created_at)
        db.add(video)
        if media:
            asset = Asset(id=ident, sha256=f"{index:064x}", size=123, kind="media")
            part = VideoPart(id=ident, video_id=ident, cid=str(index))
            db.add_all([asset, part])
            db.flush()
            db.add(MediaVariant(id=ident, part_id=part.id, asset_id=asset.id, format_key="original",
                                kind="archive", duration=600, created_at=created_at))
        ids.append(ident)
    db.commit()
    return ids


def stats_config(db, *, enabled=False, account_id="account", interval=1):
    db.add(SourceAccount(id=account_id, name="mock account", secret_encrypted="not-a-credential"))
    db.add(Setting(key="statistics", value={"enabled": enabled, "account_id": account_id,
                                           "interval_hours": interval, "refresh_danmaku": False}))
    db.add(Setting(key="ingest", value={"quality": "720p", "request_interval_seconds": 3}))
    db.commit()


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def test_media_production_batch_limit_same_timestamps_and_bulk_existing_dedupe(workspace):
    ws = workspace
    with ws.sessions() as db:
        ids = add_videos(db, 205, ws.clock.now - timedelta(days=1), media=True)
        first = maintenance.enqueue_media_maintenance(db)
        assert first["queued"] == 100 and first["remaining_pending"]
        assert maintenance.enqueue_media_maintenance(db)["not_due"]
        assert db.scalar(select(func.count()).select_from(Job)) == 100
    # New sessions prove that the continuation does not rely on process memory.
    for expected in (100, 5):
        ws.clock.now += timedelta(seconds=5)
        with ws.sessions() as db:
            result = maintenance.enqueue_media_maintenance(db)
            assert result["queued"] == expected
    assert result["queued_total"] == 205 and not result["remaining_pending"]
    with ws.sessions() as db:
        assert set(db.scalars(select(Job.target_id))) == set(ids)
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 205
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(ws.engine, "before_cursor_execute", capture)
    try:
        ws.clock.now += timedelta(minutes=1)
        with ws.sessions() as db:
            result = maintenance.enqueue_media_maintenance(db)
            assert result["queued"] == 0 and result["remaining_pending"]
    finally:
        event.remove(ws.engine, "before_cursor_execute", capture)
    dedupe_queries = [query for query in statements if query.startswith("SELECT") and "jobs.dedupe_key" in query]
    assert len(dedupe_queries) == 1 and " IN " in dedupe_queries[0]
    # Subsequent cycles still advance over existing demands; no 100 per-row lookups.
    for _ in range(2):
        ws.clock.now += timedelta(seconds=5)
        with ws.sessions() as db:
            result = maintenance.enqueue_media_maintenance(db)
            assert result["queued"] == 0
    assert not result["remaining_pending"]


def test_media_freezes_policy_and_new_video_waits_for_next_cycle(workspace, monkeypatch):
    ws = workspace
    monkeypatch.setattr(maintenance, "BATCH_SIZE", 2)
    with ws.sessions() as db:
        ids = add_videos(db, 3, ws.clock.now - timedelta(days=1), media=True)
        maintenance.enqueue_media_maintenance(db)
        db.add(Setting(key="playback", value={"segment_seconds": 10, "analyze_loudness": False}))
        new_id = add_videos(db, 1, ws.clock.now + timedelta(seconds=1), start=3, media=True)[0]
    ws.clock.now += timedelta(seconds=5)
    with ws.sessions() as db:
        result = maintenance.enqueue_media_maintenance(db)
        assert result["queued"] == 1 and not result["remaining_pending"]
        first_jobs = list(db.scalars(select(Job)))
        assert {job.target_id for job in first_jobs} == set(ids)
        assert all(job.policy["segment_seconds"] == 6 and job.policy["analyze_loudness"] for job in first_jobs)
    ws.clock.now += timedelta(minutes=1)
    with ws.sessions() as db:
        assert maintenance.enqueue_media_maintenance(db)["remaining_pending"]
    ws.clock.now += timedelta(seconds=5)
    with ws.sessions() as db:
        assert not maintenance.enqueue_media_maintenance(db)["remaining_pending"]
        new_job = db.scalar(select(Job).where(Job.target_id == new_id))
        assert new_job.policy == {"package": True, "analyze_loudness": False, "segment_seconds": 10}


def test_manual_statistics_resumes_while_disabled_and_freezes_account_policy_spacing(workspace, monkeypatch):
    ws = workspace
    monkeypatch.setattr(maintenance, "BATCH_SIZE", 2)
    started = ws.clock.now
    with ws.sessions() as db:
        ids = add_videos(db, 5, started - timedelta(days=1))
        stats_config(db)
        assert maintenance.enqueue_statistics(db)["disabled"]
        first = maintenance.enqueue_statistics(db, manual=True)
        assert first["queued_now"] == first["queued"] == 2 and first["remaining_pending"]
        duplicate = maintenance.enqueue_statistics(db, manual=True)
        assert duplicate["queued"] == 0 and duplicate["already_running"]
        assert duplicate["batch_id"] == first["batch_id"]
        db.add(SourceAccount(id="another", name="another", secret_encrypted="mock"))
        db.get(Setting, "statistics").value = {"enabled": False, "account_id": "another", "refresh_danmaku": True}
        db.get(Setting, "ingest").value = {"quality": "1080p", "request_interval_seconds": 120}
        db.commit()
    for count in (2, 1):
        ws.clock.now += timedelta(seconds=5)
        with ws.sessions() as db:
            result = maintenance.enqueue_statistics(db)
            assert result["queued_now"] == count
    assert result["queued_total"] == 5 and not result["remaining_pending"]
    assert "policy" not in result and "account_id" not in result
    with ws.sessions() as db:
        queued = list(db.scalars(select(Job).order_by(Job.target_id)))
        assert [job.target_id for job in queued] == ids
        assert all(job.account_id == "account" and job.policy["quality"] == "720p"
                   and job.policy["refresh_danmaku"] is False for job in queued)
        assert [utc(job.available_at) for job in queued] == [started + timedelta(seconds=index * 3) for index in range(5)]
        assert maintenance.enqueue_statistics(db)["disabled"]


def test_statistics_next_interval_includes_new_video_and_skips_active_targets(workspace, monkeypatch):
    ws = workspace
    monkeypatch.setattr(maintenance, "BATCH_SIZE", 2)
    with ws.sessions() as db:
        ids = add_videos(db, 3, ws.clock.now - timedelta(days=1))
        stats_config(db, enabled=True)
        jobs.enqueue(db, "refresh_stats", ids[0], "account", dedupe_key="already-running")
        db.commit()
        first = maintenance.enqueue_statistics(db)
        assert first["queued_now"] == 1 and first["remaining_pending"]
        new_id = add_videos(db, 1, ws.clock.now + timedelta(seconds=1), start=3)[0]
    ws.clock.now += timedelta(seconds=5)
    with ws.sessions() as db:
        result = maintenance.enqueue_statistics(db)
        assert result["queued_now"] == 1 and not result["remaining_pending"]
        assert not db.scalar(select(Job).where(Job.target_id == new_id))
        assert maintenance.enqueue_statistics(db)["not_due"]
    ws.clock.now += timedelta(hours=1)
    with ws.sessions() as db:
        next_batch = maintenance.enqueue_statistics(db)
        assert next_batch["batch_id"] != first["batch_id"]
        assert next_batch["queued_now"] == 0 and next_batch["remaining_pending"]
    ws.clock.now += timedelta(seconds=5)
    with ws.sessions() as db:
        final = maintenance.enqueue_statistics(db)
        assert final["queued_now"] == 1 and not final["remaining_pending"]
        assert db.scalar(select(func.count()).select_from(Job)) == 4
        assert db.scalar(select(Job).where(Job.target_id == new_id))


def test_stats_dedupe_survives_replaying_cursor_after_completed_jobs(workspace, monkeypatch):
    ws = workspace
    monkeypatch.setattr(maintenance, "BATCH_SIZE", 2)
    with ws.sessions() as db:
        add_videos(db, 3, ws.clock.now - timedelta(days=1))
        stats_config(db)
        maintenance.enqueue_statistics(db, manual=True)
        for job in db.scalars(select(Job)):
            job.status = "succeeded"
        runtime = db.get(Setting, "statistics_runtime")
        runtime.value = {**runtime.value, "cursor": None}
        db.commit()
    ws.clock.now += timedelta(seconds=5)
    with ws.sessions() as db:
        result = maintenance.enqueue_statistics(db)
        assert result["queued_now"] == 0 and result["remaining_pending"]
        assert db.scalar(select(func.count()).select_from(Job)) == 2
    ws.clock.now += timedelta(seconds=5)
    with ws.sessions() as db:
        result = maintenance.enqueue_statistics(db)
        assert result["queued_now"] == 1 and result["queued_total"] == 3


def test_batch_jobs_outbox_and_cursor_commit_atomically(workspace, monkeypatch):
    ws = workspace
    with ws.sessions() as db:
        add_videos(db, 3, ws.clock.now - timedelta(days=1))
        stats_config(db)
        real_enqueue = jobs.enqueue
        calls = 0

        def fail_mid_batch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected queue failure")
            return real_enqueue(*args, **kwargs)

        monkeypatch.setattr(jobs, "enqueue", fail_mid_batch)
        with pytest.raises(RuntimeError, match="injected"):
            maintenance.enqueue_statistics(db, manual=True)
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert db.get(Setting, "statistics_runtime") is None
        monkeypatch.setattr(jobs, "enqueue", real_enqueue)
        assert maintenance.enqueue_statistics(db, manual=True)["queued"] == 3


def test_empty_library_finishes_without_pending_loop_and_legacy_due_state_works(workspace):
    ws = workspace
    with ws.sessions() as db:
        stats_config(db, enabled=True)
        db.add(Setting(key="statistics_runtime", value={"next_run_at": (ws.clock.now - timedelta(seconds=1)).isoformat(),
                                                       "last_count": 900}))
        db.commit()
        result = maintenance.enqueue_statistics(db)
        assert result["queued"] == 0 and result["queued_total"] == 0 and not result["remaining_pending"]
        assert maintenance.enqueue_statistics(db)["not_due"]
        media = maintenance.enqueue_media_maintenance(db)
        assert media["queued"] == 0 and not media["remaining_pending"]
        assert maintenance.enqueue_media_maintenance(db)["not_due"]
