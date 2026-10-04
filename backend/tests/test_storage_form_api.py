import json

import pytest
from sqlalchemy import func, select

from app.models import Asset, AssetLocation, StorageProfile
from app.security import decrypt_secret, encrypt_secret
from test_api import context, login


def test_storage_credentials_preserved_rotated_and_cleared_by_type(context):
    client, db, tmp = context
    login(client)
    body = {"name": "Cloud", "kind": "s3", "config": {"bucket": "fixture-bucket"}}
    assert client.post("/api/v1/admin/storage", json=body).status_code == 422
    created = client.post("/api/v1/admin/storage", json={**body, "credentials": {
        "access_key_id": "test-key", "secret_access_key": "test-secret"}})
    assert created.status_code == 201, created.text
    assert "test-secret" not in created.text
    identifier = created.json()["id"]
    original = db.get(StorageProfile, identifier).secret_encrypted
    url = f"/api/v1/admin/storage/{identifier}"
    assert client.patch(url, json={**body, "name": "Renamed"}).status_code == 200
    assert db.get(StorageProfile, identifier).secret_encrypted == original
    assert client.patch(url, json={**body, "credentials": {"access_key_id": "replacement"}}).status_code == 422
    assert db.get(StorageProfile, identifier).secret_encrypted == original
    assert client.patch(url, json={**body, "credentials": {
        "access_key_id": "replacement", "secret_access_key": "replacement-secret"}}).status_code == 200
    assert json.loads(decrypt_secret(db.get(StorageProfile, identifier).secret_encrypted))["access_key_id"] == "replacement"
    oss = {"name": "OSS", "kind": "oss", "config": {"endpoint": "https://oss-cn-hangzhou.aliyuncs.com", "bucket": "fixture-bucket"}}
    assert client.patch(url, json=oss).status_code == 422
    assert client.patch(url, json={**oss, "credentials": {
        "access_key_id": "oss-key", "access_key_secret": "oss-test-secret"}}).status_code == 200
    assert client.patch(url, json={"name": "Local", "kind": "local", "config": {"root": str(tmp / "media" / "alternate")}}).status_code == 200
    assert db.get(StorageProfile, identifier).secret_encrypted is None


@pytest.mark.parametrize('field', ['endpoint', 'public_endpoint'])
@pytest.mark.parametrize('endpoint', [
    'https://user:secret@example.test/path', 'https://[broken',
    'https://example.test:not-a-port', 'https://example.test:65536',
    'https://example.test:0', 'https://example.test:-1',
])
def test_storage_form_rejects_malformed_endpoint_without_persisting(context, field, endpoint):
    client, db, _ = context
    login(client)
    before = db.scalar(select(func.count()).select_from(StorageProfile))
    body = {"name": "Invalid", "kind": "s3", "config": {"bucket": "fixture-bucket", field: endpoint},
            "credentials": {"access_key_id": "test", "secret_access_key": "test"}}
    response = client.post("/api/v1/admin/storage", json=body)
    assert response.status_code == 422
    assert "user:secret" not in response.text
    assert db.scalar(select(func.count()).select_from(StorageProfile)) == before


def test_legacy_null_cloud_endpoints_can_be_renamed_with_existing_assets(context):
    client, db, _ = context
    login(client)
    config = {'bucket': 'fixture-bucket', 'endpoint': None, 'public_endpoint': None}
    profile = StorageProfile(name='Legacy', kind='s3', config=config,
                             secret_encrypted=encrypt_secret(json.dumps({'access_key_id': 'test', 'secret_access_key': 'test'})))
    asset = Asset(sha256='f' * 64, size=1)
    db.add_all([profile, asset]); db.flush()
    location = AssetLocation(asset_id=asset.id, storage_profile_id=profile.id, object_key='existing-object')
    db.add(location); db.commit()
    previous_secret = profile.secret_encrypted
    response = client.patch(f'/api/v1/admin/storage/{profile.id}', json={
        'name': 'Renamed', 'kind': 's3', 'config': config})
    assert response.status_code == 200, response.text
    assert response.json()['config'] == config
    db.refresh(profile)
    assert profile.config == config and profile.secret_encrypted == previous_secret
    assert db.get(AssetLocation, location.id).object_key == 'existing-object'


@pytest.mark.parametrize('endpoint', ['http://127.0.0.1:9000', 'https://[::1]:443'])
def test_storage_endpoint_accepts_valid_explicit_port(context, endpoint):
    client, _, _ = context
    login(client)
    response = client.post('/api/v1/admin/storage', json={
        'name': 'Cloud', 'kind': 's3', 'config': {'bucket': 'fixture-bucket', 'endpoint': endpoint},
        'credentials': {'access_key_id': 'test', 'secret_access_key': 'test'}})
    assert response.status_code == 201
    assert response.json()['config']['endpoint'] == endpoint
