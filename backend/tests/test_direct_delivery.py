import json
from urllib.parse import quote, urlsplit

import pytest
from sqlalchemy import select

from app.models import AssetLocation, PlaybackSession, StorageProfile, utcnow
from app.security import encrypt_secret
from app.storage.base import StorageError
from app.storage.delivery import validate_delivery_url
from test_api import context, login
from test_delivery_api import seed_media


class SigningOnly:
    def __init__(self):
        self.signed = []

    def presign(self, key, expires_in, *, version_id=None):
        self.signed.append((key, expires_in, version_id))
        return 'https://fixture.oss.example/' + quote(key) + '?signature=test-only'

    def head(self, *args, **kwargs):
        raise AssertionError('Direct delivery must not HEAD every object')


def cloud_replica(db, asset, *, mode='auto'):
    profile = StorageProfile(name='OSS direct', kind='oss', config={
        'bucket': 'fixture', 'region': 'cn-hangzhou', 'endpoint': 'https://oss-cn-hangzhou.aliyuncs.com',
        'read_priority': 0, 'delivery_mode': mode,
    }, secret_encrypted=encrypt_secret(json.dumps({'access_key_id': 'fixture', 'access_key_secret': 'fixture'})))
    db.add(profile); db.flush()
    db.add(AssetLocation(asset_id=asset.id, storage_profile_id=profile.id,
                         object_key='videos/测试视频-UP主.mp4', state='ready', verified_at=utcnow()))
    db.commit()
    return profile


def test_cloud_file_issues_direct_url_without_reading_or_proxying_media(context, monkeypatch):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp)
    profile = cloud_replica(db, asset)
    signer = SigningOnly()
    monkeypatch.setattr('app.storage.delivery.get_adapter', lambda _: signer)
    response = client.post('/api/v1/playback-sessions', json={'part_id': part.id})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['selected_route_id'] == profile.id
    assert data['direct'] and data['delivery'] == 'direct' and data['url_expires_at']
    assert urlsplit(data['url']).hostname == 'fixture.oss.example'
    assert len(signer.signed) == 1 and signer.signed[0][1] == 3600
    assert 'access_key_secret' not in response.text
    # Only authenticated session creation issues the capability; a foreign
    # website cannot create one, and local playback still uses protected paths.
    assert client.post('/api/v1/playback-sessions', json={'part_id': part.id},
                       headers={'Origin': 'https://evil.invalid'}).status_code == 403
    local = db.scalar(select(StorageProfile).where(StorageProfile.kind == 'local'))
    other = client.post('/api/v1/playback-sessions', json={'part_id': part.id, 'route_id': local.id}).json()
    assert not other['direct'] and other['url'].startswith('/api/')
    assert client.get(other['url'], headers={'Range': 'bytes=0-2'}).content == bytes([0, 1, 2])


def test_hls_cloud_segments_are_signed_in_one_manifest_and_remain_session_bound(context, monkeypatch):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp, hls=True)
    cloud_replica(db, asset)
    signer = SigningOnly()
    monkeypatch.setattr('app.storage.delivery.get_adapter', lambda _: signer)
    login(client)
    response = client.post('/api/v1/playback-sessions', json={'part_id': part.id})
    assert response.status_code == 200
    data = response.json()
    assert data['direct'] and data['protocol'] == 'hls' and data['url'].startswith('/api/')
    manifest = client.get(data['url'])
    assert manifest.status_code == 200
    assert 'https://fixture.oss.example/videos/' in manifest.text
    assert '/assets/' not in manifest.text and 'Cookie' not in manifest.text
    assert len(signer.signed) == 1  # This fixture reuses its init object as a fragment.
    assert 1 <= signer.signed[0][1] <= 3600
    assert manifest.headers['cache-control'] == 'private, no-store'
    login(client, 'reader')
    assert client.get(data['url']).status_code == 403


def test_retired_replica_cannot_mint_new_fragment_urls(context, monkeypatch):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp, hls=True)
    profile = cloud_replica(db, asset)
    signer = SigningOnly()
    monkeypatch.setattr('app.storage.delivery.get_adapter', lambda _: signer)
    data = client.post('/api/v1/playback-sessions', json={'part_id': part.id}).json()
    location = db.scalar(select(AssetLocation).where(AssetLocation.storage_profile_id == profile.id))
    location.state = 'retired'; db.commit()
    assert client.get(data['url']).status_code == 503
    assert not signer.signed


def test_cloud_redirect_mode_keeps_compatible_session_endpoint(context, monkeypatch):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp)
    cloud_replica(db, asset, mode='redirect')
    def no_signing(*args, **kwargs):
        raise AssertionError('Compatibility mode signs only when the endpoint is used')
    monkeypatch.setattr('app.storage.delivery.get_adapter', no_signing)
    data = client.post('/api/v1/playback-sessions', json={'part_id': part.id}).json()
    assert data['delivery'] == 'redirect' and not data['direct']
    assert data['url'].startswith('/api/v1/playback-sessions/')


@pytest.mark.parametrize('value', [
    '//evil.invalid/object', 'javascript:alert(1)', 'https://user:secret@host/object',
    'https://host:99999/object', 'https://host/object\n#EXT-X-KEY',
    'https://host/object"bad', 'https://host/object\\bad', 'https://host/object#fragment',
])
def test_cloud_manifest_urls_reject_injection(value):
    with pytest.raises(StorageError):
        validate_delivery_url(value)


def test_signing_failure_does_not_create_a_playback_session(context, monkeypatch):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp)
    cloud_replica(db, asset)
    class FailedSigner:
        def presign(self, *args, **kwargs):
            raise RuntimeError('https://secret-token-must-not-escape.invalid')
    monkeypatch.setattr('app.storage.delivery.get_adapter', lambda _: FailedSigner())
    response = client.post('/api/v1/playback-sessions', json={'part_id': part.id})
    assert response.status_code == 503 and 'secret-token' not in response.text
    assert not list(db.scalars(select(PlaybackSession)))
