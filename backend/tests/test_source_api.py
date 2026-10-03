import pytest
from app.models import CaptureRun, Collection
from app.source_links import resolve_source
from test_api import context, login


@pytest.mark.parametrize("value,kind,identifier", [
    ("https://space.bilibili.com/123/favlist?fid=456&ftype=create", "favorite", "456"),
    ("https://space.bilibili.com/123/upload/video", "creator", "123"),
    ("space.bilibili.com/123/", "creator", "123"),
    ("456", "favorite", "456"),
])
def test_supported_source_link(value, kind, identifier):
    assert resolve_source(value) == {"kind": kind, "source_id": identifier}


@pytest.mark.parametrize("value", ["file:///etc/passwd", "https://evil.invalid/?fid=12",
    "https://space.bilibili.com.evil.invalid/123", "https://user:password@space.bilibili.com/123",
    "https://space.bilibili.com:8080/123", "https://space.bilibili.com/123/favlist", "0"])
def test_resolver_does_not_follow_arbitrary_links(value):
    with pytest.raises(ValueError):
        resolve_source(value)


def test_sources_are_typed_and_monitoring_and_credentials_remain_private(context):
    client, db, _ = context
    login(client)
    account = client.post("/api/v1/admin/accounts", json={"name": "测试账号", "cookie": "synthetic-cookie-only"}).json()
    uid = "9007199254740993"
    source = client.post("/api/v1/admin/sources", json={"kind": "creator", "source_id": uid,
        "title": "UP订阅", "account_id": account["id"], "policy": {"initial_strategy": "new_only"}})
    assert source.status_code == 201, source.text
    source = source.json()
    assert source["kind"] == "creator" and source["monitor"] == {}
    collection = db.get(Collection, source["collection_id"])
    assert collection.kind == "creator" and collection.owner_uid == uid
    favorite = client.post("/api/v1/admin/sources", json={"kind": "favorite", "source_id": uid,
        "title": "收藏订阅", "account_id": account["id"]})
    assert favorite.status_code == 201  # Numeric IDs are scoped by source kind.
    assert client.post("/api/v1/admin/sources", json={"kind": "creator", "source_id": uid,
        "title": "重复", "account_id": account["id"]}).status_code == 409
    db.add(CaptureRun(collection_id=collection.id, status="complete", counts={"new_items": 2}, scope={}, checkpoint={}))
    db.commit()
    history = client.get(f'/api/v1/admin/sources/{source["id"]}/history').json()
    assert history["items"][0]["counts"]["new_items"] == 2
    scan = client.post(f'/api/v1/admin/sources/{source["id"]}/scan', json={"full": True})
    assert scan.status_code == 200 and scan.json()["policy"]["force_full_scan"] is True
    assert client.post(f'/api/v1/admin/sources/{source["id"]}/scan', json={}).status_code == 409
    updated = client.patch(f'/api/v1/admin/accounts/{account["id"]}', json={"cookie": "replacement-synthetic", "name": "已更新"})
    assert updated.status_code == 200 and "cookie" not in updated.json() and "secret_encrypted" not in updated.json()
    assert updated.json()["status"] == "unverified"
    client.post('/api/v1/auth/logout')
    login(client, "reader")
    assert client.post('/api/v1/admin/sources/resolve', json={"value": "123"}).status_code == 403
    assert client.patch(f'/api/v1/admin/accounts/{account["id"]}', json={"name": "no"}).status_code == 403
    assert client.get(f'/api/v1/admin/sources/{source["id"]}/history').status_code == 403


def test_public_server_contract_has_no_private_config(context):
    client, _, _ = context
    data = client.get('/api/v1/server').json()
    assert data['application'] == 'treasure-up' and data['api_version'] == 1
    assert set(data) == {'application', 'api_version', 'version', 'features', 'authentication'}


def test_password_login_accepts_only_explicit_external_tls_origin(context, monkeypatch):
    from app.config import settings
    client, _, _ = context
    monkeypatch.setattr(settings, 'passkey_origin', 'https://video.example.test')
    monkeypatch.setattr(settings, 'passkey_rp_id', 'video.example.test')
    payload = {'username': 'admin', 'password': 'a-test-password-123'}
    assert client.post('/api/v1/auth/login', json=payload, headers={'Origin': 'https://video.example.test'}).status_code == 200
    assert client.post('/api/v1/auth/login', json=payload, headers={'Origin': 'https://attacker.example.test'}).status_code == 403
