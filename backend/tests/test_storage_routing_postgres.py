"""Real session-row contention with synthetic replicas, never business data."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.models import PlaybackSession, StorageProfile, utcnow
from app.storage import routing
from app.storage.base import StorageError
from app.storage.service import migrate_asset
from test_delivery_api import seed_media
from test_postgres_integration import postgres_workspace


pytestmark = pytest.mark.skipif(os.environ.get('TREASURE_RUN_POSTGRES_TESTS') != '1',
                                reason='Explicit isolated PostgreSQL test opt-in required')


def seed(space):
    with space.sessions() as db:
        _, _, variant, asset = seed_media(db, space.root)
        first = db.scalar(select(StorageProfile))
        second = StorageProfile(name='replica', kind='local', config={'root': str(space.root / 'replica')})
        db.add(second); db.flush()
        migrate_asset(db, asset.id, second.id)
        session = PlaybackSession(variant_id=variant.id, protocol='file', profile_id=first.id,
                                  state={'guest_hash': 'fixture', 'switch_count': 0},
                                  expires_at=utcnow() + timedelta(hours=1))
        db.add(session); db.commit()
        return session.id, asset.id, first.id, second.id


@pytest.mark.parametrize('fail_first', [False, True], ids=['healthy-parallel-heads', 'one-concurrent-failover'])
def test_parallel_fragments_do_not_serialize_healthy_io_but_share_failover(postgres_workspace, monkeypatch, fail_first):
    space = postgres_workspace
    session_id, asset_id, first_id, second_id = seed(space)
    original = routing.resolve_on_profile
    barrier = threading.Barrier(2, timeout=8)
    counts = {first_id: 0, second_id: 0}
    count_lock = threading.Lock()

    def slow_head(db, asset, profile, **kwargs):
        with count_lock:
            counts[profile] += 1
        if profile == first_id:
            # Before this fix only the first request could enter: the other
            # waited on its row lock while the provider operation was pending.
            barrier.wait()
            if fail_first:
                raise StorageError('Synthetic replica unavailable')
        return original(db, asset, profile, **kwargs)

    monkeypatch.setattr(routing, 'resolve_on_profile', slow_head)

    def read():
        with space.sessions() as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            db.execute(text("SET LOCAL statement_timeout = '15s'"))
            session = db.get(PlaybackSession, session_id)
            result = routing.resolve_session_asset(db, session, asset_id, frozenset([asset_id]))
            db.commit()
            return result['profile_id']

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(read) for _ in range(2)]
        profiles = [future.result(timeout=20) for future in futures]
    assert profiles == [second_id if fail_first else first_id] * 2
    assert counts == {first_id: 2, second_id: 2 if fail_first else 0}
    with space.sessions() as db:
        session = db.get(PlaybackSession, session_id)
        assert session.state['switch_count'] == int(fail_first)
        assert session.state.get('failed_profile_ids', []) == ([first_id] if fail_first else [])
        assert session.state['guest_hash'] == 'fixture'
