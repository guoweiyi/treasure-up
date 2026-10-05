"""Card payloads omit media internals while status/feature semantics survive."""
import json

from app import catalog
from app.capture_state import latest_capture_jobs
from app.models import Job
from test_api import context, login, seed_catalog


def test_card_endpoint_is_small_and_full_detail_remains_available(context):
    client, db, _ = context
    video, part, _ = seed_catalog(db)
    video.description = "long source description " * 5000
    video.metadata_json = {"is_upower_exclusive": True, "source_quality": {"formats": ["source-spec"] * 5000},
        "media_properties": {"source": {"dolby_atmos": True, "dolby_vision": True, "evidence": ["packet"] * 5000}}}
    db.commit()
    full = client.get('/api/v1/videos').json()['items'][0]
    cards = client.get('/api/v1/videos?view=card')
    assert cards.status_code == 200
    card = cards.json()['items'][0]
    assert card['description'] == ''
    assert not {'source_title', 'source_quality', 'media_properties', 'notes', 'capture_runs'} & card.keys()
    for field in ('id', 'title', 'cover_url', 'creators', 'stats', 'playable', 'capture_status', 'parts_count', 'content_features'):
        assert card[field] == full[field]
    assert all(card['content_features'].values())
    assert len(json.dumps(card)) < len(json.dumps(full)) / 20
    detail = client.get(f'/api/v1/videos/{video.id}').json()
    assert detail['description'] == video.description and detail['media_properties']['source']['dolby_atmos']
    login(client)
    assert client.put(f'/api/v1/videos/{video.id}/star', json={'starred': True}).status_code == 200
    assert client.get('/api/v1/videos?view=card').json()['items'][0]['starred'] is True
    assert client.get('/api/v1/videos?view=unknown').status_code == 422


def test_catalog_does_not_load_comment_work_checkpoint_and_keeps_false_policy(context):
    _, db, _ = context
    video, part, _ = seed_catalog(db)
    job = Job(kind='archive_video', target_id=video.id, dedupe_key='card-checkpoint', status='succeeded',
              policy={'download_media': False, 'private_unused': 'x' * 10000},
              checkpoint={'part_ids': [part.id], 'media_parts': [part.id], 'comments': {'candidates': ['large'] * 20000}})
    db.add(job); db.commit()
    projected = latest_capture_jobs(db, [video])[video.id]['archive_video']
    assert projected.policy == {'download_media': False}
    assert projected.checkpoint == {'part_ids': [part.id], 'media_parts': [part.id]}
    assert catalog.video_card_views(db, [video])[0]['ingest_state']['media'] == 'disabled'
