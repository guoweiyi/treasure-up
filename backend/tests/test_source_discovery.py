from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import source_discovery as discovery
from app.db import get_db
from app.ingest.errors import IngestError
from app.models import Base, Setting, SourceAccount, User, utcnow
from app.security import COOKIE_NAME, create_session


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(discovery.router)
    discovery._cache.clear()
    monkeypatch.setattr(discovery, "decrypt_secret", lambda _: "mock-cookie")
    calls, clients, reply = [], [], {"created": {"count": 0, "list": None},
        "collected": {"count": 0, "has_more": False, "list": []}, "following": {"total": 0, "list": []}}

    class Client:
        def __init__(self, secret, **kwargs):
            self.closed = False
            clients.append(self)
        def close(self):
            self.closed = True
        def nav(self):
            calls.append(("nav", {}))
            if isinstance(reply.get("nav"), Exception):
                raise reply["nav"]
            return {"isLogin": True, "mid": 123, "uname": "账号昵称"}
        def json(self, path, params):
            calls.append((path, params))
            category = "created" if "created/" in path else "collected" if "collected/" in path else "following" if "followings" in path else "info"
            result = reply[category]
            if isinstance(result, Exception):
                raise result
            return result
        def profile(self, uid):
            calls.append(("profile", {"mid": uid}))
            return reply["profile"]
    monkeypatch.setattr(discovery, "BiliClient", Client)
    with Session(engine, expire_on_commit=False) as db:
        account = SourceAccount(name="测试账号", secret_encrypted="ciphertext", status="valid")
        admin = User(username="admin", password_hash="unused", role="admin")
        reader = User(username="reader", password_hash="unused", role="reader")
        db.add_all([account, admin, reader]); db.flush()
        _, admin_token = create_session(db, admin)
        _, reader_token = create_session(db, reader)
        db.commit()
        def dependency():
            yield db
        app.dependency_overrides[get_db] = dependency
        with TestClient(app, base_url="http://localhost") as client:
            client.cookies.set(COOKIE_NAME, admin_token)
            yield client, db, account, reply, calls, clients, reader_token
    engine.dispose()
    discovery._cache.clear()


def request(context, category="created", **params):
    client, _, account, *_ = context
    return client.get("/api/v1/admin/source-discovery", params={"account_id": account.id, "category": category, **params})


def test_admin_required_and_private_responses_not_cached_by_browser(context):
    client, _, _, _, calls, _, reader_token = context
    assert request(context).headers["Cache-Control"] == "no-store"
    calls.clear()
    client.cookies.set(COOKIE_NAME, reader_token)
    assert request(context).status_code == 403
    client.cookies.clear()
    assert request(context).status_code == 401
    assert calls == []


def test_real_client_waits_between_nav_and_list_without_holding_account_lock(context, monkeypatch):
    import httpx
    from app.ingest import runner
    from app.ingest.client import BiliClient
    from app.ingest.throttle import AccountPacer
    clock, held, waits, paths = [utcnow()], [], [], []
    @contextmanager
    def lock(_db, name):
        assert not held
        held.append(name)
        try:
            yield
        finally:
            held.pop()
    def sleep(seconds):
        assert not held, "foreground list interval must release the worker's account mutex"
        waits.append(seconds)
        clock[0] += timedelta(seconds=seconds)
    def transport(incoming):
        assert held and incoming.headers.get("cookie") == "SESSDATA=fixture"
        paths.append(incoming.url.path)
        data = {"isLogin": True, "mid": "123", "uname": "fixture"} if incoming.url.path.endswith("/nav") else {"count": 0, "list": []}
        return httpx.Response(200, json={"code": 0, "data": data})
    monkeypatch.setattr(discovery, "_lock", lock)
    monkeypatch.setattr(runner, "_lock", lock)
    monkeypatch.setattr(discovery, "decrypt_secret", lambda _: "SESSDATA=fixture")
    monkeypatch.setattr(discovery, "BiliClient", lambda secret, **kwargs: BiliClient(secret, transport=httpx.MockTransport(transport), **kwargs))
    monkeypatch.setattr(discovery, "AccountPacer", lambda db, account, policy, guard, **kwargs:
        AccountPacer(db, account, policy, guard, clock=lambda: clock[0], sleep=sleep, **kwargs))
    assert request(context).status_code == 200
    assert paths == ["/x/web-interface/nav", "/x/v3/fav/folder/created/list-all"]
    assert sum(waits) == 3 and not held


