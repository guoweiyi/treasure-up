from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.ingest import runner
from app.ingest.errors import IngestError
from app.models import CaptureRun, Collection, CollectionItem, Creator, Job, Video, VideoCreator
from test_ingest_runner import FakeClient, db, setup


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def favorite(identity, *, added=None, bvid=True, **extra):
    return {"id": identity, "type": 2, "bvid": f"BV{identity:010d}" if bvid else "", "title": f"video {identity}",
            "pubtime": int((NOW - timedelta(days=30)).timestamp()), "fav_time": added or int(NOW.timestamp()), **extra}


def page(items, *, more=False, total=None):
    return {"medias": items, "has_more": more, "info": {"media_count": len(items) if total is None else total}}


def source_job(db, *, kind="favorite", policy=None, collection=None, account_id=None):
    if collection is None:
        collection = Collection(source_id="123", title="preserve custom title", kind=kind)
        db.add(collection)
        db.flush()
    if account_id is None:
        job = setup(db, "scan_collection", collection.id, {"images": False, **(policy or {})})
    else:
        job = Job(kind="scan_collection", target_id=collection.id, account_id=account_id, status="running",
                  policy={"images": False, **(policy or {})}, dedupe_key=str(uuid4()))
        db.add(job); db.commit()
    return collection, job


def complete(db, job, *, limit=30):
    for _ in range(limit):
        result = runner.run_job(db, job)
        if not result["continuation"]:
            job.status = "succeeded"
            db.commit()
            return result
    pytest.fail("Scan failed to finish within its expected page budget")


def children(db):
    return list(db.scalars(select(Job).where(Job.kind == "archive_video")))


def test_favorite_checkpoint_verifies_head_and_equal_timestamp_records(db, monkeypatch):
    calls = []
    class Client(FakeClient):
        def favorite_page(self, _, number):
            calls.append(number)
            return page([favorite(number)], more=number < 3, total=3)
    monkeypatch.setattr(runner, "BiliClient", Client)
    monkeypatch.setattr(runner, "_now", lambda: NOW)
    collection, job = source_job(db, policy={"request_budget": 1})
    for _ in range(3):
        assert runner.run_job(db, job)["continuation"]
        assert not collection.monitor_state.get("last_full_scan_at")
    assert not runner.run_job(db, job)["continuation"]
    assert calls == [1, 2, 3, 1]
    assert len(children(db)) == 3
    assert collection.monitor_state["counts"]["new_items"] == 3
    assert collection.monitor_state["status"] == "complete"
    assert db.scalar(select(func.count()).select_from(CaptureRun)) == 1
    # Re-executing the completed checkpoint makes no additional source requests.
    runner.run_job(db, job)
    assert calls == [1, 2, 3, 1] and len(children(db)) == 3


@pytest.mark.parametrize("strategy,limit,expected", [("all", 2, 5), ("latest", 2, 2), ("new_only", 2, 0)])
def test_initial_strategy_always_builds_full_baseline(db, monkeypatch, strategy, limit, expected):
    class Client(FakeClient):
        def favorite_page(self, _, number):
            return page([favorite(index) for index in range(1, 6)])
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db, policy={"initial_strategy": strategy, "initial_limit": limit})
    complete(db, job)
    assert len(children(db)) == expected
    assert db.scalar(select(func.count()).select_from(CollectionItem)) == 5
    assert collection.monitor_state["baseline_completed_at"]
    assert collection.monitor_state["counts"]["newly_favorited"] == 0


