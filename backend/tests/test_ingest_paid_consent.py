import pytest
from sqlalchemy import select

from app.ingest import runner
from app.ingest.client import parse_cookies
from app.models import CaptureRun, Collection, CollectionItem, Job, SourceSubscription, Video, VideoPart
from app.paid_capture import source_origin, summary
from test_ingest_runner import FakeClient, db, setup  # noqa: F401
from test_ingest_sources import NOW, children, complete, favorite, page, source_job


def subscription(db, collection, job, consent=False):
    row = SourceSubscription(collection_id=collection.id, account_id=job.account_id,
                             policy={"include_paid_videos": consent})
    db.add(row); db.commit()
    return row


def test_source_skips_paid_until_saved_source_consent_and_revisits_without_new_event(db, monkeypatch):
    class Client(FakeClient):
        def favorite_page(self, _, number):
            return page([favorite(1, is_upower_exclusive=True), favorite(2)])
    monkeypatch.setattr(runner, "BiliClient", Client)
    monkeypatch.setattr(runner, "_now", lambda: NOW)
    # A global/job policy true is not explicit consent on this source.
    collection, first = source_job(db, policy={"include_paid_videos": True})
    source = subscription(db, collection, first)
    complete(db, first)
    assert len(children(db)) == 1
    item = db.scalar(select(CollectionItem).where(CollectionItem.source_resource_id == "2:1"))
    assert item.observation["archive_decision"] == "paid_consent_required"
    assert collection.monitor_state["counts"]["paid_detected"] == 1
    assert collection.monitor_state["counts"]["paid_skipped"] == 1
    assert summary(db, collection.id, source.policy) == {"detected_count": 1, "pending_count": 1, "consent_required": True}
    source.policy = {"include_paid_videos": True}; db.commit()
    _, second = source_job(db, collection=collection, account_id=first.account_id, policy={"force_full_scan": True})
    complete(db, second)
    assert len(children(db)) == 2
    paid_job = next(child for child in children(db) if child.target_id == item.video_id)
    assert paid_job.policy["source_collection_id"] == collection.id
    assert source_origin(db, paid_job) == collection.id
    assert not summary(db, collection.id, source.policy)["consent_required"]


def test_creator_listing_charge_flag_and_unrelated_title_are_not_confused(db, monkeypatch):
    class Client(FakeClient):
        def profile(self, uid): return {"mid": uid, "name": "UP"}
        def creator_page(self, uid, pn):
            return {"page": {"count": 2, "pn": pn, "ps": 30}, "list": {"vlist": [
                {"aid": 1, "bvid": "BV0000000001", "is_charging_arc": True, "title": "exclusive"},
                {"aid": 2, "bvid": "BV0000000002", "title": "给手机充电"}]}}
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db, kind="creator")
    subscription(db, collection, job)
    complete(db, job)
    assert len(children(db)) == 1
    assert db.get(Video, children(db)[0].target_id).bvid == "BV0000000002"
    assert collection.monitor_state["counts"]["paid_skipped"] == 1


@pytest.mark.parametrize("strategy", ["latest", "new_only"])
def test_explicit_all_paid_grant_overrides_only_paid_initial_window_and_preserves_stops(db, monkeypatch, strategy):
    class Client(FakeClient):
        def favorite_page(self, _, number):
            return page([favorite(1), favorite(2, is_charging_arc=True), favorite(3, is_charging_arc=True), favorite(4)])
    monkeypatch.setattr(runner, "BiliClient", Client)
    monkeypatch.setattr(runner, "_now", lambda: NOW)
    collection, job = source_job(db, policy={"initial_strategy": strategy, "initial_limit": 1})
    subscription(db, collection, job, consent=True)
    stopped_video = Video(bvid="BV0000000003")
    db.add(stopped_video); db.flush()
    stopped = Job(kind="archive_video", target_id=stopped_video.id, status="cancelled", dedupe_key="explicit-stop")
    db.add(stopped); db.commit()
    complete(db, job)
    queued = {db.get(Video, item.target_id).bvid for item in children(db) if item.status == "queued"}
    assert queued == ({"BV0000000001", "BV0000000002"} if strategy == "latest" else {"BV0000000002"})
    assert db.get(Job, stopped.id).status == "cancelled"


