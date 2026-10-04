import json
from datetime import timedelta

from sqlalchemy import select

from app.models import (AssetRef, MediaVariant, PlaybackSession, Setting, SourceAccount, StorageProfile,
                        VideoStatSnapshot, Job, utcnow)
from app.maintenance import enqueue_statistics, enqueue_media_maintenance
from app.storage.service import ingest_file, migrate_asset
from test_api import context, login, seed_catalog


def seed_media(db, tmp, *, hls=False):
    video, part, _ = seed_catalog(db)
    path = tmp / "original.mp4"
    path.write_bytes(bytes(range(256)) * 1024)
    asset = ingest_file(db, path, kind="media", mime_type="video/mp4")
    original = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="original", duration=3600)
    db.add(original); db.flush()
    compatible = MediaVariant(part_id=part.id, asset_id=asset.id, kind="playback", format_key="compatible")
    db.add(compatible); db.flush()
    if hls:
        index = {"schema": "treasure.hls.v1", "stream_copy": True, "source_asset_id": asset.id,
                 "init_asset_id": asset.id, "target_duration": 6,
                 "segments": [{"sequence": 0, "duration": 6, "asset_id": asset.id}]}
        path = tmp / "package.json"; path.write_text(json.dumps(index))
        index_asset = ingest_file(db, path, kind="hls_index", mime_type="application/json")
        db.add(AssetRef(asset_id=asset.id, entity_type="asset", entity_id=index_asset.id, purpose="hls_segment"))
        db.add(MediaVariant(part_id=part.id, asset_id=index_asset.id, kind="hls", format_key="hls",
                            metadata_json={"source_variant_id": original.id, "source_asset_id": asset.id, "stream_copy": True}))
    db.commit()
    return video, part, original, asset


def test_original_quality_default_and_session_ownership(context):
    client, db, tmp = context
    _, part, original, asset = seed_media(db, tmp)
    login(client)
    result = client.post('/api/v1/playback-sessions', json={"part_id": part.id})
    assert result.status_code == 200, result.text
    session = result.json()
    assert session['variant_id'] == original.id
    assert client.head(session['url']).headers['content-length'] == str(asset.size)
    assert client.get(session['url'], headers={'Range': 'bytes=17-19'}).content == bytes([17, 18, 19])
    assert client.get(session['url'].rsplit('/', 1)[0] + '/unrelated').status_code == 404
    login(client, 'reader')
    assert client.get(session['url']).status_code == 403


def test_hls_manifest_authentication_and_expiry(context):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp, hls=True)
    login(client)
    session = client.post('/api/v1/playback-sessions', json={"part_id": part.id}).json()
    assert session['protocol'] == 'hls'
    manifest = client.get(session['url'])
    assert manifest.status_code == 200 and '#EXT-X-ENDLIST' in manifest.text
    assert f'/playback-sessions/{session["id"]}/assets/{asset.id}' in manifest.text
    assert 'https://' not in manifest.text and 'Cookie' not in manifest.text
    record = db.get(PlaybackSession, session['id']); record.expires_at = utcnow() - timedelta(seconds=1); db.commit()
    assert client.get(session['url']).status_code == 403


def test_failed_routes_persist_when_every_node_is_down(context, monkeypatch):
    from app.storage.base import StorageError
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp)
    login(client)
    session = client.post('/api/v1/playback-sessions', json={'part_id':part.id}).json()
    def offline(*args, **kwargs):
        raise StorageError('test node offline')
    monkeypatch.setattr('app.storage.routing.resolve_on_profile', offline)
    assert client.get(session['url']).status_code == 503
    db.expire_all()
    stored = db.get(PlaybackSession, session['id'])
    assert stored.state['failed_profile_ids'] == [session['selected_route_id']]


