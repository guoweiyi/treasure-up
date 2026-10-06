"""Batch submission freezes bounded selections into the existing replica jobs."""
from datetime import timedelta
from sqlalchemy import select

from app.models import AssetLocation, AssetRef, Job, MediaVariant, StorageProfile, Video, VideoPart, utcnow
from app.jobs import sync_video_job
from app.storage.service import ingest_file
from test_api import context, login
from test_delivery_api import seed_media


def target(db, tmp):
    profile = StorageProfile(name="Backup", kind="local", config={"root": str(tmp / "backup")})
    db.add(profile); db.commit()
    return profile


def test_batch_requires_admin_csrf_and_bounded_nonempty_selection(context):
    client, db, tmp = context
    video, _, _, _ = seed_media(db, tmp)
    profile = target(db, tmp)
    url = "/api/v1/admin/storage/sync-batch"
    body = {"target_profile_id": profile.id, "items": [{"video_id": video.id}]}
    assert client.post(url, json=body).status_code == 401
    login(client, "reader")
    assert client.post(url, json=body).status_code == 403
    login(client)
    token = client.headers.pop("X-CSRF-Token")
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, json=body, headers={"Origin": "https://outside.invalid"}).status_code == 403
    client.headers["X-CSRF-Token"] = token
    for items in ([], [{"video_id": video.id}] * 51, [{"video_id": video.id, "variant_ids": []}]):
        assert client.post(url, json={**body, "items": items}).status_code == 422
    assert list(db.scalars(select(Job))) == []


def test_batch_partial_failures_and_single_call_deduplicate(context):
    client, db, tmp = context
    video, _, original, _ = seed_media(db, tmp)
    profile = target(db, tmp); login(client)
    empty = Video(bvid="BV0000000002", title="Empty")
    db.add(empty); db.commit()
    body = {"target_profile_id": profile.id, "items": [
        {"video_id": video.id, "variant_ids": [original.id]}, {"video_id": video.id},
        {"video_id": "missing"}, {"video_id": empty.id},
    ]}
    response = client.post("/api/v1/admin/storage/sync-batch", json=body)
    assert response.status_code == 202, response.text
    data = response.json()
    assert (data["queued"], data["existing"], data["failed"]) == (1, 0, 2)
    assert len(data["items"]) == 3
    assert [r["status"] for r in data["items"]] == ["queued", "failed", "failed"]
    repeated = client.post("/api/v1/admin/storage/sync-batch", json=body).json()
    assert (repeated["queued"], repeated["existing"], repeated["failed"]) == (0, 1, 2)
    job_id = data["items"][0]["job"]["id"]
    single = client.post(f"/api/v1/admin/videos/{video.id}/sync", json={"target_profile_id": profile.id})
    assert single.json()["id"] == job_id
    jobs = list(db.scalars(select(Job)))
    assert len(jobs) == 1 and jobs[0].kind == "sync_video"
    jobs[0].status = "succeeded"; db.commit()
    assert client.post(f"/api/v1/admin/videos/{video.id}/sync", json={"target_profile_id": profile.id}).json()["id"] != job_id


