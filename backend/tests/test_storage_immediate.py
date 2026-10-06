from sqlalchemy import func, select

from app.models import Job, StorageProfile
from app.storage.local import LocalStorage
from test_api import context, login


def test_storage_probe_returns_checks_immediately_without_a_job(context):
    client, db, tmp = context
    login(client)
    profile = db.scalar(select(StorageProfile))
    before = db.scalar(select(func.count()).select_from(Job))
    response = client.post(f'/api/v1/admin/storage/{profile.id}/probe')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'passed' and all(body['checks'].values())
    assert body['elapsed_ms'] >= 0 and body['browser_cors'] == 'not_tested'
    assert 'job_id' not in body
    assert db.scalar(select(func.count()).select_from(Job)) == before
    assert not list((tmp / 'media' / 'probes').glob('*'))


def test_probe_failed_readback_still_deletes_its_file_and_redacts_errors(context, monkeypatch):
    client, db, tmp = context
    login(client)
    profile = db.scalar(select(StorageProfile))
    def failed_range(*args, **kwargs):
        raise RuntimeError('AKSK-CREDENTIAL-SHOULD-NOT-BE-RETURNED')
    monkeypatch.setattr(LocalStorage, 'read_range', failed_range)
    response = client.post(f'/api/v1/admin/storage/{profile.id}/probe')
    assert response.status_code == 200
    assert response.json()['status'] == 'failed'
    assert response.json()['checks']['delete']
    assert 'AKSK' not in response.text
    assert not list((tmp / 'media' / 'probes').glob('*'))


def test_probe_requires_administrator_and_csrf(context):
    client, db, _ = context
    profile = db.scalar(select(StorageProfile))
    url = f'/api/v1/admin/storage/{profile.id}/probe'
    assert client.post(url).status_code == 401
    login(client, 'reader')
    assert client.post(url).status_code == 403
    login(client)
    client.headers.pop('X-CSRF-Token')
    assert client.post(url).status_code == 403


def test_discovery_uses_saved_credentials_without_returning_them(context, monkeypatch):
    client, db, _ = context
    login(client)
    body = {'name': 'S3', 'kind': 's3', 'config': {'bucket': 'fixture'},
            'credentials': {'access_key_id': 'fixture-key', 'secret_access_key': 'fixture-secret'}}
    profile = client.post('/api/v1/admin/storage', json=body).json()
    def discover(kind, config, credentials):
        assert kind == 's3' and credentials['secret_access_key'] == 'fixture-secret'
        return {'items': [{'name': 'one', 'region': 'eu-west-1'}]}
    monkeypatch.setattr('app.storage.discovery.discover_storage', discover)
    response = client.post('/api/v1/admin/storage/discover', json={
        'kind': 's3', 'config': {}, 'profile_id': profile['id']})
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['name'] == 'one'
    assert 'fixture-secret' not in response.text and 'fixture-key' not in response.text


def test_discovery_failure_is_redacted_and_type_switch_cannot_reuse_credentials(context, monkeypatch):
    client, db, _ = context
    login(client)
    def fail(*args):
        raise RuntimeError('provider token=VERY-SECRET')
    monkeypatch.setattr('app.storage.discovery.discover_storage', fail)
    response = client.post('/api/v1/admin/storage/discover', json={
        'kind': 's3', 'config': {}, 'credentials': {'access_key_id': 'key', 'secret_access_key': 'secret'}})
    assert response.status_code == 422 and 'VERY-SECRET' not in response.text
    profile = db.scalar(select(StorageProfile))
    response = client.post('/api/v1/admin/storage/discover', json={
        'kind': 's3', 'config': {}, 'profile_id': profile.id})
    assert response.status_code == 422


def test_browser_delivery_probe_checks_range_cors_and_does_not_send_site_credentials(context, monkeypatch):
    import httpx
    from app.storage import probe
    monkeypatch.setattr('app.passkeys.site_policy', lambda: ('https://video.example.test', 'video.example.test'))
    payload = bytes(range(64))
    allowed = ['https://video.example.test']
    def serve(request):
        assert request.url.host == 'cloud.example.test'
        assert request.headers['origin'] == 'https://video.example.test'
        assert request.headers['range'] == 'bytes=17-53'
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(206, headers={'Content-Range': 'bytes 17-53/64',
                                           'Access-Control-Allow-Origin': allowed[0]}, content=payload[17:54])
    real_client = httpx.Client
    monkeypatch.setattr(probe.httpx, 'Client', lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(serve)))
    class Adapter:
        def presign(self, *args, **kwargs):
            return 'https://cloud.example.test/probe?signed=test'
    assert probe.browser_delivery_check(Adapter(), 'probe', None, payload)[0] == 'passed'
    allowed[0] = 'https://wrong.example.test'
    result = probe.browser_delivery_check(Adapter(), 'probe', None, payload)
    assert result[0] == 'failed' and 'CORS' in result[1]