def _metadata_job(db, *, source=True, paid=True):
    video = Video(bvid="BV1234567890", aid="123", metadata_json={"is_upower_exclusive": paid})
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="123", duration=10)
    db.add(part); db.flush()
    collection = Collection(source_id="1", kind="creator")
    db.add(collection); db.flush()
    job = setup(db, "archive_video", video.id, {"images": False, "comments": False, "danmaku": False,
        "subtitles": False, "profiles": False, **({"source_collection_id": collection.id} if source else {})})
    job.checkpoint = {"metadata_done": True, "basics_done": True, "part_ids": [part.id]}
    db.add(CollectionItem(collection_id=collection.id, source_resource_id="2:123", video_id=video.id, source_state="available"))
    subscription(db, collection, job)
    return collection, video, part, job


@pytest.mark.parametrize("direct", [False, True])
def test_metadata_detection_blocks_source_media_but_explicit_bv_remains_allowed(db, monkeypatch, direct):
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    collection, video, part, job = _metadata_job(db, source=not direct)
    result = runner.run_job(db, job)
    downloads = list(db.scalars(select(Job).where(Job.kind == "download_media")))
    assert bool(downloads) is direct
    if not direct:
        assert result["paid_consent_required"] is True and not result["continuation"]
        assert video.capture_status == "metadata_ready"
        assert video.metadata_json["ingest_state"]["media"] == "awaiting_consent"
        run = db.get(CaptureRun, result["run_id"])
        assert run.status == "bounded_complete" and run.end_reason == "paid_consent_required"
        assert summary(db, collection.id, {})["consent_required"] is True


def test_legacy_media_child_rechecks_revoked_source_consent_without_download(db, monkeypatch):
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    collection, video, part, parent = _metadata_job(db)
    run = CaptureRun(collection_id=collection.id, scope={})
    db.add(run); db.flush()
    # Pre-upgrade source jobs had no policy marker, only this durable run key.
    parent.dedupe_key, parent.policy = f"archive:{video.id}:{run.id}", {}
    child = setup(db, "download_media", video.id, {"include_paid_videos": True})
    child.checkpoint = {"part_ids": [part.id], "metadata_parent_id": parent.id}; db.commit()
    monkeypatch.setattr(runner, "archive_media", lambda *_a, **_k: pytest.fail("paid body downloaded without consent"))
    result = runner.run_job(db, child)
    assert result["paid_consent_required"] is True and video.capture_status == "metadata_ready"
    assert source_origin(db, child) == collection.id


def test_changed_paid_state_is_checked_after_fresh_view_before_any_extractor(db, monkeypatch):
    collection, video, part, parent = _metadata_job(db, paid=False)
    class Client(FakeClient):
        cookies = parse_cookies("SESSDATA=fixture")
        def view(self, _):
            return {"pages": [{"cid": part.cid, "page": 1}], "is_upower_exclusive": True,
                    "is_upower_play": True, "is_upower_preview": False}
    monkeypatch.setattr(runner, "BiliClient", Client)
    child = setup(db, "download_media", video.id, {"source_collection_id": collection.id})
    child.checkpoint = {"part_ids": [part.id], "metadata_parent_id": parent.id}; db.commit()
    # Calls the real archive_media path: consent callback runs before client
    # extractor, playurl or any download would be needed.
    result = runner.run_job(db, child)
    assert result["paid_consent_required"] and not result["continuation"]
    assert video.metadata_json["access"]["upower_exclusive"] is True
    assert video.capture_status == "metadata_ready"


