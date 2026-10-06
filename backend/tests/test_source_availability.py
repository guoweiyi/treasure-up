from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.ingest import runner
from app.ingest.availability import HISTORY_LIMIT, observe
from app.ingest.errors import IngestError
from app.models import CaptureRun, Collection, CollectionItem, Job, VideoStatSnapshot
from test_ingest_refresh import Source, db, setup


def absent(**overrides):
    return IngestError('来源内容不可访问', **{
        'code': 'not_found', 'retryable': False, 'source_code': -404,
        'source_endpoint': '/x/web-interface/view', **overrides})


def next_refresh(db, account, video):
    job = Job(kind='refresh_stats', target_id=video.id, account_id=account.id, status='running',
              dedupe_key=str(uuid4()), policy={'interval_jitter_seconds': 0})
    db.add(job); db.commit()
    return job


def test_authoritative_absence_keeps_archive_stats_and_recovers_with_history(db, monkeypatch):
    class Missing(Source):
        def view(self, bvid): raise absent()
    monkeypatch.setattr(runner, 'BiliClient', Source)
    account, video, first = setup(db)
    runner.run_job(db, first)
    stats_id = db.scalar(select(VideoStatSnapshot.id))
    monkeypatch.setattr(runner, 'BiliClient', Missing)
    missing = next_refresh(db, account, video)
    missing.policy = {'refresh_danmaku': True, 'refresh_comments': True, 'refresh_tags': True}
    for stage in ('_comments', '_tags', '_danmaku', '_images', 'archive_media'):
        monkeypatch.setattr(runner, stage, lambda *args: pytest.fail('absent source must not run capture stages'))
    result = runner.run_job(db, missing)
    assert not result['continuation'] and result['source_check']['outcome'] == 'unavailable'
    assert video.source_state == 'unavailable'
    assert video.capture_status == 'complete' and video.metadata_json == {'kept': True}
    assert db.scalar(select(VideoStatSnapshot.id)) == stats_id
    assert db.scalar(select(func.count()).select_from(VideoStatSnapshot)) == 1
    assert db.get(CaptureRun, result['run_id']).end_reason == 'source_unavailable'
    assert runner.run_job(db, missing)['source_check'] == result['source_check']
    assert len(video.source_availability['history']) == 2
    monkeypatch.setattr(runner, 'BiliClient', Source)
    recovered = runner.run_job(db, next_refresh(db, account, video))
    assert recovered['source_check']['outcome'] == 'available' and video.source_state == 'available'
    assert [row['outcome'] for row in video.source_availability['history']] == ['available', 'unavailable', 'available']


@pytest.mark.parametrize('error', [
    IngestError('offline', code='network_error'),
    IngestError('expired', code='login_required'),
    IngestError('temporary gate', code='endpoint_login_required', blocked=True),
    IngestError('risk', code='rate_limited'),
    IngestError('paid entitlement', code='access_denied'),
    IngestError('HTTP 404', code='http_error'),
    IngestError('missing stats shape', code='invalid_stats'),
    IngestError('generic 404', code='not_found'),
    absent(source_endpoint='/x/v2/reply/wbi/main'),
    absent(source_code=-62002),
])
def test_inconclusive_checks_never_change_last_confirmed_state(db, error):
    account, video, job = setup(db)
    ctx = SimpleNamespace(db=db, run=SimpleNamespace(id='test-run'), cp={})
    for state in ('available', 'unavailable'):
        video.source_state = state
        assert observe(ctx, video, error=error) == 'inconclusive'
        assert video.source_state == state and video.capture_status == 'complete'
        assert ctx.cp['source_check']['reason'] == error.code


@pytest.mark.parametrize('paid_origin', ['metadata', 'subscription'])
def test_paid_not_found_is_not_deletion_evidence(db, paid_origin):
    account, video, job = setup(db)
    if paid_origin == 'metadata':
        video.metadata_json = {'access': {'upower_exclusive': True}}
    else:
        folder = Collection(source_id='fixture', title='Fixture')
        db.add(folder); db.flush()
        db.add(CollectionItem(collection_id=folder.id, source_resource_id='123', video_id=video.id,
                              observation={'paid': True}))
        db.flush()
    ctx = SimpleNamespace(db=db, run=SimpleNamespace(id='test-run'), cp={})
    assert observe(ctx, video, error=absent()) == 'inconclusive'
    assert video.source_state == 'available'
    assert video.source_availability['reason'] == 'paid_access_unconfirmed'


def test_check_history_is_bounded_and_deduplicates_same_run(db):
    _, video, _ = setup(db)
    for number in range(HISTORY_LIMIT + 5):
        ctx = SimpleNamespace(db=db, run=SimpleNamespace(id=str(number)), cp={})
        observe(ctx, video)
        observe(ctx, video)
    assert len(video.source_availability['history']) == HISTORY_LIMIT
    assert video.source_availability['history'][0]['run_id'] == '5'


def test_stats_nav_failure_records_inconclusive_without_invalidating_media(db, monkeypatch):
    class Offline(Source):
        def nav(self): raise IngestError('offline', code='network_error')
    monkeypatch.setattr(runner, 'BiliClient', Offline)
    account, video, job = setup(db)
    with pytest.raises(IngestError): runner.run_job(db, job)
    assert video.source_state == 'available' and video.capture_status == 'complete'
    assert video.source_availability['outcome'] == 'inconclusive'
    assert video.source_availability['reason'] == 'network_error'
