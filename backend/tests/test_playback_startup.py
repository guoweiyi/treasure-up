"""Startup must inspect the small index and selected fragment, never the archive."""
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models import AssetLocation, MediaVariant, PlaybackSession, StorageProfile, Video, utcnow
from app.storage import routing
from app.storage.graph import GraphStorage
from app.storage.local import LocalStorage
from app.storage.service import get_adapter, ingest_file, resolve_asset
from test_api import context
from test_delivery_api import seed_media
from test_direct_delivery import SigningOnly, cloud_replica
from test_storage_routing import replicas


def split_package(db, tmp):
    video, part, original, source = seed_media(db, tmp)
    original.video_codec, original.audio_codec = 'hevc', 'eac3'
    original.metadata_json = {'dolby_vision': True, 'dolby_atmos': True, 'archive_stream_copy': True}
    assets = []
    for number, payload in enumerate([b'initialization', b'first-fragment-content', b'later-fragment-content']):
        path = tmp / f'fragment-{number}'
        path.write_bytes(payload)
        assets.append(ingest_file(db, path, kind='hls_init' if number == 0 else 'hls_segment',
                                 mime_type='video/mp4' if number == 0 else 'video/iso.segment'))
    index = {'schema': 'treasure.hls.v1', 'stream_copy': True, 'source_asset_id': source.id,
             'init_asset_id': assets[0].id, 'target_duration': 6,
             'segments': [{'sequence': number, 'duration': 6, 'asset_id': asset.id}
                          for number, asset in enumerate(assets[1:])]}
    path = tmp / 'split-package.json'
    path.write_text(json.dumps(index))
    index_asset = ingest_file(db, path, kind='hls_index', mime_type='application/json')
    package = MediaVariant(part_id=part.id, asset_id=index_asset.id, kind='hls', format_key='split',
        metadata_json={'source_variant_id': original.id, 'source_asset_id': source.id, 'stream_copy': True})
    db.add(package); db.commit()
    return part, original, source, package, index_asset, assets


def test_auto_uses_same_original_when_requested_cloud_node_has_no_hls(context, monkeypatch):
    client, db, tmp = context
    part, original, source, package, _, _ = split_package(db, tmp)
    profile = cloud_replica(db, source)
    signer = SigningOnly()
    monkeypatch.setattr('app.storage.delivery.get_adapter', lambda _: signer)
    response = client.post('/api/v1/playback-sessions', json={'part_id': part.id, 'route_id': profile.id})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['protocol'] == 'file' and result['direct']
    assert result['variant_id'] == result['source_variant_id'] == original.id
    assert result['selected_route_id'] == profile.id
    assert result['media']['video_codec'] == 'hevc' and result['media']['audio_codec'] == 'eac3'
    assert result['media']['dolby_vision'] and result['media']['dolby_atmos']
    assert len(signer.signed) == 1
    # An explicit HLS request must not silently change its requested transport.
    assert client.post('/api/v1/playback-sessions', json={
        'part_id': part.id, 'route_id': profile.id, 'protocol': 'hls'}).status_code == 503
    # Automatic selection can still use the complete local HLS package.
    automatic = client.post('/api/v1/playback-sessions', json={'part_id': part.id}).json()
    assert automatic['protocol'] == 'hls' and automatic['variant_id'] == package.id


def test_auto_can_play_original_when_packaged_index_is_unavailable(context):
    client, db, tmp = context
    part, original, _, _, index_asset, _ = split_package(db, tmp)
    resolve_asset(db, index_asset.id)['path'].unlink()
    response = client.post('/api/v1/playback-sessions', json={'part_id': part.id})
    assert response.status_code == 200, response.text
    assert response.json()['protocol'] == 'file'
    assert response.json()['source_variant_id'] == original.id
    assert client.post('/api/v1/playback-sessions', json={'part_id': part.id, 'protocol': 'hls'}).status_code == 503