def test_selected_version_includes_its_hls_dependencies_and_shared_sidecars_only(context):
    client, db, tmp = context
    video, part, original, source = seed_media(db, tmp, hls=True)
    profile = target(db, tmp)
    path = tmp / "different.mp4"; path.write_bytes(b"another media payload")
    other_asset = ingest_file(db, path, kind="media", mime_type="video/mp4")
    other = MediaVariant(part_id=part.id, asset_id=other_asset.id, kind="archive", format_key="different")
    db.add(other)
    path = tmp / "cover.jpg"; path.write_bytes(b"cover payload")
    cover = ingest_file(db, path, kind="cover", mime_type="image/jpeg")
    video.cover_asset_id = cover.id
    # Dedicated segment proves dependency expansion, independent of source file.
    path = tmp / "segment.m4s"; path.write_bytes(b"dedicated segment payload")
    segment = ingest_file(db, path, kind="hls_segment", mime_type="video/mp4")
    hls = db.scalar(select(MediaVariant).where(MediaVariant.kind == "hls"))
    db.add(AssetRef(asset_id=segment.id, entity_type="asset", entity_id=hls.asset_id, purpose="hls_segment"))
    db.commit(); login(client)
    result = client.post("/api/v1/admin/storage/sync-batch", json={"target_profile_id": profile.id,
        "items": [{"video_id": video.id, "variant_ids": [original.id, original.id]}]}).json()
    job = db.get(Job, result["items"][0]["job"]["id"])
    assert set(job.policy["asset_ids"]) == {source.id, hls.asset_id, segment.id, cover.id}
    assert other_asset.id not in job.policy["asset_ids"]
    assert job.policy["target_profile_id"] == profile.id
    assert job.checkpoint == {}, "submission must not perform file transfers"


def test_batch_rejects_foreign_and_hls_variant_and_disabled_target(context):
    client, db, tmp = context
    video, part, original, asset = seed_media(db, tmp, hls=True)
    profile = target(db, tmp)
    other_video = Video(bvid="BV0000000003", title="Other")
    db.add(other_video); db.flush()
    other_part = VideoPart(video_id=other_video.id, cid="999")
    db.add(other_part); db.flush()
    other = MediaVariant(part_id=other_part.id, asset_id=asset.id, kind="archive", format_key="other")
    db.add(other); db.commit(); login(client)
    hls = db.scalar(select(MediaVariant).where(MediaVariant.kind == "hls"))
    for variant_id in (other.id, hls.id, "missing"):
        result = client.post("/api/v1/admin/storage/sync-batch", json={"target_profile_id": profile.id,
            "items": [{"video_id": video.id, "variant_ids": [variant_id]}]}).json()
        assert result["failed"] == 1 and result["queued"] == 0
    profile.enabled = False; db.commit()
    result = client.post("/api/v1/admin/storage/sync-batch", json={"target_profile_id": profile.id,
        "items": [{"video_id": video.id}]})
    assert result.status_code == 422
    assert list(db.scalars(select(Job))) == []


def test_multiple_selected_videos_execute_existing_worker_and_copy_exact_bytes(context):
    client, db, tmp = context
    first, _, _, first_asset = seed_media(db, tmp)
    profile = target(db, tmp)
    second = Video(bvid="BV0000000004", title="Second")
    db.add(second); db.flush()
    part = VideoPart(video_id=second.id, cid="1000")
    db.add(part); db.flush()
    payload = b"a second synthetic media file" * 100
    path = tmp / "second.mp4"; path.write_bytes(payload)
    asset = ingest_file(db, path, kind="media", mime_type="video/mp4")
    db.add(MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="second"))
    db.commit(); login(client)
    result = client.post("/api/v1/admin/storage/sync-batch", json={"target_profile_id": profile.id,
        "items": [{"video_id": first.id}, {"video_id": second.id}]}).json()
    assert result["queued"] == 2 and result["failed"] == 0
    for item in result["items"]:
        job = db.get(Job, item["job"]["id"])
        job.status = "running"; job.lease_owner = "batch-test"
        job.lease_expires_at = utcnow() + timedelta(minutes=10); db.commit()
        outcome = sync_video_job(db, job, owner="batch-test")
        assert outcome["completed"] == outcome["total"] == 1
        assert outcome["originals_retained"] is True
    locations = list(db.scalars(select(AssetLocation).where(AssetLocation.storage_profile_id == profile.id)))
    assert len(locations) == 2 and all(location.state == "ready" for location in locations)
    expected = {first_asset.id: bytes(range(256)) * 1024, asset.id: payload}
    for location in locations:
        assert (tmp / "backup" / location.object_key).read_bytes() == expected[location.asset_id]
        assert location.verified_at is not None
