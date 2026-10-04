from datetime import datetime, timezone

from app.models import (Collection, CollectionItem, Comment, CommentAsset, MediaVariant,
                        Video, VideoCreator, VideoPart)
from app.schemas import IngestPolicy
from test_api import context, seed_catalog
from test_delivery_api import seed_media


def test_public_playlists_are_playable_ordered_deduplicated_and_scoped(context):
    client, db, tmp = context
    first, _, _, asset = seed_media(db, tmp)
    first.published_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    second = Video(bvid='BVsecond0001', title='Second', published_at=datetime(2021, 1, 1, tzinfo=timezone.utc))
    missing = Video(bvid='BVmissing001', title='Not downloaded')
    db.add_all([second, missing]); db.flush()
    part = VideoPart(video_id=second.id, cid='98765')
    db.add(part); db.flush()
    db.add(MediaVariant(part_id=part.id, asset_id=asset.id, format_key='second'))
    from sqlalchemy import select
    creator_id = db.scalar(select(VideoCreator.creator_id).where(VideoCreator.video_id == first.id))
    db.add_all([VideoCreator(video_id=second.id, creator_id=creator_id),
                VideoCreator(video_id=second.id, creator_id=creator_id, role='staff')])
    collection = Collection(source_id='1001', title='My list')
    other = Collection(source_id='1002', title='Other list')
    db.add_all([collection, other]); db.flush()
    db.add_all([CollectionItem(collection_id=collection.id, source_resource_id='1', video_id=second.id, position=1),
                CollectionItem(collection_id=collection.id, source_resource_id='2', video_id=first.id, position=2),
                CollectionItem(collection_id=collection.id, source_resource_id='3', video_id=second.id, position=3),
                CollectionItem(collection_id=collection.id, source_resource_id='4', video_id=missing.id, position=0)])
    db.commit()
    page = client.get('/api/v1/playlists', params={'collection_id':collection.id,'page_size':1}).json()
    assert page['scope_title'] == 'My list' and page['total'] == 2
    assert page['items'][0]['id'] == second.id
    assert 'notes' not in page['items'][0]
    next_page = client.get('/api/v1/playlists', params={'collection_id':collection.id,'page_size':1,'page':2}).json()
    assert next_page['items'][0]['id'] == first.id
    by_creator = client.get('/api/v1/playlists', params={'creator_id':creator_id}).json()
    assert [row['id'] for row in by_creator['items']] == [second.id, first.id]
    assert client.get('/api/v1/playlists', params={'collection_id':other.id}).json()['total'] == 0
    assert client.get('/api/v1/playlists').status_code == 422
    assert client.get('/api/v1/playlists', params={'collection_id':collection.id, 'creator_id':creator_id}).status_code == 422
    assert client.get('/api/v1/playlists?collection_id=missing').status_code == 404


def test_comment_emotes_are_inline_and_only_reference_linked_assets(context):
    client, db, tmp = context
    video, _, _, asset = seed_media(db, tmp)
    comment = Comment(video_id=video.id, rpid='100', content='Nice[笑] [坏]', raw={'asset_manifest':[
        {'purpose':'emote','token':'[笑]','asset_id':asset.id},
        {'purpose':'emote','token':'[坏]','asset_id':'unrelated-private-asset'},
        {'purpose':'emote','token':'[笑]','asset_id':asset.id}]})
    db.add(comment); db.flush()
    db.add(CommentAsset(comment_id=comment.id, asset_id=asset.id, kind='emote'))
    db.commit()
    def displayed():
        return next(row for row in client.get(f'/api/v1/videos/{video.id}/comments').json()['items'] if row['rpid'] == '100')
    row = displayed()
    assert row['images'] == []
    assert row['emotes'] == [{'text':'[笑]','asset_url':f'/api/v1/assets/{asset.id}'}]
    db.add_all([CommentAsset(comment_id=comment.id, asset_id=asset.id, kind='attachment'),
                CommentAsset(comment_id=comment.id, asset_id=asset.id, kind='image')])
    comment.raw = {'asset_manifest': comment.raw['asset_manifest'] + [
        {'purpose':'attachment','asset_id':asset.id}, {'purpose':'attachment','asset_id':asset.id}]}
    db.commit()
    assert displayed()['images'] == [f'/api/v1/assets/{asset.id}']
    comment.raw = {'content':{'emote':{'[笑]':{'url':'https://example.invalid/remote.png'}}}}; db.commit()
    # Legacy records without a verified mapping retain their text, never an
    # arbitrary remote image or an incorrectly paired local emoticon.
    assert displayed()['emotes'] == []
    assert displayed()['images'] == [f'/api/v1/assets/{asset.id}']
    comment.raw = {'asset_manifest': []}; db.commit()
    assert displayed()['images'] == []