@pytest.mark.parametrize("rotation", ["after_nav", "before_list", "after_list"])
def test_rotation_never_caches_old_identity_or_list_under_new_credentials(context, monkeypatch, rotation):
    import httpx
    from app.ingest import runner
    from app.ingest.client import BiliClient
    from app.ingest.throttle import AccountPacer
    _, db, account, *_ = context
    db.add(Setting(key="ingest", value={"request_interval_seconds": 1})); db.commit()
    old_identity = discovery._key(account, "identity")
    clock, paths, held = [utcnow()], [], []
    change = {"pending": False, "done": False}
    def rotate():
        db.execute(update(SourceAccount).where(SourceAccount.id == account.id)
            .values(secret_encrypted="new-ciphertext").execution_options(synchronize_session=False))
        db.commit()
        change["done"], change["pending"] = True, False
    @contextmanager
    def lock(_db, name):
        assert not held
        if rotation == "before_list" and not change["done"] and discovery._cached(old_identity):
            rotate()
        held.append(name)
        try:
            yield
        finally:
            held.pop()
            if change["pending"]:
                rotate()  # Simulates another session immediately after HTTP unlock.
    def sleep(seconds):
        assert not held
        clock[0] += timedelta(seconds=seconds)
    def transport(incoming):
        assert held
        nav = incoming.url.path.endswith("/nav")
        identity = "456" if incoming.headers.get("cookie") == "SESSDATA=new" else "123"
        paths.append(("nav" if nav else "list", identity, incoming.url.params.get("up_mid")))
        if not change["done"] and ((rotation == "after_nav" and nav) or (rotation == "after_list" and not nav)):
            change["pending"] = True
        data = {"isLogin": True, "mid": identity, "uname": "fixture"} if nav else {
            "count": 1, "list": [{"id": 1, "title": "fixture " + identity, "media_count": 0}]}
        return httpx.Response(200, json={"code": 0, "data": data})
    monkeypatch.setattr(discovery, "_lock", lock)
    monkeypatch.setattr(runner, "_lock", lock)
    monkeypatch.setattr(discovery, "decrypt_secret", lambda value: "SESSDATA=new" if value == "new-ciphertext" else "SESSDATA=old")
    monkeypatch.setattr(discovery, "BiliClient", lambda secret, **kwargs: BiliClient(secret, transport=httpx.MockTransport(transport), **kwargs))
    monkeypatch.setattr(discovery, "AccountPacer", lambda db, account, policy, guard, **kwargs:
        AccountPacer(db, account, policy, guard, clock=lambda: clock[0], sleep=sleep, **kwargs))
    rejected = request(context)
    assert rejected.status_code == 429 and rejected.headers["X-Source-Error"] == "account_changed"
    assert rejected.headers["Retry-After"] == "1"
    db.refresh(account)
    assert account.cooldown_until is None and account.risk_failures == 0
    assert discovery._cached(discovery._key(account, "identity")) is None
    assert discovery._cached(discovery._key(account, "created", 0)) is None
    response = request(context)
    assert response.status_code == 200 and response.json()["items"][0]["title"] == "fixture 456"
    assert paths[-1] == ("list", "456", "456")
    assert not any(path == "list" and identity == "456" and uid == "123" for path, identity, uid in paths)


def test_created_all_is_cached_then_sliced_and_deduplicated(context):
    _, _, _, reply, calls, clients, _ = context
    rows = [{"id": n, "title": f"收藏 {n}", "media_count": n} for n in range(1, 26)]
    reply["created"] = {"count": 26, "list": rows + [rows[0]], "private_raw": "not-exposed"}
    first = request(context).json()
    second = request(context, page=2).json()
    assert len(first["items"]) == 20 and first["next_page"] == 2
    assert [row["source_id"] for row in second["items"]] == [str(n) for n in range(21, 26)]
    assert second["cached"] and second["total"] == 25 and not second["has_more"]
    assert calls == [("nav", {}), ("/x/v3/fav/folder/created/list-all", {"up_mid": "123"})]
    assert clients[0].closed
    assert "private_raw" not in first and set(first["items"][0]) == {"kind", "source_id", "title", "owner_uid", "owner_name", "media_count"}


@pytest.mark.parametrize("category", ["collected", "following"])
def test_paginated_categories_use_required_parameters_and_raw_page_end(context, category):
    _, _, _, reply, calls, _, _ = context
    key = "mid" if category == "following" else "id"
    name = "uname" if category == "following" else "title"
    reply[category] = {"list": [{key: 1, name: "测试"}, {key: 1, name: "重复"}],
                       "count": 40, "total": 48, "has_more": True}
    result = request(context, category).json()
    assert len(result["items"]) == 1 and result["next_page"] == 2
    _, params = calls[-1]
    assert params["pn"] == 1
    if category == "following":
        assert params == {"pn": 1, "ps": 24, "vmid": "123", "order": "desc", "order_type": "attention", "gaia_source": "main_web"}
    else:
        assert params == {"pn": 1, "ps": 20, "up_mid": "123", "platform": "web"}
    reply[category] = {"list": [], "count": 20, "total": 24, "has_more": False}
    assert request(context, category, page=2).json()["next_page"] is None