def test_component_failure_cannot_bypass_paid_confirmation(db, monkeypatch):
    from app.ingest.errors import IngestError, PartialCaptureError
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    _, video, _, job = _metadata_job(db)
    job.policy = {**job.policy, "comments": True}; db.commit()
    def fail(*args): raise IngestError("bad response", code="invalid_pagination")
    monkeypatch.setattr(runner, "_comments", fail)
    with pytest.raises(PartialCaptureError): runner.run_job(db, job)
    assert not db.scalar(select(Job.id).where(Job.kind == "download_media"))
    assert video.metadata_json["ingest_state"]["media"] == "awaiting_consent"


@pytest.mark.parametrize("old_state", ["complete", "awaiting_consent"])
def test_metadata_repair_preserves_completed_paid_media_after_consent_revocation(db, monkeypatch, old_state):
    from app.models import Asset, MediaVariant
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    collection, video, part, parent = _metadata_job(db)
    asset = Asset(sha256="e" * 64, size=4, kind="video", mime_type="video/mp4")
    db.add(asset); db.flush()
    variant = MediaVariant(part_id=part.id, asset_id=asset.id, format_key="saved", kind="archive")
    db.add(variant); db.flush()
    child = Job(kind="download_media", target_id=video.id, account_id=parent.account_id,
        status="succeeded", dedupe_key="download:" + parent.id,
        checkpoint={"part_ids": [part.id], "media_parts": [part.id], "media_variants": {part.id: variant.id}})
    db.add(child); db.flush()
    # Source consent is now false. Metadata repair must reuse the existing
    # completed body, including repairing a stale label from the original bug.
    video.capture_status = "partial"
    video.metadata_json = {**video.metadata_json, "ingest_state": {
        "metadata": "partial", "media": old_state, "media_job_id": child.id}}
    if old_state == "awaiting_consent":
        video.metadata_json = {**video.metadata_json, "paid_capture": {"consent_required": True}}
        parent.checkpoint = {**parent.checkpoint, "paid_consent_required": True}
    db.commit()
    monkeypatch.setattr(runner, "archive_media", lambda *_a, **_k: pytest.fail("must reuse existing archive"))
    result = runner.run_job(db, parent)
    assert not result["continuation"] and not result["paid_consent_required"]
    assert video.capture_status == "complete" and video.metadata_json["ingest_state"]["media"] == "complete"
    assert not (video.metadata_json.get("paid_capture") or {}).get("consent_required")
    assert list(db.scalars(select(Job.id).where(Job.kind == "download_media"))) == [child.id]
    assert db.get(MediaVariant, variant.id).asset_id == asset.id
    assert summary(db, collection.id, {})["pending_count"] == 0


def test_bounded_paid_waiting_child_is_not_mistaken_for_completed_media(db, monkeypatch):
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    _, video, part, parent = _metadata_job(db)
    child = Job(kind="download_media", target_id=video.id, account_id=parent.account_id,
        status="succeeded", dedupe_key="download:" + parent.id,
        checkpoint={"part_ids": [part.id], "paid_consent_required": True})
    db.add(child); db.commit()
    result = runner.run_job(db, parent)
    assert result["paid_consent_required"] and video.capture_status == "metadata_ready"
    assert video.metadata_json["ingest_state"]["media"] == "awaiting_consent"
    source = db.scalar(select(SourceSubscription).where(SourceSubscription.collection_id == parent.policy["source_collection_id"]))
    source.policy = {"include_paid_videos": True}; db.commit()
    for _ in range(2):
        result = runner.run_job(db, parent)
        assert not result["paid_consent_required"]
        assert video.capture_status == "metadata_ready"
        assert video.metadata_json["ingest_state"]["media"] == "queued"
    children = list(db.scalars(select(Job).where(Job.kind == "download_media")))
    assert len(children) == 2
    following = next(item for item in children if item.id != child.id)
    assert following.status == "queued" and following.checkpoint["part_ids"] == [part.id]
    assert following.checkpoint["metadata_parent_id"] == parent.id
    assert not following.checkpoint.get("paid_consent_required")
    assert child.status == "succeeded" and child.checkpoint["paid_consent_required"] is True
