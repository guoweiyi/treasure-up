from starlette.testclient import TestClient
from http.cookies import SimpleCookie

from app.main import app
from app.delivery_api import GUEST_COOKIE
from app.models import Comment, VideoStar
from app.storage.service import ingest_file
from test_api import context, login
from test_delivery_api import seed_media


def test_new_guest_playback_renews_binding_without_invalidating_existing_session(context):
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp, hls=True)
    first = client.post('/api/v1/playback-sessions', json={'part_id': part.id})
    assert first.status_code == 200
    original_token = client.cookies.get(GUEST_COOKIE)
    # A browser near expiry must get another full four hours for the new video.
    second = client.post('/api/v1/playback-sessions', json={'part_id': part.id})
    assert second.status_code == 200
    renewed = SimpleCookie()
    renewed.load(second.headers.get('set-cookie', ''))
    assert GUEST_COOKIE in renewed
    assert renewed[GUEST_COOKIE].value == original_token
    assert renewed[GUEST_COOKIE]['max-age'] == str(4 * 3600)
    assert renewed[GUEST_COOKIE]['httponly']
    assert renewed[GUEST_COOKIE]['samesite'] == 'strict'
    assert renewed[GUEST_COOKIE]['path'] == '/api/v1/playback-sessions'
    assert client.get(first.json()['url']).status_code == 200
    assert client.get(second.json()['url']).status_code == 200


def test_guest_browsing_playback_and_write_boundaries(context):
    client, db, tmp = context
    video, part, _, asset = seed_media(db, tmp, hls=True)
    for path in ("/videos", f"/videos/{video.id}", "/creators", "/collections", "/library/settings",
                 f"/videos/{video.id}/comments", f"/parts/{part.id}/danmaku"):
        assert client.get('/api/v1' + path).status_code == 200
    detail = client.get(f'/api/v1/videos/{video.id}').json()
    assert detail['starred'] is False and 'notes' not in detail and 'capture_runs' not in detail
    response = client.post('/api/v1/playback-sessions', json={'part_id': part.id})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    session = response.json()
    assert client.get(session['url']).status_code == 200
    media = f'/api/v1/playback-sessions/{session["id"]}/assets/{asset.id}'
    assert client.get(media, headers={'Range': 'bytes=10-19'}).content == bytes(range(10, 20))
    assert client.get('/api/v1/admin/accounts').status_code == 401
    assert client.put(f'/api/v1/videos/{video.id}/star', json={'starred': True}).status_code == 401
    assert client.put(f'/api/v1/progress/{part.id}', json={'position': 5, 'duration': 100}).status_code == 401
    assert client.post('/api/v1/playback-sessions', json={'part_id': part.id},
                       headers={'Origin': 'https://other.invalid'}).status_code == 403
    private = tmp / 'raw.json'; private.write_text('{"internal":true}')
    raw = ingest_file(db, private, kind='source_raw', mime_type='application/json'); db.commit()
    assert client.get(f'/api/v1/assets/{raw.id}').status_code == 404
    with TestClient(app, base_url='http://localhost') as other:
        assert other.get(session['url']).status_code == 403
        own = other.post('/api/v1/playback-sessions', json={'part_id': part.id}).json()
        assert other.cookies.get(GUEST_COOKIE) != client.cookies.get(GUEST_COOKIE)
        assert other.get(own['url']).status_code == 200
        assert other.get(media).status_code == 403


def test_personal_stars_are_authenticated_idempotent_and_isolated(context):
    client, db, tmp = context
    video, _, _, _ = seed_media(db, tmp)
    login(client, 'reader')
    url = f'/api/v1/videos/{video.id}/star'
    for _ in range(2):
        assert client.put(url, json={'starred': True}).json() == {'starred': True}
    assert client.get(f'/api/v1/videos/{video.id}').json()['starred']
    assert client.get('/api/v1/videos?starred=true').json()['total'] == 1
    login(client, 'admin')
    assert not client.get(f'/api/v1/videos/{video.id}').json()['starred']
    assert client.get('/api/v1/videos?starred=true').json()['total'] == 0
    login(client, 'reader')
    csrf = client.headers.pop('X-CSRF-Token')
    assert client.put(url, json={'starred': False}).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    assert client.put(url, json={'starred': False}).json() == {'starred': False}
    assert client.get('/api/v1/videos?starred=true').json()['total'] == 0


def test_comments_default_to_likes_and_support_latest(context):
    client, db, tmp = context
    video, _, _, _ = seed_media(db, tmp)
    from datetime import datetime, timezone
    db.add_all([Comment(video_id=video.id, rpid='hot', content='older hot', like_count=100,
                        posted_at=datetime(2020, 1, 1, tzinfo=timezone.utc)),
                Comment(video_id=video.id, rpid='new', content='newest', like_count=1,
                        posted_at=datetime(2026, 10, 1, tzinfo=timezone.utc))])
    db.commit()
    assert client.get(f'/api/v1/videos/{video.id}/comments').json()['items'][0]['rpid'] == 'hot'
    assert client.get(f'/api/v1/videos/{video.id}/comments?sort=newest').json()['items'][0]['rpid'] == 'new'
