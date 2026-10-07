from datetime import timedelta

from sqlalchemy import select

from app.housekeeping import prune_transient_records
from app.models import PlaybackSession, SourceAuthorization, StorageObservation, UserSession, utcnow
from test_api import context, login
from test_delivery_api import seed_media


def test_pruning_is_bounded_and_retains_active_sessions_and_recent_observations(context):
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp)
    login(client)
    now = utcnow()
    identifiers = []
    for _ in range(4):
        result = client.post('/api/v1/playback-sessions', json={'part_id': part.id}).json()
        identifiers.append(result['id'])
    # Age them after creation; new playback also opportunistically prunes rows.
    for index, identifier in enumerate(identifiers):
        db.get(PlaybackSession, identifier).expires_at = now - timedelta(hours=3) if index < 3 else now + timedelta(hours=1)
    profile = db.get(PlaybackSession, identifiers[-1]).profile_id
    for age in (8, 0):
        db.add(StorageObservation(profile_id=profile, latency_ms=1, elapsed_ms=1, bytes_read=1, succeeded=True,
                                  measured_at=now - timedelta(days=age)))
    old_session = UserSession(user_id=db.scalar(select(UserSession.user_id)), token_hash='expired-test-token',
                              csrf_token='expired', expires_at=now - timedelta(days=2))
    db.add(old_session); db.commit()
    counts = prune_transient_records(db, now=now, batch_size=2)
    assert counts == {'source_authorizations': 0, 'playback_sessions': 2, 'sessions': 1, 'storage_observations': 1}
    db.expire_all()
    remaining = list(db.scalars(select(PlaybackSession)))
    assert len(remaining) == 2
    assert identifiers[-1] in [row.id for row in remaining]
    assert client.get('/api/v1/auth/me').status_code == 200
    assert len(list(db.scalars(select(StorageObservation)))) == 1
    assert prune_transient_records(db, now=now)['playback_sessions'] == 1


def test_expired_qr_credentials_are_pruned_within_the_existing_batch_budget(context):
    client, db, _ = context
    login(client)
    session = db.scalar(select(UserSession))
    now = utcnow()
    entries = []
    for index, status in enumerate(('pending', 'validating', 'authorized', 'validating')):
        row = SourceAuthorization(user_id=session.user_id, session_id=session.id, name='fixture', status=status,
            key_encrypted='encrypted-fixture', credential_encrypted='encrypted-fixture',
            expires_at=now + timedelta(seconds=60) if index == 3 else now - timedelta(seconds=60))
        db.add(row); db.flush()
        entries.append(row.id)
    db.commit()
    assert prune_transient_records(db, now=now, batch_size=2)['source_authorizations'] == 2
    assert db.scalar(select(SourceAuthorization.id).where(SourceAuthorization.id == entries[-1])) == entries[-1]
    assert prune_transient_records(db, now=now)['source_authorizations'] == 1
    assert list(db.scalars(select(SourceAuthorization.id))) == [entries[-1]]