def test_explicit_hls_and_file_sessions_describe_the_media_actually_selected(context):
    from sqlalchemy import select
    client, db, tmp = context
    video, part, original, _ = seed_media(db, tmp, hls=True)
    package = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id, MediaVariant.kind == 'hls'))
    original.width, original.height = 3840, 2160
    video.metadata_json = {'media_properties': {original.id: {'fps': 60, 'video_bitrate_bps': 5000000,
        'audio_bitrate_bps': 1000000, 'dolby_atmos': True}}}
    db.commit()
    for protocol in ('file', 'hls'):
        response = client.post('/api/v1/playback-sessions', json={'part_id':part.id, 'variant_id':package.id, 'protocol':protocol})
        assert response.status_code == 200
        session = response.json()
        assert session['source_variant_id'] == original.id
        assert session['media']['width'] == 3840 and session['media']['fps'] == 60
        assert session['media']['video_bitrate_bps'] == 5000000 and session['media']['dolby_atmos'] is True
        assert session['media']['segment_count'] == (1 if protocol == 'hls' else None)


def test_badges_require_source_entitlement_or_archived_dolby_evidence(context):
    client, db, tmp = context
    video, part, variant, _ = seed_media(db, tmp)
    variant.video_codec, variant.audio_codec = 'hevc', 'eac3'
    db.commit()
    url = f'/api/v1/videos/{video.id}'
    assert client.get(url).json()['content_features'] == {'charging_exclusive':False,'dolby_vision':False,'dolby_atmos':False}
    video.metadata_json = {'is_upower_exclusive': True, 'media_properties':{variant.id:{'dolby_vision':True,'dolby_atmos':True,
        'fps':23.976,'video_bitrate_bps':1734000,'audio_bitrate_bps':1025000}}}
    variant.width, variant.height = 3840, 2160
    db.commit()
    assert all(client.get(url).json()['content_features'].values())
    session = client.post('/api/v1/playback-sessions', json={'part_id':part.id,'protocol':'file'}).json()
    assert session['media']['mime_type'] == 'video/mp4'
    assert session['media']['segment_count'] is None
    assert session['media']['width'] == 3840 and session['media']['fps'] == 23.976
    assert session['media']['audio_bitrate_bps'] == 1025000
    video.metadata_json = {**video.metadata_json, 'access': {'upower_exclusive': False}}
    db.commit()
    assert client.get(url).json()['content_features']['charging_exclusive'] is False
    video.metadata_json = {'is_upower_exclusive': False, 'access': {'upower_exclusive': True}}
    db.commit()
    assert client.get(url).json()['content_features']['charging_exclusive'] is True


def test_comment_budget_defaults_are_bounded_and_validate_inputs():
    import pytest
    from pydantic import ValidationError
    policy = IngestPolicy()
    assert (policy.comment_top_limit, policy.comment_reply_total_limit, policy.comment_asset_bytes_limit) == (500,200,25*1024**2)
    for values in ({'comment_top_limit':-1}, {'comment_asset_bytes_limit':2**40}, {'asset_interval_seconds':0}):
        with pytest.raises(ValidationError):
            IngestPolicy(**values)