def test_cache_isolated_by_account_and_rotated_credentials(context):
    _, db, account, _, calls, _, _ = context
    assert request(context).status_code == 200
    assert request(context).json()["cached"]
    account.secret_encrypted = "new-ciphertext"; db.commit()
    assert not request(context).json()["cached"]
    other = SourceAccount(name="other", secret_encrypted="new-ciphertext")
    db.add(other); db.commit()
    assert not request(context, account_id=other.id).json()["cached"]
    assert len(calls) == 6
    account.status = "invalid"; db.commit()
    assert request(context).status_code == 409


def test_account_reload_under_worker_mutex_precedes_decryption(context, monkeypatch):
    _, db, account, _, _, _, _ = context
    observed = []
    @contextmanager
    def rotate(database, key):
        assert key == "account:" + account.id
        database.execute(update(SourceAccount).where(SourceAccount.id == account.id).values(secret_encrypted="rotated")
                         .execution_options(synchronize_session=False))
        database.commit()
        yield
    monkeypatch.setattr(discovery, "_lock", rotate)
    monkeypatch.setattr(discovery, "decrypt_secret", lambda value: observed.append(value) or "mock-cookie")
    assert request(context).status_code == 200
    assert observed == ["rotated"]
    assert request(context).json()["cached"]


@pytest.mark.parametrize("code,status", [("login_required", 409), ("rate_limited", 429), ("network_error", 502)])
def test_nav_failure_is_safe_closes_client_and_observes_cooldown(context, code, status):
    _, _, account, reply, calls, clients, _ = context
    reply["nav"] = IngestError("sensitive upstream text", code=code)
    response = request(context)
    assert response.status_code == status and "sensitive" not in response.text
    assert clients[0].closed and len(calls) == 1
    if code == "login_required":
        assert account.status == "invalid"
    if code == "rate_limited":
        assert response.headers["X-Source-Error"] == "rate_limited"
        assert account.cooldown_until is not None and account.risk_failures == 1
        assert request(context).status_code == 429
        assert len(calls) == 1


def test_long_configured_pacing_defers_without_repeating_nav_or_bypassing_deadline(context, monkeypatch):
    _, db, account, _, calls, _, _ = context
    base = discovery.BiliClient
    class PacedClient(base):
        def nav(self):
            self.before_request()
            return super().nav()
        def json(self, path, params):
            self.before_request()
            return super().json(path, params)
    monkeypatch.setattr(discovery, "BiliClient", PacedClient)
    db.add(Setting(key="ingest", value={"request_interval_seconds": 60})); db.commit()
    response = request(context)
    assert response.status_code == 429 and response.headers["X-Source-Error"] == "account_cooldown"
    assert [path for path, _ in calls] == ["nav"]
    assert request(context).status_code == 429
    assert len(calls) == 1
    # Simulate elapsed time; do not sleep or contact any source.
    account.next_request_at = utcnow() - timedelta(seconds=1); db.commit()
    assert request(context).status_code == 200
    assert [path for path, _ in calls] == ["nav", "/x/v3/fav/folder/created/list-all"]


def test_decryption_errors_not_leaked_and_cooldown_avoids_requests(context, monkeypatch):
    _, db, account, _, calls, _, _ = context
    account.cooldown_until = utcnow() + timedelta(hours=1); db.commit()
    assert request(context).status_code == 429 and not calls
    account.cooldown_until = None; db.commit()
    def bad(_): raise ValueError("sensitive internal key detail")
    monkeypatch.setattr(discovery, "decrypt_secret", bad)
    response = request(context)
    assert response.status_code == 409 and "sensitive" not in response.text


@pytest.mark.parametrize("bad", [{"list": None, "count": 2}, {"list": [{"id": 1, "title": ""}]}, {"list": [{"id": "https://bad", "title": "a"}]}])
def test_malformed_lists_are_errors_not_empty_success(context, bad):
    context[3]["created"] = bad
    assert request(context).status_code == 502
    assert all(key[2] == "identity" for key in discovery._cache)


@pytest.mark.parametrize("kind,value,reply_key,data", [
    ("creator", "https://space.bilibili.com/789", "profile", {"mid": 789, "name": "真实 UP"}),
    ("favorite", "https://space.bilibili.com/123/favlist?fid=789", "info", {"id": 789, "title": "真实收藏夹"}),
])
def test_resolve_fetches_real_title_and_rejects_arbitrary_hosts(context, kind, value, reply_key, data):
    client, _, account, reply, calls, _, _ = context
    reply[reply_key] = data
    params = {"account_id": account.id, "kind": kind, "value": value}
    response = client.get("/api/v1/admin/source-discovery/resolve", params=params)
    assert response.json() == {"kind": kind, "source_id": "789", "title": data.get("title", data.get("name"))}
    before = len(calls)
    assert client.get("/api/v1/admin/source-discovery/resolve", params=params).json() == response.json()
    assert len(calls) == before
    params["value"] = "http://127.0.0.1/internal?fid=789"
    assert client.get("/api/v1/admin/source-discovery/resolve", params=params).status_code == 422
    assert len(calls) == before
