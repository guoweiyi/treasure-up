import hashlib

import httpx
import pytest

from app.ingest import runner
from app.ingest.client import BiliClient
from app.ingest.errors import IngestError
from test_ingest_refresh import db, setup


@pytest.mark.parametrize('data', [[], {}, {'isLogin': 'false'}, {'isLogin': 0},
    {'isLogin': True}, {'isLogin': True, 'mid': True}, {'isLogin': True, 'mid': 0},
    {'isLogin': True, 'mid': '１２３'}, {'isLogin': True, 'mid': '9' * 5000}])
def test_incomplete_nav_is_not_a_logout(data):
    client = BiliClient('SESSDATA=fixture', transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'code': 0, 'data': data})))
    try:
        with pytest.raises(IngestError) as caught: client.nav()
        assert caught.value.code == 'invalid_response'
    finally: client.close()


def test_not_found_preserves_endpoint_and_code_without_remote_message():
    client = BiliClient('SESSDATA=fixture', transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'code': -404, 'message': 'SECRET remote diagnostics'})))
    try:
        with pytest.raises(IngestError) as caught: client.view('BV1234567890')
        assert caught.value.code == 'not_found' and caught.value.source_code == -404
        assert caught.value.source_endpoint == '/x/web-interface/view'
        assert 'SECRET' not in str(caught.value)
    finally: client.close()


@pytest.mark.parametrize('verification,expected', [
    ({'code': 0, 'data': {'isLogin': True, 'mid': 1}}, 'endpoint_login_required'),
    ({'code': 0, 'data': {'isLogin': False}}, 'login_required'),
    ({'code': -101}, 'login_required'),
    ({'code': -352}, 'rate_limited'),
    ({'code': 0, 'data': {}}, 'invalid_response'),
])
def test_content_login_error_gets_one_identity_check_without_retry(verification, expected):
    paths = []
    def reply(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=verification if request.url.path.endswith('/nav') else {'code': -101})
    client = BiliClient('SESSDATA=fixture', transport=httpx.MockTransport(reply))
    try:
        with pytest.raises(IngestError) as caught: client.view('BV1234567890')
        assert caught.value.code == expected
        assert paths == ['/x/web-interface/view', '/x/web-interface/nav']
    finally: client.close()


def test_endpoint_login_failure_does_not_invalidate_valid_account(db, monkeypatch):
    paths = []
    def reply(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'code': 0, 'data': {'isLogin': True, 'mid': 1}}
                              if request.url.path.endswith('/nav') else {'code': -101})
    monkeypatch.setattr(runner, 'BiliClient', lambda *args, **kwargs: BiliClient(*args, **kwargs,
                        transport=httpx.MockTransport(reply)))
    account, video, job = setup(db)
    with pytest.raises(IngestError) as caught: runner.run_job(db, job)
    assert caught.value.code == 'endpoint_login_required'
    assert account.status == 'valid' and video.source_state == 'available'
    assert paths == ['/x/web-interface/nav', '/x/web-interface/view', '/x/web-interface/nav']


def test_confirmed_logout_invalidates_account_but_never_the_archived_video(db, monkeypatch):
    nav_calls = []
    def reply(request):
        if request.url.path.endswith('/nav'):
            nav_calls.append(request.url.path)
            return httpx.Response(200, json={'code': 0, 'data': {'isLogin': True, 'mid': 1}}
                                  if len(nav_calls) == 1 else {'code': -101})
        return httpx.Response(200, json={'code': -101})
    monkeypatch.setattr(runner, 'BiliClient', lambda *args, **kwargs: BiliClient(*args, **kwargs,
                        transport=httpx.MockTransport(reply)))
    account, video, job = setup(db)
    with pytest.raises(IngestError) as caught: runner.run_job(db, job)
    assert caught.value.code == 'login_required' and account.status == 'invalid'
    assert video.source_state == 'available' and video.capture_status == 'complete'
    assert video.source_availability['outcome'] == 'inconclusive'
    assert len(nav_calls) == 2


@pytest.mark.parametrize('initial', ['invalid', 'expired', 'disabled'])
def test_manual_verification_can_recheck_invalid_but_never_disabled(db, monkeypatch, initial):
    paths = []
    def reply(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'code': 0, 'data': {'isLogin': True, 'mid': 1}})
    monkeypatch.setattr(runner, 'BiliClient', lambda *args, **kwargs: BiliClient(*args, **kwargs,
                        transport=httpx.MockTransport(reply)))
    account, _, job = setup(db)
    account.status, job.kind = initial, 'verify_account'
    db.commit()
    if initial == 'disabled':
        with pytest.raises(IngestError) as caught: runner.run_job(db, job)
        assert caught.value.code == 'account_disabled' and account.status == 'disabled' and paths == []
    else:
        assert runner.run_job(db, job)['logged_in'] is True
        assert account.status == 'valid' and paths == ['/x/web-interface/nav']


def test_inflight_nav_cannot_reactivate_disabled_account(db):
    account, _, _ = setup(db)
    generation = (account.id, hashlib.sha256(account.secret_encrypted.encode()).hexdigest())
    account.status = 'disabled'
    db.commit()
    with pytest.raises(IngestError) as caught:
        runner.update_account_identity(db, account, generation, nav={'isLogin': True, 'mid': 1})
    assert caught.value.code == 'account_disabled' and account.status == 'disabled'
