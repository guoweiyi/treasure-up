"""Optional PostgreSQL maintenance lock check, in a fresh disposable test DB."""
import os

import pytest
from sqlalchemy import func, select, text

from app import maintenance
from app.models import Job, Setting, SourceAccount, Video
from test_postgres_integration import postgres_workspace


pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit TREASURE_RUN_POSTGRES_TESTS=1 required")


def test_busy_maintenance_returns_without_blocking_and_can_resume(postgres_workspace, monkeypatch):
    ws = postgres_workspace
    monkeypatch.setattr(maintenance, "BATCH_SIZE", 1)
    with ws.sessions() as db:
        db.add(SourceAccount(id="test-account", name="mock", secret_encrypted="mock"))
        db.add(Setting(key="statistics", value={"enabled": False, "account_id": "test-account"}))
        db.add_all([Video(bvid="BV0000000001"), Video(bvid="BV0000000002")])
        db.commit()
    with ws.engine.begin() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(87213002)"))
        with ws.sessions() as db:
            # A blocking advisory/row lock would hit the server-side timeout.
            db.execute(text("SET LOCAL statement_timeout = '300ms'"))
            result = maintenance.enqueue_statistics(db, manual=True)
            assert result["busy"] and result["queued"] == 0
            assert db.get(Setting, "statistics_runtime") is None
            assert db.scalar(select(func.count()).select_from(Job)) == 0
    with ws.sessions() as db:
        result = maintenance.enqueue_statistics(db, manual=True)
        assert result["queued"] == 1 and result["remaining_pending"]
    with ws.sessions() as db:
        duplicate = maintenance.enqueue_statistics(db, manual=True)
        assert duplicate["already_running"] and duplicate["queued"] == 0
        assert duplicate["batch_id"] == result["batch_id"]
        assert db.scalar(select(func.count()).select_from(Job)) == 1
