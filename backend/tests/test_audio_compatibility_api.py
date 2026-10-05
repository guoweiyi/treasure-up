"""API contracts for explicit stereo copies and matching playback sessions."""
import json

import pytest
from sqlalchemy import func, select

from app.models import Job, MediaVariant, Setting
from app.storage.service import ingest_file
from test_api import context, login
from test_delivery_api import seed_media


def test_only_admin_can_request_a_copy_and_repeated_requests_keep_job_state(context):
    client, db, tmp = context
    _, part, original, _ = seed_media(db, tmp)
    url = f'/api/v1/admin/variants/{original.id}/compatible'
    assert client.post(url).status_code == 401
    login(client, 'reader')
    assert client.post(url).status_code == 403
    login(client)
    db.add(Setting(key='ingest', value={'max_download_bytes':2000000})); db.commit()
    response = client.post(url)
    assert response.status_code == 202
    job = db.get(Job, response.json()['id'])
    assert job.kind == 'create_playback' and job.target_id == original.id
    assert job.policy['max_download_bytes'] == 2000000
    assert client.post(url).json()['id'] == job.id
    job.status = 'paused'; db.commit()
    assert client.post(url).json()['status'] == 'paused'
    job.status = 'failed'; db.commit()
    assert client.post(url).json()['status'] == 'failed'
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    copy = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id, MediaVariant.kind == 'playback'))
    assert client.post(f'/api/v1/admin/variants/{copy.id}/compatible').status_code == 422


def test_new_profile_can_follow_old_unsupported_result_without_resetting_it(context):
    client, db, tmp = context
    _, _, original, _ = seed_media(db, tmp)
    old = Job(kind='create_playback',target_id=original.id,dedupe_key='playback:'+original.id,
              status='succeeded',result={'compatibility':'unsupported','reason':'hdr_conversion_unsupported'})
    db.add(old); db.commit(); login(client)
    response = client.post(f'/api/v1/admin/variants/{original.id}/compatible')
    assert response.status_code == 202 and response.json()['id'] != old.id
    db.refresh(old)
    assert old.status == 'succeeded' and old.result['compatibility'] == 'unsupported'


@pytest.mark.parametrize('protocol', ['file', 'hls'])
def test_stereo_playback_uses_its_own_assets_and_never_inherits_atmos(context, protocol):
    client, db, tmp = context
    video, part, original, _ = seed_media(db, tmp, hls=True)
    original.audio_codec = 'eac3'; original.video_codec = 'hevc'
    original.metadata_json = {'dolby_atmos':True,'dolby_vision':True,'audio_channels':8}
    path = tmp / 'stereo.mp4'; path.write_bytes(b'stereo fixture, different bytes')
    asset = ingest_file(db, path, kind='media', mime_type='video/mp4')
    copy = MediaVariant(part_id=part.id,asset_id=asset.id,kind='playback',format_key='audio-only',
        video_codec='hevc',audio_codec='aac',metadata_json={'source_variant_id':original.id,
        'source_asset_id':original.asset_id,'compatibility_mode':'audio_only','video_stream_copy':True,
        'audio_transcoded':True,'audio_channels':2,'dolby_vision':True,'dolby_atmos':False,'ec3':{}})
    db.add(copy); db.flush()
    package_id = None
    if protocol == 'hls':
        index = {'schema':'treasure.hls.v1','stream_copy':True,'source_asset_id':asset.id,
            'init_asset_id':asset.id,'target_duration':6,'segments':[{'sequence':0,'duration':6,'asset_id':asset.id}]}
        path = tmp / 'stereo-index.json'; path.write_text(json.dumps(index))
        package_asset = ingest_file(db,path,kind='hls_index',mime_type='application/json')
        package = MediaVariant(part_id=part.id,asset_id=package_asset.id,kind='hls',format_key='stereo-hls',
            video_codec='hevc',audio_codec='aac',metadata_json={**copy.metadata_json,
            'source_variant_id':copy.id,'source_asset_id':asset.id})
        db.add(package); db.flush(); package_id = package.id
    db.commit()
    response = client.post('/api/v1/playback-sessions',json={'part_id':part.id,'variant_id':copy.id,'protocol':protocol})
    assert response.status_code == 200
    value = response.json()
    assert value['source_variant_id'] == copy.id and value['variant_id'] == (package_id or copy.id)
    assert value['media']['audio_codec'] == 'aac' and value['media']['audio_channels'] == 2
    assert value['media']['dolby_atmos'] is False and value['media']['dolby_vision'] is True
    assert value['media']['ec3'] == {}
    assert client.get(value['url']).status_code == 200
    assert client.get(f'/api/v1/playback-sessions/{value["id"]}/assets/{original.asset_id}').status_code == 404
