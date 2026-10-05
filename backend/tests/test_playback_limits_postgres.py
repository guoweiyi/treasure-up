"""Real HTTP and transaction contention in isolated treasure_test_* databases."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue

import pytest
from sqlalchemy import func, select, text
from starlette.testclient import TestClient

from app import delivery_api, playback_limits
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import PlaybackSession, StorageProfile, User, utcnow
from app.security import COOKIE_NAME, create_session
from test_delivery_api import seed_media
from test_passkeys_postgres import _wait_for_postgres_lock_waiters
from test_postgres_integration import postgres_workspace


pytestmark = pytest.mark.skipif(os.environ.get('TREASURE_RUN_POSTGRES_TESTS') != '1',
                                reason='Explicit isolated PostgreSQL test opt-in required')


def seed(space):
    with space.sessions() as db:
        user = User(username='playback-limit-user', password_hash='test-only')
        db.add_all([user, StorageProfile(name='Local', kind='local', is_default=True,
                                        config={'root': str(settings.media_root)})])
        db.flush()
        session, token = create_session(db, user)
        _, part, _, _ = seed_media(db, space.root)
        return part.id, {'Cookie': f'{COOKIE_NAME}={token}', 'X-CSRF-Token': session.csrf_token}


@pytest.mark.parametrize('scope,limit_name', [
    ('user', 'IDENTITY_WINDOW_LIMIT'), ('user', 'IDENTITY_ACTIVE_LIMIT'),
    ('guest', 'IDENTITY_WINDOW_LIMIT'), ('guest', 'IDENTITY_ACTIVE_LIMIT'),
    ('client', 'CLIENT_WINDOW_LIMIT'), ('client', 'CLIENT_ACTIVE_LIMIT'),
    ('global', 'GLOBAL_WINDOW_LIMIT'), ('global', 'GLOBAL_ACTIVE_LIMIT'),
])
def test_concurrent_http_creation_cannot_overbook_any_quota(postgres_workspace, monkeypatch, scope, limit_name):
    space = postgres_workspace
    part_id, user_headers = seed(space)
    monkeypatch.setattr(playback_limits, limit_name, 3)
    pids = Queue()

    def dependency():
        with space.sessions() as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            db.execute(text("SET LOCAL statement_timeout = '15s'"))
            pids.put(db.scalar(text('SELECT pg_backend_pid()')))
            yield db

    def post(index):
        address = '198.51.100.1' if scope == 'client' else f'198.51.100.{index + 1}'
        headers = user_headers if scope == 'user' else {
            'Cookie': f'treasure_viewer={chr(65 if scope == "guest" else 65 + index) * 43}'}
        with TestClient(app, base_url='http://localhost', client=(address, 50000)) as client:
            return client.post('/api/v1/playback-sessions', json={'part_id': part_id}, headers=headers)

    app.dependency_overrides[get_db] = dependency
    try:
        with space.sessions() as holder:
            with playback_limits.creation_lock(holder):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(post, index) for index in range(8)]
                    try:
                        waiting_pids = {pids.get(timeout=10) for _ in range(8)}
                        assert len(waiting_pids) == 8
                        _wait_for_postgres_lock_waiters(space, waiting_pids)
                    finally:
                        holder.rollback()
                    responses = [future.result(timeout=20) for future in futures]
        assert [response.status_code for response in responses].count(200) == 3
        assert [response.status_code for response in responses].count(429) == 5
        assert all(int(response.headers['retry-after']) > 0 for response in responses if response.status_code == 429)
        with space.sessions() as db:
            assert db.scalar(select(func.count()).select_from(PlaybackSession)) == 3
            assert all(row.state['client_hash'] for row in db.scalars(select(PlaybackSession)))
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_slow_media_preparation_does_not_hold_global_creation_lock(postgres_workspace, monkeypatch):
    space = postgres_workspace
    part_id, _ = seed(space)
    entered, release = threading.Event(), threading.Event()
    original = delivery_api.session_assets

    def slow_media(db, session):
        entered.set()
        assert release.wait(timeout=10)
        return original(db, session)

    monkeypatch.setattr(delivery_api, 'session_assets', slow_media)

    def dependency():
        with space.sessions() as db:
            yield db

    def post():
        with TestClient(app, base_url='http://localhost') as client:
            return client.post('/api/v1/playback-sessions', json={'part_id': part_id})

    app.dependency_overrides[get_db] = dependency
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(post)
            try:
                assert entered.wait(timeout=10)
                with space.sessions() as other:
                    assert other.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': playback_limits.CREATION_LOCK})
            finally:
                release.set()
            assert future.result(timeout=15).status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_creation_cleanup_skips_busy_expired_session(postgres_workspace):
    space = postgres_workspace
    part_id, _ = seed(space)

    def dependency():
        with space.sessions() as db:
            db.execute(text("SET LOCAL statement_timeout = '1s'"))
            yield db

    app.dependency_overrides[get_db] = dependency
    try:
        with TestClient(app, base_url='http://localhost') as client:
            first = client.post('/api/v1/playback-sessions', json={'part_id': part_id})
            assert first.status_code == 200
            expired_id = first.json()['id']
            with space.sessions() as db:
                db.get(PlaybackSession, expired_id).expires_at = utcnow() - timedelta(hours=2)
                db.commit()
            with space.sessions() as holder:
                holder.scalar(select(PlaybackSession).where(PlaybackSession.id == expired_id).with_for_update())
                assert client.post('/api/v1/playback-sessions', json={'part_id': part_id}).status_code == 200
            assert client.post('/api/v1/playback-sessions', json={'part_id': part_id}).status_code == 200
            with space.sessions() as db:
                assert db.get(PlaybackSession, expired_id) is None
                assert db.scalar(select(func.count()).select_from(PlaybackSession)) == 2
    finally:
        app.dependency_overrides.pop(get_db, None)
