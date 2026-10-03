import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import get_db
from app.main import app
from app.models import (Base, Comment, Creator, DanmakuSnapshot, MediaVariant, PlatformUser, Setting,
                        StorageProfile, User, UserSnapshot, Video, VideoAnnotation, VideoCreator, VideoPart)
from app.security import hash_password
from app.storage.service import ingest_file


@pytest.fixture
def context(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "secret_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    monkeypatch.setattr(settings, "x_accel_prefix", "")
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([User(username="admin", password_hash=hash_password("a-test-password-123"), role="admin"),
                    User(username="reader", password_hash=hash_password("a-test-password-123"), role="reader"),
                    StorageProfile(name="Local", kind="local", config={"root": str(tmp_path / "media")}, is_default=True)])
        db.commit()
        def dependency():
            yield db
        app.dependency_overrides[get_db] = dependency
        with TestClient(app, base_url="http://localhost") as client:
            yield client, db, tmp_path
        app.dependency_overrides.clear()
    engine.dispose()


def login(client, name="admin"):
    result = client.post("/api/v1/auth/login", json={"username": name, "password": "a-test-password-123"})
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result.json()


def test_authentication_csrf_and_reader_permissions(context):
    client, db, _ = context
    assert client.get("/api/v1/videos").status_code == 401
    login(client, "reader")
    assert client.get("/api/v1/videos").status_code == 200
    assert client.get("/api/v1/admin/accounts").status_code == 403
    assert client.post("/api/v1/admin/jobs", json={"kind": "archive_video", "target_id": "BV1234567890"}).status_code == 403
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/auth/logout").status_code == 403
    client.headers["X-CSRF-Token"] = client.get("/api/v1/auth/me").json()["csrf_token"]
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_origin_and_failure_throttle(context, monkeypatch):
    client, _, _ = context
    monkeypatch.setattr(settings, "login_limit", 2)
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "a-test-password-123"},
                       headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    for _ in range(2):
        assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "incorrect"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "a-test-password-123"}).status_code == 429


def seed_catalog(db):
    user = PlatformUser(uid="9007199254740993", display_name="新的昵称", signature="摄影与记录")
    video = Video(bvid="BV1234567890", aid="9007199254740994", title="原始标题", description="原始简介")
    db.add_all([user, video]); db.flush()
    creator = Creator(user_id=user.id)
    snapshot = UserSnapshot(user_id=user.id, display_name="旧昵称")
    part = VideoPart(video_id=video.id, cid="9007199254740995", title="第一部分")
    db.add_all([creator, snapshot, part]); db.flush()
    db.add(VideoCreator(video_id=video.id, creator_id=creator.id, role="owner"))
    root = Comment(video_id=video.id, rpid="123", content="测试顶层", author_user_id=user.id,
                   author_snapshot_id=snapshot.id, reply_count=1)
    reply = Comment(video_id=video.id, rpid="124", root_rpid="123", parent_rpid="123", content="回复内容")
    db.add_all([root, reply]); db.commit()
    return video, part, creator


def test_metadata_overrides_snapshot_search_and_comment_tree(context):
    client, db, _ = context
    login(client)
    video, part, creator = seed_catalog(db)
    response = client.patch(f"/api/v1/admin/videos/{video.id}", json={"title_override": "", "description_override": "人工简介", "tags": ["摄影"]})
    assert response.status_code == 200, response.text
    assert response.json()["title"] == ""
    video.title = "源站更新后的标题"; db.commit()
    assert client.get(f"/api/v1/videos/{video.id}").json()["title"] == ""
    client.patch(f"/api/v1/admin/videos/{video.id}", json={"title_override": None})
    assert client.get(f"/api/v1/videos/{video.id}").json()["title"] == "源站更新后的标题"
    assert client.get("/api/v1/creators", params={"q": "旧昵称"}).json()["items"][0]["id"] == creator.id
    assert client.get("/api/v1/videos", params={"creator_id": creator.id, "q": "源站"}).json()["total"] == 1
    roots = client.get(f"/api/v1/videos/{video.id}/comments").json()
    assert roots["total"] == 1 and roots["items"][0]["author"]["name"] == "旧昵称"
    assert roots["items"][0]["author"]["uid"] == "9007199254740993"
    assert client.get(f"/api/v1/videos/{video.id}/comments", params={"root": "123"}).json()["items"][0]["content"] == "回复内容"


