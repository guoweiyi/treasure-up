from copy import deepcopy
from datetime import timedelta

import pytest
from app import catalog
from app.models import Asset, Job, MediaVariant, VideoPart, utcnow
from test_api import context, login, seed_catalog


def jobs(db, video, part, *, parent_status="succeeded", media_status="failed", bvid_target=False):
    parent = Job(kind="archive_video", target_id=video.bvid if bvid_target else video.id,
                 status=parent_status, dedupe_key="parent", policy={"media": True},
                 checkpoint={"part_ids": [part.id]}, created_at=utcnow() - timedelta(seconds=1))
    db.add(parent); db.flush()
    child = Job(kind="download_media", target_id=video.id, status=media_status, dedupe_key="child",
                policy={"media": True}, error="safe media failure" if media_status == "failed" else None,
                checkpoint={"part_ids": [part.id], "metadata_parent_id": parent.id})
    db.add(child); db.flush()
    parent.checkpoint = {**parent.checkpoint, "media_job_id": child.id}
    video.capture_status = "complete"
    video.metadata_json = {"ingest_state": {"metadata": "running", "media": "running", "media_job_id": child.id}}
    db.commit()
    return parent, child


def original(db, part):
    asset = Asset(sha256=(part.id.replace("-", "") * 2)[:64], size=3, kind="media")
    db.add(asset); db.flush()
    variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="source")
    db.add(variant); db.commit()
    return variant


@pytest.mark.parametrize("media_status", ["failed", "partial", "blocked", "paused", "cancelled"])
def test_child_terminal_state_replaces_stale_running_without_mutating_video(context, media_status):
    client, db, _ = context
    video, part, _ = seed_catalog(db)
    parent, child = jobs(db, video, part, media_status=media_status, bvid_target=True)
    saved = deepcopy(video.metadata_json)
    public = client.get(f"/api/v1/videos/{video.id}").json()
    assert public["capture_status"] == "partial"
    assert public["ingest_state"]["metadata"] == "ready"
    assert public["ingest_state"]["media"] == media_status
    assert "safe media failure" not in str(public)  # Detailed task errors remain admin-only.
    assert video.metadata_json == saved and video.capture_status == "complete"
    login(client)
    detail = client.get(f"/api/v1/admin/jobs/{parent.id}").json()
    assert detail["status"] == "succeeded" and detail["target_title"] == video.title
    assert detail["capture"]["media"] == media_status
    assert detail["capture"]["media_job_id"] == child.id
    assert detail["capture"]["resume_media"] == (media_status == "paused")
    assert detail["capture"]["retry_media"] == (media_status != "paused")


def test_failed_metadata_and_explicit_retry_are_immediately_visible(context):
    client, db, _ = context
    video, part, _ = seed_catalog(db)
    parent = Job(kind="archive_video", target_id=video.id, status="failed", dedupe_key="failed",
                 checkpoint={"part_ids": [part.id]}, error="safe internal failure", policy={"media": True})
    db.add(parent)
    video.metadata_json = {"ingest_state": {"metadata": "running"}}
    db.commit()
    assert catalog.video_view(db, video)["ingest_state"]["metadata"] == "failed"
    assert catalog.video_view(db, video)["capture_status"] == "partial"
    login(client)
    assert client.post(f"/api/v1/admin/jobs/{parent.id}/retry").status_code == 200
    current = client.get(f"/api/v1/videos/{video.id}").json()
    assert current["ingest_state"]["metadata"] == "queued"
    assert current["capture_status"] == "capturing"


def test_retry_download_from_successful_parent_keeps_metadata_history(context):
    client, db, _ = context
    video, part, _ = seed_catalog(db)
    parent, child = jobs(db, video, part)
    login(client)
    assert client.post(f"/api/v1/admin/jobs/{child.id}/retry").status_code == 200
    result = client.get(f"/api/v1/admin/jobs/{parent.id}").json()
    assert result["status"] == "succeeded" and result["capture"]["media"] == "queued"
    assert client.get(f"/api/v1/videos/{video.id}").json()["capture_status"] == "metadata_ready"


def test_success_requires_every_selected_original_not_just_one_variant(context):
    _, db, _ = context
    video, part, _ = seed_catalog(db)
    parent, child = jobs(db, video, part, media_status="succeeded")
    second = VideoPart(video_id=video.id, cid="second", position=2)
    db.add(second); db.flush()
    first_variant = original(db, part)
    child.checkpoint = {**child.checkpoint, "part_ids": [part.id, second.id],
                        "media_variants": {part.id: first_variant.id, second.id: "missing"}}
    db.commit()
    view = catalog.video_view(db, video)
    assert view["playable"] and view["capture_status"] == "partial"
    assert view["ingest_state"]["media"] == "missing"
    assert view["ingest_state"]["media_completed_parts"] == 1
    second_variant = original(db, second)
    child.checkpoint = {**child.checkpoint, "media_variants": {part.id: first_variant.id, second.id: second_variant.id}}
    db.commit()
    assert catalog.video_view(db, video)["capture_status"] == "complete"


def test_consent_wait_is_not_success_and_metadata_only_is_intentionally_complete(context):
    _, db, _ = context
    video, part, _ = seed_catalog(db)
    parent, child = jobs(db, video, part, media_status="succeeded")
    child.checkpoint = {**child.checkpoint, "paid_consent_required": True}
    db.commit()
    view = catalog.video_view(db, video)
    assert view["capture_status"] == "metadata_ready"
    assert view["ingest_state"]["media"] == "awaiting_consent"
    assert catalog.job_views(db, [parent])[0]["capture"]["media"] == "awaiting_consent"
    parent.policy = {"media": False}; db.commit()
    assert catalog.video_view(db, video)["capture_status"] == "complete"
    assert catalog.job_views(db, [parent])[0]["capture"]["media"] == "disabled"


def test_new_capture_does_not_reuse_old_child_success_and_legacy_originals_are_preserved(context):
    _, db, _ = context
    video, part, _ = seed_catalog(db)
    _, old = jobs(db, video, part, media_status="succeeded")
    original(db, part)
    parent = Job(kind="archive_video", target_id=video.id, status="succeeded", dedupe_key="new",
                 created_at=utcnow() + timedelta(seconds=1), policy={"media": True},
                 checkpoint={"part_ids": [part.id]})
    db.add(parent); db.commit()
    view = catalog.video_view(db, video)
    assert view["capture_status"] == "metadata_ready" and view["ingest_state"]["media"] == "not_started"
    parent.checkpoint = {**parent.checkpoint, "media_parts": [part.id]}; db.commit()
    assert catalog.video_view(db, video)["capture_status"] == "complete"


@pytest.mark.parametrize("policy,required", [
    ({"download_media": False}, False),
    ({"media": False}, False),
    ({"download_media": False, "media": True}, False),
    ({"download_media": True, "media": False}, True),
    ({}, True),
])
def test_metadata_only_policy_matches_runner_public_key_mapping(context, policy, required):
    _, db, _ = context
    video, part, _ = seed_catalog(db)
    parent = Job(kind="archive_video", target_id=video.id, status="succeeded", dedupe_key="metadata-only",
                 policy=policy, checkpoint={"part_ids": [part.id]})
    db.add(parent); db.commit()
    view = catalog.video_view(db, video)
    assert view["capture_status"] == ("metadata_ready" if required else "complete")
    assert view["ingest_state"]["media"] == ("not_started" if required else "disabled")
    assert catalog.job_views(db, [parent])[0]["capture"]["media"] == view["ingest_state"]["media"]
