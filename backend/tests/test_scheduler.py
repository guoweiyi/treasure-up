from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app import scheduler


class LeadershipLost(RuntimeError):
    pass


@pytest.mark.parametrize("cycle_fails", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_scheduler_exits_on_lost_leader_connection_and_reacquires_on_restart(monkeypatch, cycle_fails, cleanup_fails):
    events = []
    class Leader:
        def execution_options(self, **options):
            assert options == {"isolation_level": "AUTOCOMMIT"}
            return self
        def __enter__(self):
            self.pings = 0
            return self
        def __exit__(self, *args):
            events.append("closed")
        def scalar(self, query):
            assert "pg_try_advisory_lock" in str(query)
            events.append("lock")
            return True
        def execute(self, query):
            assert str(query) == "SELECT 1"
            self.pings += 1
            if self.pings == 2:
                raise LeadershipLost("connection was terminated")
    monkeypatch.setattr(scheduler, "engine", SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"), connect=Leader))
    @contextmanager
    def session():
        events.append("session")
        yield object()
    monkeypatch.setattr(scheduler, "SessionLocal", session)
    def due(db):
        events.append("due")
        if cycle_fails:
            raise ValueError("a failed cycle must not hide the next lost leader")
    monkeypatch.setattr(scheduler, "schedule_due", due)
    monkeypatch.setattr(scheduler, "schedule_backup", lambda db: None)
    def cleanup(db):
        events.append("cleanup")
        if cleanup_fails:
            raise ValueError("cleanup failure must not stop job dispatch")
    monkeypatch.setattr(scheduler, "prune_transient_records", cleanup)
    monkeypatch.setattr("app.maintenance.enqueue_statistics", lambda db: None)
    monkeypatch.setattr("app.maintenance.enqueue_media_maintenance", lambda db: None)
    monkeypatch.setattr("app.source_credentials.schedule_refreshes", lambda db: None)
    monkeypatch.setattr(scheduler, "recover_and_dispatch", lambda db, send: events.append("dispatch"))
    monkeypatch.setattr(scheduler.time, "sleep", lambda delay: events.append("sleep"))
    for _ in range(2):
        with pytest.raises(LeadershipLost):
            scheduler.main()
    cycle = ["lock", "session", "cleanup", "session", "due", *([] if cycle_fails else ["dispatch"]), "sleep", "closed"]
    assert events == cycle * 2