def test_private_assets_range_playback_and_danmaku_modes(context):
    client, db, tmp = context
    video, part, _ = seed_catalog(db)
    media = tmp / "clip.mp4"; media.write_bytes(bytes(range(100)))
    asset = ingest_file(db, media, kind="video", mime_type="video/mp4")
    db.add(MediaVariant(part_id=part.id, asset_id=asset.id, format_key="test", kind="playback"))
    data = tmp / "danmaku.json"
    data.write_text(json.dumps([{"text": "滚动", "time": 1, "mode": 1}, {"text": "顶部", "time": 2, "mode": 5},
                                {"text": "底部", "time": 3, "mode": 4}, {"text": "BAS", "time": 4, "mode": 9}]), encoding="utf-8")
    da = ingest_file(db, data, kind="danmaku", mime_type="application/json")
    db.add(DanmakuSnapshot(part_id=part.id, run_id="test", data_asset_id=da.id, status="complete")); db.commit()
    url = f"/api/v1/assets/{asset.id}"
    assert client.get(url).status_code == 401
    login(client)
    result = client.get(url, headers={"Range": "bytes=10-19"})
    assert result.status_code == 206 and result.content == bytes(range(10, 20))
    assert result.headers["content-range"] == "bytes 10-19/100"
    assert client.head(url).headers["content-length"] == "100"
    assert client.get(url, headers={"Range": "bytes=999-"}).status_code == 416
    playback = client.post("/api/v1/playback-sessions", json={"part_id": part.id})
    assert playback.status_code == 200, playback.text
    assert playback.json()["protocol"] == "file"
    assert client.get(playback.json()["url"], headers={"Range": "bytes=10-19"}).content == bytes(range(10, 20))
    assert [v["mode"] for v in client.get(playback.json()["danmaku_url"]).json()] == [0, 1, 2]
    result = client.put(f"/api/v1/progress/{part.id}", json={"position": 22.5, "duration": 100})
    assert result.status_code == 200
    assert client.get(f"/api/v1/progress/{part.id}").json()["position"] == 22.5


def test_admin_secrets_defaults_pagination_and_job_validation(context):
    client, db, tmp = context
    login(client)
    body = {"name": "测试账号", "cookie": "SESSDATA=test-placeholder-not-a-real-cookie"}
    created = client.post("/api/v1/admin/accounts", json=body)
    assert created.status_code == 201, created.text
    assert "SESSDATA" not in created.text and "secret_encrypted" not in created.text
    assert "SESSDATA" not in client.get("/api/v1/admin/accounts").text
    source = client.post("/api/v1/admin/sources", json={"source_id": "12345", "title": "收藏", "account_id": created.json()["id"]})
    assert source.status_code == 201 and source.json()["enabled"] is False
    job = client.post(f"/api/v1/admin/sources/{source.json()['id']}/scan").json()
    assert job["status"] == "queued" and job["target_id"] == source.json()["collection_id"]
    assert client.post(f"/api/v1/admin/jobs/{job['id']}/pause").json()["status"] == "paused"
    assert client.post(f"/api/v1/admin/jobs/{job['id']}/resume").json()["status"] == "queued"
    profile = client.get("/api/v1/admin/storage").json()["items"][0]
    assert client.post(f"/api/v1/admin/storage/{profile['id']}/migrate", json={"target_profile_id": profile["id"], "asset_ids": []}).status_code == 422
    assert client.post("/api/v1/admin/storage", json={"name": "invalid", "kind": "local", "config": {"root": str(tmp)}}).status_code == 422
    assert client.post("/api/v1/admin/backups").status_code == 409
    assert client.get("/api/v1/admin/accounts", params={"page": 2, "page_size": 1}).json()["items"] == []
    assert client.get("/api/v1/library/settings").json() == {"site_name": "Treasure Up", "default_danmaku": True}
    assert client.patch("/api/v1/admin/settings", json={"display": {"site_name": "小云的片库", "default_danmaku": False}}).status_code == 200
    assert client.get("/api/v1/library/settings").json()["site_name"] == "小云的片库"


def test_cloud_head_does_not_redirect_to_get_signed_url(context, monkeypatch):
    from app.storage import service
    client, db, tmp = context
    payload = tmp / "blob"; payload.write_bytes(b"hello")
    asset = ingest_file(db, payload, kind="video", mime_type="video/mp4"); db.commit()
    login(client)
    monkeypatch.setattr(service, "resolve_asset", lambda *a: {"kind": "s3", "url": "https://private.invalid/?signature=test", "size": 5, "mime_type": "video/mp4"})
    result = client.head(f"/api/v1/assets/{asset.id}")
    assert result.status_code == 200 and result.headers["content-length"] == "5"
    assert "location" not in result.headers