def test_first_hls_fragment_never_reads_large_archive_or_later_fragments(context, monkeypatch):
    from app.playback import hls
    client, db, tmp = context
    part, _, source, _, index_asset, assets = split_package(db, tmp)
    # Model a very large verified archive without creating a giant test file.
    # Removing it and a later fragment must not delay the first available one.
    resolve_asset(db, source.id)['path'].unlink()
    resolve_asset(db, assets[-1].id)['path'].unlink()
    source.size = 500 * 1024**3
    db.commit()
    reads, heads = [], []
    read_index, local_head = hls.read_asset_bytes, LocalStorage.head
    def bounded_index(db, asset_id, **kwargs):
        assert asset_id == index_asset.id
        reads.append(asset_id)
        return read_index(db, asset_id, **kwargs)
    def observe_head(store, key, **kwargs):
        heads.append(key)
        return local_head(store, key, **kwargs)
    monkeypatch.setattr(hls, 'read_asset_bytes', bounded_index)
    monkeypatch.setattr(LocalStorage, 'head', observe_head)
    response = client.post('/api/v1/playback-sessions', json={'part_id': part.id})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['protocol'] == 'hls'
    assert client.get(data['url']).status_code == 200
    first = client.get(f"/api/v1/playback-sessions/{data['id']}/assets/{assets[1].id}",
                       headers={'Range': 'bytes=0-4'})
    assert first.status_code == 206 and first.content == b'first'
    assert reads == [index_asset.id]
    first_key = db.scalar(select(AssetLocation.object_key).where(AssetLocation.asset_id == assets[1].id))
    assert heads == [first_key]


@pytest.mark.parametrize('protocol', ['auto', 'file'])
def test_session_retains_same_source_measurements_when_metadata_is_null(context, protocol):
    client, db, tmp = context
    part, original, _, package, _, _ = split_package(db, tmp)
    video = db.get(Video, part.video_id)
    measurements = {'fps': 59.94, 'video_bitrate_bps': 8_000_000,
                    'audio_bitrate_bps': 192_000, 'total_bitrate_bps': 8_210_000}
    video.metadata_json = {'media_properties': {original.id: measurements}}
    missing = dict.fromkeys(measurements)
    original.metadata_json = {**original.metadata_json, **missing}
    package.metadata_json = {**package.metadata_json, **missing, 'dolby_atmos': False}
    db.commit()
    result = client.post('/api/v1/playback-sessions', json={
        'part_id': part.id, 'variant_id': original.id, 'protocol': protocol}).json()
    assert result['source_variant_id'] == original.id
    assert {key: result['media'][key] for key in measurements} == measurements
    if protocol == 'auto':
        assert result['media']['dolby_atmos'] is False
    detail = client.get(f'/api/v1/videos/{video.id}').json()
    assert {key: detail['media_properties'][original.id][key] for key in measurements} == measurements


def test_old_archive_average_uses_original_asset_not_hls_index_or_other_rendition(context):
    client, db, tmp = context
    part, original, source, _, index, _ = split_package(db, tmp)
    video = db.get(Video, part.video_id)
    compatible = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id, MediaVariant.kind == 'playback'))
    video.metadata_json = {'media_properties': {compatible.id: {
        'fps': 60, 'video_bitrate_bps': 33_000_000, 'audio_bitrate_bps': 192_000, 'total_bitrate_bps': 33_210_000}}}
    db.commit()
    result = client.post('/api/v1/playback-sessions', json={'part_id': part.id, 'variant_id': original.id}).json()
    media = result['media']
    assert result['protocol'] == 'hls' and result['source_variant_id'] == original.id
    assert media['total_bitrate_bps'] == source.size * 8 / original.duration
    assert source.size != index.size
    assert media['total_bitrate_estimated'] and media['total_bitrate_includes_container']
    assert all(media.get(key) is None for key in ('fps', 'video_bitrate_bps', 'audio_bitrate_bps'))