def test_incremental_window_does_not_stop_on_old_known_or_out_of_order_items(db, monkeypatch):
    phase, calls = [0], []
    class Client(FakeClient):
        def favorite_page(self, _, number):
            calls.append(number)
            if phase[0] == 0:
                return page([favorite(1, added=int((NOW - timedelta(days=1)).timestamp()))])
            if number == 1:
                return page([favorite(1, added=int((NOW - timedelta(days=1)).timestamp()))], more=True, total=4)
            if number == 2:
                return page([favorite(2, added=int((NOW + timedelta(minutes=30)).timestamp()))], more=True, total=4)
            pytest.fail("Incremental scan exceeded the configured fixed window")
    monkeypatch.setattr(runner, "BiliClient", Client)
    monkeypatch.setattr(runner, "_now", lambda: NOW)
    collection, initial = source_job(db, policy={"initial_strategy": "new_only"})
    complete(db, initial)
    phase[0] = 1
    monkeypatch.setattr(runner, "_now", lambda: NOW + timedelta(hours=1))
    _, job = source_job(db, collection=collection, account_id=initial.account_id,
                       policy={"initial_strategy": "new_only", "incremental_pages": 2})
    result = complete(db, job)
    assert calls == [1, 1, 1, 2]
    assert len(children(db)) == 1
    assert result["monitor_state"]["status"] == "window_complete"
    assert result["monitor_state"]["counts"]["newly_favorited"] == 1
    assert db.get(CaptureRun, result["run_id"]).status == "window_complete"
    assert collection.monitor_state["last_full_scan_at"] == NOW.isoformat()


def test_periodic_full_scan_finds_old_unseen_tail_without_deleting_archive(db, monkeypatch):
    phase = [0]
    class Client(FakeClient):
        def favorite_page(self, _, number):
            if phase[0] == 0:
                return page([favorite(1)])
            return page([favorite(2, added=int((NOW - timedelta(days=2)).timestamp()))])
    monkeypatch.setattr(runner, "BiliClient", Client)
    monkeypatch.setattr(runner, "_now", lambda: NOW)
    collection, job = source_job(db)
    complete(db, job)
    old_video = db.scalar(select(Video).where(Video.aid == "1"))
    phase[0] = 1
    monkeypatch.setattr(runner, "_now", lambda: NOW + timedelta(hours=25))
    _, later = source_job(db, collection=collection, account_id=job.account_id)
    result = complete(db, later)
    assert result["monitor_state"]["scan_mode"] == "full"
    assert result["monitor_state"]["counts"]["new_items"] == 1
    assert result["monitor_state"]["counts"]["newly_favorited"] == 0
    assert result["monitor_state"]["counts"]["not_observed"] == 1
    assert db.get(Video, old_video.id) is not None
    assert db.scalar(select(CollectionItem).where(CollectionItem.source_resource_id == "2:1")).source_state == "not_observed"


def test_changed_head_or_count_never_marks_unseen_or_advances_baseline(db, monkeypatch):
    calls = [0]
    class Client(FakeClient):
        def favorite_page(self, _, number):
            calls[0] += 1
            return page([favorite(1 if calls[0] == 1 else 2)])
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db)
    old = CollectionItem(collection_id=collection.id, source_resource_id="2:999", source_state="available")
    db.add(old); db.commit()
    result = complete(db, job)
    assert result["monitor_state"]["status"] == "unstable"
    assert "baseline_completed_at" not in collection.monitor_state
    assert old.source_state == "available"
    assert db.get(CaptureRun, result["run_id"]).status == "unstable"


@pytest.mark.parametrize("response", [
    {"medias": [], "has_more": True},
    {"medias": [], "has_more": False, "info": {"media_count": 8}},
    {"medias": [], "has_more": "false"},
    {"medias": [], "has_more": 0.0},
    {"medias": [favorite(1), favorite(1)], "has_more": False},
])
def test_invalid_favorite_pages_are_not_empty_success(db, monkeypatch, response):
    class Client(FakeClient):
        def favorite_page(self, *_): return deepcopy(response)
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db)
    with pytest.raises(IngestError) as error:
        runner.run_job(db, job)
    assert error.value.code == "invalid_pagination"
    assert collection.monitor_state["status"] == "partial"
    assert "baseline_completed_at" not in collection.monitor_state
    assert not children(db)


