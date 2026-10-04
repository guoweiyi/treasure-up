from datetime import timedelta
import hashlib
import json

import pytest
from sqlalchemy import func, select

from app import userscript_api as integration
from app.models import (Asset, AuditLog, IntegrationToken, Job, MediaVariant, Setting, SourceAccount,
                        User, Video, VideoPart, utcnow)
from test_api import context, login  # noqa: F401

ADMIN = "/api/v1/admin/integrations/userscript-tokens"
STATUS = "/api/v1/integrations/userscript/status"
SUBMIT = "/api/v1/integrations/userscript/videos"


def setup_token(context, policy=None):
    client, db, _ = context
    login(client)
    account = SourceAccount(name="test-account", uid="private-uid", secret_encrypted="COOKIE-MUST-NOT-LEAVE", status="valid")
    db.add(account); db.commit()
    response = client.post(ADMIN, json={"name": "fixture", "account_id": account.id, "policy": policy or {}})
    assert response.status_code == 201, response.text
    payload = response.json()
    return account, payload["item"], payload["token"]


def bearer(token, **extra):
    return {"Authorization": "Bearer " + token, **extra}


def test_token_is_one_time_hash_only_scoped_and_cookie_independent(context):
    client, db, _ = context
    account, item, token = setup_token(context, {"quality": "720p", "create_compatible_copy": False})
    stored = db.get(IntegrationToken, item["id"])
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest() and stored.token_hash != token
    listing = client.get(ADMIN).json()
    assert listing["items"][0]["scope"] == "ingest.submit"
    serialized = json.dumps(listing)
    assert token not in serialized and stored.token_hash not in serialized and "COOKIE-MUST-NOT-LEAVE" not in serialized
    audit = json.dumps([row.details for row in db.scalars(select(AuditLog))])
    assert token not in audit and stored.token_hash not in audit
    client.cookies.clear(); client.headers.pop("X-CSRF-Token")
    assert client.get(ADMIN, headers=bearer(token)).status_code == 401
    assert client.post(SUBMIT, json={"bvids": ["BV1234567890"]}).status_code == 401
    status = client.get(STATUS, headers=bearer(token))
    assert status.status_code == 200
    assert status.json()["account"] == {"name": "test-account"}
    assert "private-uid" not in status.text and account.id not in status.text
    response = client.post(SUBMIT, headers=bearer(token), json={"bvids": ["BV1234567890", "BV1234567890", "BV1234567891"]})
    assert response.status_code == 202, response.text
    assert response.json()["unique"] == 2 and response.json()["submitted"] == 3
    assert response.json()["counts"]["queued"] == 2
    jobs = list(db.scalars(select(Job)))
    assert len(jobs) == 2 and all(job.kind == "archive_video" and job.account_id == account.id for job in jobs)
    assert all(job.policy["quality"] == "720p" and job.policy["create_compatible_copy"] is False for job in jobs)
    repeated = client.post(SUBMIT, headers=bearer(token), json={"bvids": ["BV1234567890"]})
    assert repeated.json()["counts"]["active"] == 1
    assert db.scalar(select(func.count()).select_from(Job)) == 2


