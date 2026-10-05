import os
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.housekeeping import prune_transient_records
from app.models import User, UserSession, utcnow
from test_postgres_integration import postgres_workspace

pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit TREASURE_RUN_POSTGRES_TESTS=1 required")


def test_pruning_skips_locked_rows_and_keeps_live_sessions(postgres_workspace):
    ws = postgres_workspace
    now = utcnow()
    with ws.sessions() as db:
        user = User(username='cleanup-test', password_hash='not-a-real-password')
        db.add(user); db.flush()
        db.add_all([UserSession(id=str(i), user_id=user.id, token_hash=str(i), csrf_token='test',
                    expires_at=now + timedelta(days=2) if i == 2 else now - timedelta(days=2)) for i in range(3)])
        db.commit()
    with ws.sessions() as holder:
        holder.scalar(select(UserSession).where(UserSession.id == '0').with_for_update())
        with ws.sessions() as db:
            db.execute(text("SET LOCAL statement_timeout = '500ms'"))
            assert prune_transient_records(db, now=now)['sessions'] == 1
            assert set(db.scalars(select(UserSession.id))) == {'0', '2'}
    with ws.sessions() as db:
        assert prune_transient_records(db, now=now)['sessions'] == 1
        assert list(db.scalars(select(UserSession.id))) == ['2']
