from datetime import timedelta

from sqlalchemy import select

from app.housekeeping import prune_transient_records
from app.models import SourceAccount, SourceAuthorization, User, UserSession, utcnow
from test_postgres_integration import postgres_workspace, pytestmark


def test_expired_authorization_cleanup_skips_busy_rows_and_preserves_account_refresh_state(postgres_workspace):
    sessions = postgres_workspace.sessions
    now = utcnow()
    with sessions() as db:
        user = User(username='fixture', password_hash='fixture')
        account = SourceAccount(name='fixture', secret_encrypted='encrypted-fixture',
            refresh_pending_encrypted='pending-fixture')
        db.add_all([user, account]); db.flush()
        session = UserSession(user_id=user.id, token_hash='a'*64, csrf_token='fixture', expires_at=now+timedelta(hours=1))
        db.add(session); db.flush()
        rows = [SourceAuthorization(user_id=user.id, session_id=session.id, account_id=account.id, name='fixture',
            key_encrypted='encrypted-fixture', credential_encrypted='encrypted-fixture', status='validating',
            expires_at=now+timedelta(seconds=60) if index == 2 else now-timedelta(seconds=60)) for index in range(3)]
        db.add_all(rows); db.commit()
        ids, account_id = [row.id for row in rows], account.id
    with sessions() as busy:
        busy.execute(select(SourceAuthorization.id).where(SourceAuthorization.id == ids[0]).with_for_update())
        with sessions() as db:
            assert prune_transient_records(db, now=now)['source_authorizations'] == 1
        busy.rollback()
    with sessions() as db:
        assert prune_transient_records(db, now=now)['source_authorizations'] == 1
        assert list(db.scalars(select(SourceAuthorization.id))) == [ids[2]]
        assert db.get(SourceAccount, account_id).refresh_pending_encrypted == 'pending-fixture'