@pytest.mark.parametrize("body", [
    {"bvids": ["https://127.0.0.1/admin"]}, {"bvids": ["file:///etc/passwd"]},
    {"bvids": ["BV1234567890 " ]}, {"bvids": []}, {"bvids": ["BV1234567890"] * 51},
    {"bvids": ["BV1234567890"], "kind": "backup"}, {"bvids": ["BV1234567890"], "account_id": "other"},
    {"bvids": ["BV1234567890"], "policy": {"max_download_bytes": 99999999999999}},
])
def test_submission_rejects_urls_arbitrary_jobs_policy_and_oversized_batches(context, body):
    client, db, _ = context
    _, _, token = setup_token(context)
    assert client.post(SUBMIT, headers=bearer(token), json=body).status_code == 422
    assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_admin_permissions_csrf_and_origin_boundary(context):
    client, db, _ = context
    assert client.get(ADMIN).status_code == 401
    login(client, "reader")
    assert client.get(ADMIN).status_code == 403
    assert client.post(ADMIN, json={"name": "x", "account_id": "x"}).status_code == 403
    client.post("/api/v1/auth/logout")
    _, item, token = setup_token(context)
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.delete(ADMIN + "/" + item["id"]).status_code == 403
    assert client.patch(ADMIN + "/" + item["id"], json={"name": "x"}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    for origin in ("https://www.bilibili.com", "https://evil.invalid", "http://localhost"):
        assert client.post(SUBMIT, headers={"Origin": origin}, json={"bvids": ["BV1234567890"]}).status_code == 401
    for origin in ("https://www.bilibili.com", "moz-extension://fixture", "chrome-extension://fixture", "null"):
        assert client.get(STATUS, headers=bearer(token, Origin=origin)).status_code == 200
    assert client.post(SUBMIT, headers=bearer(token), content=json.dumps({"bvids": ["BV1234567890"]})).status_code in {415, 422}
    preflight = client.options(SUBMIT, headers={"Origin": "https://www.bilibili.com", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert "access-control-allow-origin" not in preflight.headers
    assert db.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.parametrize("change", ["revoke", "expire", "disable_user", "demote_user", "invalid_account", "disable_account"])
def test_revocation_expiry_and_disabled_identity_are_enforced(context, change):
    client, db, _ = context
    account, item, token = setup_token(context)
    stored = db.get(IntegrationToken, item["id"])
    if change == "revoke":
        assert client.delete(ADMIN + "/" + item["id"]).json() == {"id": item["id"], "revoked": True}
        assert client.patch(ADMIN + "/" + item["id"], json={"name": "restore"}).status_code == 409
    elif change == "expire":
        stored.expires_at = utcnow() - timedelta(seconds=1)
    elif change in {"disable_user", "demote_user"}:
        user = db.get(User, stored.user_id)
        if change == "disable_user": user.disabled = True
        else: user.role = "reader"
    else:
        account.status = "invalid" if change == "invalid_account" else "disabled"
    db.commit()
    assert client.post(SUBMIT, headers=bearer(token), json={"bvids": ["BV1234567890"]}).status_code in {401, 409}
    assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_inventory_states_deletion_barrier_and_metadata_only_do_not_fake_saved_media(context):
    client, db, _ = context
    _, _, token = setup_token(context)
    saved, metadata, stopped = [Video(bvid=f"BV123456789{i}", capture_status="complete") for i in range(3)]
    db.add_all([saved, metadata, stopped]); db.flush()
    part = VideoPart(video_id=saved.id, cid="1")
    media = Asset(sha256="a" * 64, size=1, kind="media")
    db.add_all([part, media]); db.flush()
    db.add(MediaVariant(part_id=part.id, asset_id=media.id, format_key="saved"))
    db.add(Job(kind="archive_video", target_id=stopped.id, status="cancelled", dedupe_key="stopped"))
    db.add(Setting(key="library_deleted_video:BV1234567893", value={"job_id": "fixture"}))
    db.commit()
    result = client.post(SUBMIT, headers=bearer(token), json={"bvids": [f"BV123456789{i}" for i in range(4)]}).json()
    assert [item["status"] for item in result["items"]] == ["existing", "queued", "needs_attention", "deleted"]
    assert db.get(Video, metadata.id).capture_status == "complete"
    assert db.scalar(select(func.count()).select_from(Job)) == 2


def test_account_rotation_uses_current_binding_and_obeys_existing_cooldown(context):
    client, db, _ = context
    account, item, token = setup_token(context)
    rotated = client.patch(f"/api/v1/admin/accounts/{account.id}", json={"cookie": "rotated-fixture-only"})
    assert rotated.status_code == 200
    account.cooldown_until = utcnow() + timedelta(hours=1)
    db.commit()
    response = client.post(SUBMIT, headers=bearer(token), json={"bvids": ["BV1234567890"]})
    assert response.status_code == 202
    job = db.get(Job, response.json()["items"][0]["job_id"])
    assert job.account_id == account.id and integration._utc(job.available_at) == integration._utc(account.cooldown_until)
    assert "rotated-fixture-only" not in response.text


def test_durable_batch_and_daily_limits_allow_duplicate_reuse_without_new_quota(context, monkeypatch):
    client, db, _ = context
    _, item, token = setup_token(context)
    monkeypatch.setattr(integration, "MINUTE_REQUESTS", 2)
    monkeypatch.setattr(integration, "DAY_VIDEOS", 1)
    first = client.post(SUBMIT, headers=bearer(token), json={"bvids": ["BV1234567890", "BV1234567891"]})
    assert first.json()["counts"]["queued"] == first.json()["counts"]["daily_limit"] == 1
    assert client.post(SUBMIT, headers=bearer(token), json={"bvids": ["BV1234567890"]}).json()["counts"]["active"] == 1
    rejected = client.post(SUBMIT, headers=bearer(token), json={"bvids": ["BV1234567892"]})
    assert rejected.status_code == 429 and int(rejected.headers["Retry-After"]) > 0
    assert db.get(IntegrationToken, item["id"]).day_videos == 1
    assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_admin_can_rebind_and_change_frozen_policy_without_disclosing_token(context):
    client, db, _ = context
    _, item, token = setup_token(context)
    second = SourceAccount(name="second", secret_encrypted="never-read", status="valid")
    db.add(second); db.commit()
    response = client.patch(ADMIN + "/" + item["id"], json={"name": "updated", "account_id": second.id,
        "policy": {"quality": "1080p", "fetch_comments": False}})
    assert response.status_code == 200 and response.json()["account_name"] == "second"
    assert token not in response.text
    result = client.post(SUBMIT, headers=bearer(token), json={"bvids": ["BV1234567890"]}).json()
    job = db.get(Job, result["items"][0]["job_id"])
    assert job.account_id == second.id and job.policy["quality"] == "1080p" and job.policy["fetch_comments"] is False