def test_compatibility_hls_only_uses_its_own_source_measurements(context):
    client, db, tmp = context
    part, original, _, package, _, _ = split_package(db, tmp)
    compatible = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id, MediaVariant.kind == 'playback'))
    path = tmp / 'different-compatibility.mp4'
    path.write_bytes(b'separate compatibility media')
    asset = ingest_file(db, path, kind='media', mime_type='video/mp4')
    compatible.asset_id, compatible.duration = asset.id, 12
    compatible.video_codec, compatible.audio_codec = 'h264', 'aac'
    compatible.metadata_json = {'audio_bitrate_bps': 192_000, 'dolby_atmos': False}
    package.video_codec, package.audio_codec = 'h264', 'aac'
    package.metadata_json = {'source_variant_id': compatible.id, 'source_asset_id': asset.id, 'stream_copy': True}
    db.get(Video, part.video_id).metadata_json = {'media_properties': {original.id: {
        'fps': 120, 'video_bitrate_bps': 8_000_000, 'audio_bitrate_bps': 1_000_000, 'total_bitrate_bps': 9_000_000}}}
    db.commit()
    result = client.post('/api/v1/playback-sessions', json={'part_id': part.id, 'variant_id': compatible.id}).json()
    assert result['source_variant_id'] == compatible.id and result['protocol'] == 'hls'
    assert result['media']['audio_bitrate_bps'] == 192_000 and result['media']['dolby_atmos'] is False
    assert result['media']['total_bitrate_bps'] == asset.size * 8 / compatible.duration
    assert result['media'].get('fps') is None and result['media'].get('video_bitrate_bps') is None


@pytest.mark.parametrize('failure', [None, 404, 403, 503, 'wrong-size', 'directory', 'unsafe-url'])
def test_graph_playback_has_one_metadata_request_and_preserves_failover(replicas, monkeypatch, failure):
    db, asset, first, second = replicas
    profile = StorageProfile(name='Graph', kind='onedrive', config={'drive_id': 'fixture'}, enabled=True)
    db.add(profile); db.flush()
    db.add(AssetLocation(asset_id=asset.id, storage_profile_id=profile.id, object_key='fixture/video.mp4',
                         state='ready', verified_at=utcnow()))
    session = PlaybackSession(user_id='test', variant_id='test', protocol='file', profile_id=profile.id,
                              expires_at=utcnow() + timedelta(hours=1), state={})
    db.add(session); db.flush()
    requests = []
    def respond(request):
        requests.append(request)
        assert request.method == 'GET' and request.url.host == 'graph.microsoft.com'
        if isinstance(failure, int):
            return httpx.Response(failure, json={'error': 'fixture unavailable'})
        body = {'file': {}, 'size': asset.size + (1 if failure == 'wrong-size' else 0),
                '@microsoft.graph.downloadUrl': 'https://fixture.sharepoint.com/download?auth=fixture'}
        if failure == 'directory':
            body.pop('file')
        if failure == 'unsafe-url':
            body['@microsoft.graph.downloadUrl'] = 'https://evil.invalid/file'
        return httpx.Response(200, json=body)
    adapter = GraphStorage('onedrive', profile.config, {}, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(adapter, '_token', lambda: 'fixture-access')
    monkeypatch.setattr(routing, 'get_adapter', lambda node: adapter if node.id == profile.id else get_adapter(node))
    result = routing.resolve_session_asset(db, session, asset.id, frozenset([asset.id]))
    assert len(requests) == 1  # No preceding HEAD / duplicate Graph GET.
    if failure is None:
        assert result['profile_id'] == profile.id and result['kind'] == 'onedrive'
        assert session.state == {}
    else:
        assert result['profile_id'] in {first.id, second.id} and result['kind'] == 'local'
        assert session.state['failed_profile_ids'] == [profile.id] and session.state['switch_count'] == 1