def test_client_route_probe_is_bounded_and_observation_cannot_cross_sessions(context):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp)
    login(client)
    data = client.post('/api/v1/playback-sessions', json={'part_id': part.id}).json()
    route = data['routes'][0]
    response = client.get(route['probe_url'])
    assert response.status_code == 206 and len(response.content) == 65536
    body = {'route_id':route['id'], 'latency_ms':2, 'elapsed_ms':12, 'bytes_read':65536, 'succeeded':True}
    url = f'/api/v1/playback-sessions/{data["id"]}/observations'
    assert client.post(url, json={**body, 'bytes_read':10**9}).status_code == 422
    assert client.post(url, json={**body, 'route_id':'unrelated'}).status_code == 422
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 409
    new = client.post('/api/v1/playback-sessions', json={'part_id':part.id}).json()
    assert new['routes'][0]['measurement_scope'] == 'browser_delivery'


def test_stats_refresh_is_spread_and_does_not_download_media(context):
    client, db, tmp = context
    video, part, _, _ = seed_media(db, tmp)
    account = SourceAccount(name='local-test', secret_encrypted='unused', uid='123')
    db.add(account); db.flush()
    db.add(Setting(key='statistics', value={'enabled':True,'account_id':account.id,'interval_hours':4}))
    db.commit()
    result = enqueue_statistics(db)
    assert result['queued'] == 1
    jobs = list(db.scalars(select(Job)))
    assert len(jobs) == 1 and jobs[0].kind == 'refresh_stats' and jobs[0].target_id == video.id
    assert enqueue_statistics(db)['queued'] == 0
    db.add(VideoStatSnapshot(video_id=video.id, counts={'view':9876543210,'like':123,'coin':None}))
    db.commit(); login(client)
    values = client.get(f'/api/v1/videos/{video.id}').json()['stats']
    assert values['view'] == 9876543210 and values['coin'] is None and values['favorite'] is None


def test_settings_allow_clearing_rate_and_validate_merged_statistics(context):
    client, db, tmp = context
    account = SourceAccount(name='settings-only', secret_encrypted='unused')
    db.add(account); db.commit(); login(client)
    url = '/api/v1/admin/settings'
    assert client.patch(url, json={'ingest':{'download_rate_bytes':1000000}}).status_code == 200
    response = client.patch(url, json={'ingest':{'download_rate_bytes':None}})
    assert response.status_code == 200 and response.json()['ingest']['download_rate_bytes'] is None
    assert client.patch(url, json={'statistics':{'account_id':account.id}}).status_code == 200
    assert client.patch(url, json={'statistics':{'enabled':True}}).status_code == 200
    assert client.patch(url, json={'statistics':{'account_id':None}}).status_code == 422
    response = client.patch(url, json={'statistics':{'enabled':False,'account_id':None}})
    assert response.status_code == 200 and response.json()['statistics']['account_id'] is None


def test_replica_actions_are_worker_jobs_and_purge_requires_retirement(context):
    client, db, tmp = context
    video, part, original, asset = seed_media(db, tmp)
    login(client)
    listing = client.get(f'/api/v1/admin/videos/{video.id}/storage').json()
    location = listing['items'][0]
    url = '/api/v1/admin/storage/locations/' + location['id']
    assert client.post(url + '/purge', json={'confirm':'DELETE'}).status_code == 409
    assert client.delete(url).status_code == 202
    assert client.post(url + '/restore').status_code == 202
    enqueue_media_maintenance(db)
    prepare = db.scalar(select(Job).where(Job.kind=='prepare_media'))
    assert prepare.policy['package'] is True
    assert prepare.target_id == original.id


def test_hls_segment_requests_reuse_validated_authorization_index(context, monkeypatch):
    from app.playback import hls
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp, hls=True)
    login(client)
    data = client.post('/api/v1/playback-sessions', json={'part_id': part.id}).json()
    def unexpected_scan(*args, **kwargs):
        raise AssertionError('A cached HLS segment must not rescan the whole playlist')
    monkeypatch.setattr(hls, 'validate_index', unexpected_scan)
    monkeypatch.setattr(hls, 'read_asset_bytes', unexpected_scan)
    url = f'/api/v1/playback-sessions/{data["id"]}/assets/{asset.id}'
    for _ in range(3):
        response = client.get(url, headers={'Range': 'bytes=7-9'})
        assert response.status_code == 206 and response.content == bytes([7, 8, 9])