def test_page_loop_and_rate_limit_keep_durable_cursor(db, monkeypatch):
    mode = ["rate"]
    calls = []
    class Client(FakeClient):
        def favorite_page(self, _, number):
            calls.append(number)
            if number == 2 and mode[0] == "rate":
                raise IngestError("slow down", code="rate_limited")
            return page([favorite(1)], more=True, total=2)
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db)
    with pytest.raises(IngestError) as error:
        runner.run_job(db, job)
    assert error.value.code == "rate_limited"
    assert job.checkpoint["source_scan"]["page"] == 2
    # The account cooldown is separately tested by the throttle tests.
    account = db.get(__import__("app.models", fromlist=["SourceAccount"]).SourceAccount, job.account_id)
    account.cooldown_until = None
    db.commit()
    mode[0] = "loop"
    with pytest.raises(IngestError) as error:
        runner.run_job(db, job)
    assert error.value.code == "pagination_loop"
    assert calls == [1, 2, 2]
    assert len(children(db)) == 1 and not collection.monitor_state.get("last_full_scan_at")


def test_creator_profile_two_pages_and_attributed_published_detection(db, monkeypatch):
    calls = []
    class Client(FakeClient):
        def profile(self, uid):
            calls.append("profile")
            return {"mid": uid, "name": "creator display name", "sign": "public intro"}
        def creator_page(self, uid, number):
            calls.append(number)
            indices = range(1, 31) if number == 1 else [31]
            return {"page": {"pn": number, "ps": 30, "count": 31}, "list": {"vlist": [
                {"aid": index, "bvid": f"BV{index:010d}", "title": str(index), "created": int(NOW.timestamp())} for index in indices]}}
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db, kind="creator", policy={"request_budget": 1, "initial_strategy": "latest", "initial_limit": 2})
    result = complete(db, job)
    assert calls == ["profile", 1, 2, 1]
    assert len(children(db)) == 2
    assert db.scalar(select(func.count()).select_from(Creator)) == 1
    assert db.scalar(select(func.count()).select_from(VideoCreator)) == 31
    assert collection.owner_uid == "123" and collection.title == "preserve custom title"
    assert result["monitor_state"]["counts"]["observed"] == 31


def test_attr_four_is_observed_and_archived_but_existing_failure_is_not_reset(db, monkeypatch):
    class Client(FakeClient):
        def favorite_page(self, *_): return page([favorite(1, attr=4), favorite(2)])
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db)
    existing = Video(bvid="BV0000000002", aid="2")
    db.add(existing); db.flush()
    db.add(Job(kind="archive_video", target_id=existing.id, status="cancelled", dedupe_key="user-stopped"))
    db.commit()
    complete(db, job)
    rows = list(db.scalars(select(CollectionItem).order_by(CollectionItem.source_resource_id)))
    assert rows[0].source_state == "available" and rows[0].observation["archive_decision"] == "queued"
    assert rows[1].observation["archive_decision"] == "needs_attention"
    assert len(children(db)) == 2  # one cancelled historical job, one new job


def test_cross_source_reuse_requires_completed_matching_capture_policy(db, monkeypatch):
    class Client(FakeClient):
        def favorite_page(self, *_): return page([favorite(1)])
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db)
    video = Video(bvid="BV0000000001", aid="1", capture_status="complete",
                  metadata_json={"capture_policy": runner._capture_policy(job.policy)})
    db.add(video); db.commit()
    complete(db, job)
    assert len(children(db)) == 0 and collection.monitor_state["counts"]["reused"] == 1
    other = Collection(source_id="456", title="second source")
    db.add(other); db.commit()
    _, second = source_job(db, collection=other, account_id=job.account_id, policy={"quality": "720p"})
    complete(db, second)
    assert len(children(db)) == 1
    assert db.scalar(select(func.count()).select_from(Video)) == 1


def test_empty_source_is_valid_only_with_consistent_termination(db, monkeypatch):
    calls = []
    class Client(FakeClient):
        def favorite_page(self, _, number):
            calls.append(number)
            return {"medias": None, "has_more": False, "info": {"media_count": 0}}
    monkeypatch.setattr(runner, "BiliClient", Client)
    collection, job = source_job(db)
    complete(db, job)
    assert calls == [1, 1] and collection.monitor_state["status"] == "complete"
    assert collection.monitor_state["counts"]["observed"] == 0
