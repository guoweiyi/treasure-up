from datetime import timedelta

import pytest
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.testclient import TestClient

from app import playback_limits
from app.delivery_api import GUEST_COOKIE
from app.main import app
from app.models import PlaybackSession, StorageObservation, utcnow
from test_api import context, login
from test_delivery_api import seed_media


def create(client, part_id, **kwargs):
    return client.post('/api/v1/playback-sessions', json={'part_id': part_id}, **kwargs)


@pytest.mark.parametrize('authenticated', [False, True], ids=['guest', 'user'])
def test_identity_burst_limit_rejects_before_media_work_and_preserves_playback(context, monkeypatch, authenticated):
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp)
    if authenticated:
        login(client)
    monkeypatch.setattr(playback_limits, 'IDENTITY_WINDOW_LIMIT', 2)
    first = create(client, part.id)
    second = create(client, part.id)
    assert first.status_code == second.status_code == 200
    session = db.get(PlaybackSession, first.json()['id'])
    assert 14390 <= (session.expires_at.replace(tzinfo=utcnow().tzinfo) - utcnow()).total_seconds() <= 14400

    def unexpected_media_work(*args, **kwargs):
        raise AssertionError('Rejected creation must not load media or storage routes')

    with monkeypatch.context() as blocked:
        blocked.setattr('app.delivery_api.session_assets', unexpected_media_work)
        response = create(client, part.id)
    assert response.status_code == 429
    assert 1 <= int(response.headers['retry-after']) <= 60
    assert db.scalar(select(func.count()).select_from(PlaybackSession)) == 2
    assert db.scalar(select(func.count()).select_from(StorageObservation)) == 0
    assert client.get(first.json()['url']).status_code == 200
    assert client.get(second.json()['url']).status_code == 200
    # The rolling creation window recovers without deleting or shortening sessions.
    later = utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(playback_limits, 'utcnow', lambda: later)
    assert create(client, part.id).status_code == 200


@pytest.mark.parametrize('authenticated', [False, True], ids=['guest', 'user'])
def test_active_limit_outlasts_burst_window_and_expiry_frees_capacity(context, monkeypatch, authenticated):
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp)
    if authenticated:
        login(client)
    monkeypatch.setattr(playback_limits, 'IDENTITY_ACTIVE_LIMIT', 2)
    sessions = [create(client, part.id).json() for _ in range(2)]
    ids = [session['id'] for session in sessions]
    later = utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(playback_limits, 'utcnow', lambda: later)
    response = create(client, part.id)
    assert response.status_code == 429
    assert int(response.headers['retry-after']) > 14000
    db.get(PlaybackSession, ids[0]).expires_at = later - timedelta(seconds=1)
    db.commit()
    assert create(client, part.id).status_code == 200
    assert client.get(sessions[1]['url']).status_code == 200
    assert db.scalar(select(func.count()).select_from(PlaybackSession)) == 3


@pytest.mark.parametrize('limit_name', ['CLIENT_WINDOW_LIMIT', 'CLIENT_ACTIVE_LIMIT'])
def test_cookie_and_forwarding_header_rotation_cannot_bypass_client_limits(context, monkeypatch, limit_name):
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp)
    monkeypatch.setattr(playback_limits, limit_name, 2)
    tokens = []
    for index in range(3):
        client.cookies.clear()
        response = create(client, part.id, headers={'X-Forwarded-For': f'198.51.100.{index + 1}',
                                                   'X-Real-IP': f'192.0.2.{index + 1}'})
        assert response.status_code == (200 if index < 2 else 429)
        if index < 2:
            tokens.append(client.cookies.get(GUEST_COOKIE))
    assert tokens[0] != tokens[1]
    rows = list(db.scalars(select(PlaybackSession)))
    assert len(rows) == 2
    assert len({row.state['client_hash'] for row in rows}) == 1


@pytest.mark.parametrize('limit_name', ['GLOBAL_WINDOW_LIMIT', 'GLOBAL_ACTIVE_LIMIT'])
def test_global_limit_applies_across_client_addresses_and_guest_bindings(context, monkeypatch, limit_name):
    _, db, tmp = context
    _, part, _, _ = seed_media(db, tmp)
    monkeypatch.setattr(playback_limits, limit_name, 2)
    for index in range(3):
        with TestClient(app, base_url='http://localhost', client=(f'198.51.100.{index + 1}', 50000)) as client:
            assert create(client, part.id).status_code == (200 if index < 2 else 429)
    assert db.scalar(select(func.count()).select_from(PlaybackSession)) == 2


def test_authenticated_identity_is_shared_across_browsers_and_guest_cookie_rotation(context, monkeypatch):
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp)
    monkeypatch.setattr(playback_limits, 'IDENTITY_WINDOW_LIMIT', 1)
    login(client)
    assert create(client, part.id).status_code == 200
    client.cookies.set(GUEST_COOKIE, 'A' * 43)
    assert create(client, part.id).status_code == 429
    with TestClient(app, base_url='http://localhost', client=('198.51.100.99', 50000)) as other:
        login(other)
        assert create(other, part.id).status_code == 429
        login(other, 'reader')
        assert create(other, part.id).status_code == 200


def test_invalid_creation_does_not_reserve_rows_or_poison_next_request(context, monkeypatch):
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp)
    monkeypatch.setattr(playback_limits, 'IDENTITY_WINDOW_LIMIT', 1)
    assert create(client, 'missing').status_code == 404
    assert client.post('/api/v1/playback-sessions', json={'part_id': part.id, 'route_id': 'missing'}).status_code == 503
    assert db.scalar(select(func.count()).select_from(PlaybackSession)) == 0
    assert create(client, part.id).status_code == 200


def test_successful_creation_prunes_small_batches_without_a_scheduler(context):
    client, db, tmp = context
    _, part, variant, asset = seed_media(db, tmp)
    first = create(client, part.id).json()
    now = utcnow()
    expired = [PlaybackSession(variant_id=variant.id, protocol='file', state={},
                               expires_at=now - timedelta(hours=2)) for _ in range(12)]
    old_reports = [StorageObservation(profile_id=first['selected_route_id'], asset_id=asset.id,
                                     scope='server_storage', latency_ms=1, elapsed_ms=2,
                                     bytes_read=1, succeeded=True, measured_at=now - timedelta(days=8))
                   for _ in range(30)]
    recent = StorageObservation(profile_id=first['selected_route_id'], asset_id=asset.id,
                                scope='server_storage', latency_ms=1, elapsed_ms=2,
                                bytes_read=1, succeeded=True, measured_at=now)
    db.add_all([*expired, *old_reports, recent])
    db.commit()
    assert create(client, part.id).status_code == 200
    assert db.scalar(select(func.count()).select_from(PlaybackSession).where(PlaybackSession.expires_at < now)) == 4
    assert db.scalar(select(func.count()).select_from(StorageObservation).where(StorageObservation.measured_at < now)) == 6
    assert db.scalar(select(StorageObservation.id).where(StorageObservation.id == recent.id)) == recent.id
    assert client.get(first['url']).status_code == 200


def test_ipv6_address_rotation_groups_by_network_and_normalizes_mapped_ipv4():
    def digest(address):
        return playback_limits.client_hash(Request({'type': 'http', 'client': (address, 50000)}))
    assert digest('2001:db8:1234:5678::1') == digest('2001:db8:1234:5678:ffff::1234')
    assert digest('2001:db8:1234:5678::1') != digest('2001:db8:1234:5679::1')
    assert digest('::ffff:192.0.2.8') == digest('192.0.2.8')
