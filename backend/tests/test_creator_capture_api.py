"""Creator capture keeps front-end privileges, source scope and deduplication."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import creator_capture_api as capture, source_discovery
from app.db import get_db
from app.ingest.errors import IngestError
from app.models import AuditLog, Base, Creator, Job, PlatformUser, Setting, SourceAccount, User, Video
from app.security import COOKIE_NAME, create_session


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(capture.router)
    source_discovery._cache.clear()
    calls, listing, details = [], {"page": {"pn": 1, "ps": 30, "count": 0}, "list": {"vlist": []}}, {}

    class Client:
        def __init__(self, *args, **kwargs): pass
        def close(self): pass
        def nav(self): return {"isLogin": True, "mid": 123, "uname": "Fixture account"}
        def creator_page(self, uid, page):
            calls.append(("list", uid, page))
            return listing
        def view(self, bvid):
            calls.append(("view", bvid))
            result = details.get(bvid, {"bvid": bvid, "owner": {"mid": "456"}, "is_upower_exclusive": False})
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(source_discovery, "BiliClient", Client)
    monkeypatch.setattr(source_discovery, "decrypt_secret", lambda _: "SESSDATA=synthetic")
    with Session(engine, expire_on_commit=False) as db:
        person = PlatformUser(uid="456", display_name="Fixture UP")
        account = SourceAccount(name="Fixture account", secret_encrypted="synthetic-ciphertext", status="valid")
        editor = User(username="editor", password_hash="unused", role="editor")
        reader = User(username="reader", password_hash="unused", role="reader")
        db.add_all([person, account, editor, reader]); db.flush()
        creator = Creator(user_id=person.id)
        db.add(creator); db.flush()
        session, token = create_session(db, editor)
        _, reader_token = create_session(db, reader)
        db.commit()
        def dependency(): yield db
        app.dependency_overrides[get_db] = dependency
        with TestClient(app, base_url="http://localhost") as client:
            client.cookies.set(COOKIE_NAME, token)
            client.headers["X-CSRF-Token"] = session.csrf_token
            yield SimpleNamespace(client=client, db=db, account=account, creator=creator, calls=calls,
                listing=listing, details=details, reader_token=reader_token, prefix=f"/api/v1/creators/{creator.id}")
    engine.dispose()
    source_discovery._cache.clear()


def post(ctx, bvids, paid="all"):
    return ctx.client.post(ctx.prefix + "/capture", json={"account_id": ctx.account.id, "bvids": bvids, "paid": paid})


def test_capture_options_and_source_requests_require_editor(context):
    ctx = context
    response = ctx.client.get(ctx.prefix + "/capture-options")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["accounts"] == [{"id": ctx.account.id, "name": "Fixture account", "status": "valid"}]
    assert "secret" not in response.text
    ctx.client.cookies.set(COOKIE_NAME, ctx.reader_token)
    assert ctx.client.get(ctx.prefix + "/capture-options").status_code == 403
    assert ctx.client.get(ctx.prefix + "/latest", params={"account_id": ctx.account.id}).status_code == 403
    ctx.client.cookies.clear()
    assert post(ctx, ["BV1234567890"]).status_code == 401
    assert ctx.calls == []


def test_capture_requires_csrf_and_valid_bounded_selection(context):
    ctx = context
    del ctx.client.headers["X-CSRF-Token"]
    assert post(ctx, ["BV1234567890"]).status_code == 403
    assert ctx.calls == []


def test_paid_filter_keeps_source_pagination_and_latest_saved_status(context):
    ctx = context
    ctx.listing.update(page={"pn": 1, "ps": 30, "count": 60}, list={"vlist": [
        {"bvid": "BV1234567890", "mid": 456, "title": "Normal", "created": 1700000000, "length": "1:23"},
        {"bvid": "BV1234567891", "mid": 456, "title": "Paid", "is_upower_exclusive": True,
         "pic": "https://i0.hdslb.com/pic.jpg?discard=secret"}]})
    ctx.db.add(Video(bvid="BV1234567891", capture_status="complete")); ctx.db.commit()
    response = ctx.client.get(ctx.prefix + "/latest", params={"account_id": ctx.account.id, "paid": "only"})
    data = response.json()
    assert response.status_code == 200 and data["has_more"] and data["next_page"] == 2 and data["total"] == 60
    assert len(data["items"]) == 1 and data["items"][0]["capture_status"] == "complete"
    assert data["items"][0]["cover_url"] == "https://i0.hdslb.com/pic.jpg"
    again = ctx.client.get(ctx.prefix + "/latest", params={"account_id": ctx.account.id, "paid": "exclude"}).json()
    assert again["cached"] and again["items"][0]["duration"] == 83 and len(ctx.calls) == 1
    ctx.listing["list"]["vlist"] = [ctx.listing["list"]["vlist"][0]]
    empty = ctx.client.get(ctx.prefix + "/latest", params={"account_id": ctx.account.id, "paid": "only", "refresh": True}).json()
    assert empty["items"] == [] and empty["has_more"] and empty["filter_scope"] == "current_source_page"


def test_selected_videos_verify_ownership_filter_and_deduplicate_jobs(context):
    ctx = context
    ctx.details["BV1234567891"] = {"bvid": "BV1234567891", "owner": {"mid": 456}, "is_upower_exclusive": True}
    result = post(ctx, ["BV1234567890", "BV1234567891", "BV1234567891"], "only")
    assert result.status_code == 202 and result.json()["queued"] == 1 and result.json()["skipped"] == 1
    job = ctx.db.scalar(select(Job))
    assert job.policy["capture_creator_uid"] == "456" and job.policy["capture_paid_filter"] == "only"
    assert job.account_id == ctx.account.id
    repeat = post(ctx, ["BV1234567891"], "only").json()
    assert repeat["queued"] == 0 and repeat["reused"] == 1
    assert ctx.db.scalar(select(func.count()).select_from(Job)) == 1
    assert ctx.db.scalar(select(func.count()).select_from(AuditLog)) == 2


def test_batch_validation_is_atomic_and_upstream_failure_does_not_queue(context):
    ctx = context
    ctx.details["BV1234567891"] = {"bvid": "BV1234567891", "owner": {"mid": 999}}
    assert post(ctx, ["BV1234567890", "BV1234567891"]).status_code == 422
    assert ctx.db.scalar(select(func.count()).select_from(Job)) == 0
    ctx.details["BV1234567891"] = IngestError("login", code="login_required")
    assert post(ctx, ["BV1234567890", "BV1234567891"]).status_code == 409
    assert ctx.db.scalar(select(func.count()).select_from(Job)) == 0


def test_deleted_video_is_not_resurrected(context):
    ctx = context
    ctx.db.add(Setting(key="library_deleted_video:BV1234567890", value={"job_id": "fixture"})); ctx.db.commit()
    response = post(ctx, ["BV1234567890"])
    assert response.status_code == 202 and response.json()["skipped"] == 1
    assert ctx.calls == [] and ctx.db.scalar(select(func.count()).select_from(Job)) == 0
