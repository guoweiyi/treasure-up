import json
import pytest

from sqlalchemy import func, select

from app.ingest import runner
from app.ingest.errors import IngestError
from app.models import Asset, CaptureRun, Comment, CommentAsset, PlatformUser, Video
from test_ingest_runner import FakeClient, comment, db, setup  # noqa: F401


def row(rpid, likes, *, replies=0, pictures=(), emote=False):
    value = comment(str(rpid), replies=replies, member=False)
    value["like"] = likes
    value["content"]["pictures"] = [{"img_src": url, "huge_unused": "DROP" * 1000} for url in pictures]
    if emote:
        value["content"].update(message="你好[doge]", emote={"[doge]": {"url": "https://i0.hdslb.com/emote.png", "unused": "DROP" * 1000}})
    value["unbounded_raw"] = "DROP" * 1000
    return value


def prepare(db, policy):
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    return video, setup(db, "refresh_comments", video.id, policy)


def test_permanently_missing_comment_avatar_records_omission_without_retry_loop(db, monkeypatch):
    calls = []
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            return {"cursor": {"is_end": True}, "replies": [comment("1")]}
        def asset(self, url, **kwargs):
            calls.append(url)
            raise IngestError("gone", code="asset_not_found", retryable=False)
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": True, "comment_reply_total_limit": 0})
    result = runner.run_job(db, job)
    assert not result["continuation"] and result["unavailable_images"] >= 1
    assert not job.checkpoint["images"]
    assert db.scalar(select(func.count()).select_from(Comment)) == 1
    run = db.get(CaptureRun, job.checkpoint["run_id"])
    assert run.status == "bounded_complete" and run.end_reason == "source_images_unavailable"
    previous = len(calls)
    assert not runner.run_job(db, job)["continuation"]
    assert len(calls) == previous


def test_top_candidates_only_create_people_and_assets_after_bounded_selection(db, monkeypatch):
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            rows = [row(1, 1), row(2, 20)] if not offset else [row(3, 99), row(4, 2)]
            for value in rows:
                value["member"] = {"mid": value["rpid_str"], "uname": "user", "avatar": "https://i0.hdslb.com/avatar.jpg"}
                value["replies"] = [row(999, 1000)] * 100
            return {"cursor": {"mode": 3, "is_end": False, "pagination_reply": {"next_offset": "a" if not offset else "b"}}, "replies": rows}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": False, "request_budget": 1, "comment_top_limit": 2, "comment_scan_limit": 4})
    assert runner.run_job(db, job)["continuation"]
    assert db.scalar(select(func.count()).select_from(Comment)) == 0
    assert db.scalar(select(func.count()).select_from(PlatformUser)) == 0
    assert not job.checkpoint["images"]
    assert "DROP" not in json.dumps(job.checkpoint) and "replies" not in job.checkpoint["comments"]["candidates"]["1"]
    assert not runner.run_job(db, job)["continuation"]
    assert set(db.scalars(select(Comment.rpid))) == {"2", "3"}
    assert video.metadata_json["comment_capture"]["scanned_roots"] == 4
    assert db.get(CaptureRun, job.checkpoint["run_id"]).status == "bounded_complete"


def test_duplicate_pages_with_new_cursors_stop_without_claiming_completion(db, monkeypatch):
    calls = []
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            calls.append(offset)
            return {"cursor": {"is_end": False, "pagination_reply": {"next_offset": str(len(calls))}}, "replies": [row(1, 1)]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": False, "comment_scan_limit": 3})
    with pytest.raises(IngestError) as error:
        runner.run_job(db, job)
    assert error.value.code == "pagination_loop" and len(calls) == 2
    assert job.checkpoint["comments"]["scanned"] == 1
    assert not job.checkpoint["comments"].get("roots_done")


def test_constant_opaque_comment_cursor_resumes_legacy_checkpoint_with_new_ids(db, monkeypatch):
    calls = []
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            calls.append(offset)
            ids = (1, 2) if len(calls) == 1 else (3, 4)
            return {"cursor": {"mode": 3, "is_end": False, "next": 0, "prev": 0,
                "all_count": 3620, "pagination_reply": {"next_offset": "opaque-constant"}},
                "replies": [row(value, value) for value in ids]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": False, "request_budget": 1, "comment_top_limit": 4, "comment_scan_limit": 4})
    assert runner.run_job(db, job)["continuation"]
    # Existing jobs from before the fix have seen_ids/candidates/offset but no
    # per-page fingerprint. They must resume without restarting the scan.
    checkpoint = dict(job.checkpoint)
    checkpoint["comments"] = {key: value for key, value in checkpoint["comments"].items() if key != "root_fingerprints"}
    job.checkpoint = checkpoint; db.commit()
    assert not runner.run_job(db, job)["continuation"]
    assert calls == ["", "opaque-constant"] and set(db.scalars(select(Comment.rpid))) == {"1", "2", "3", "4"}
    assert video.metadata_json["comment_capture"]["end_reason"] == "scan_budget"
    assert not video.metadata_json["comment_capture"]["source_end_observed"]


def test_replies_have_durable_total_and_per_root_inventory_caps(db, monkeypatch):
    calls = []
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            values = [row(1, 2, replies=100), row(2, 1, replies=100)]
            values[0]["replies"] = [row(100 + i, 1) for i in range(50)]
            return {"cursor": {"is_end": True}, "replies": values}
        def replies_page(self, aid, root, page):
            calls.append(root)
            return {"page": {"num": page, "size": 20, "count": 100},
                "replies": [comment(str(int(root)*100+i), root=root, parent=root, member=False) for i in range(20)]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": False, "request_budget": 1,
        "comment_reply_total_limit": 3, "comment_reply_per_root_limit": 2})
    assert [runner.run_job(db, job)["continuation"] for _ in range(3)] == [True, True, False]
    assert calls == ["1", "2"]
    assert db.scalar(select(func.count()).select_from(Comment).where(Comment.root_rpid != "0")) == 3
    again = setup(db, "refresh_comments", video.id, dict(job.policy))
    assert not runner.run_job(db, again)["continuation"]
    assert calls == ["1", "2"]  # no more replies requested once inventory is full


def test_refresh_does_not_grow_full_inventory_or_delete_history(db, monkeypatch):
    batch = [row(1, 2), row(2, 1)]
    class Source(FakeClient):
        def comment_page(self, *args): return {"cursor": {"is_end": True}, "replies": batch}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": False, "comment_top_limit": 2})
    assert not runner.run_job(db, job)["continuation"]
    batch[:] = [row(2, 80), row(3, 999)]
    another = setup(db, "refresh_comments", video.id, dict(job.policy))
    assert not runner.run_job(db, another)["continuation"]
    assert set(db.scalars(select(Comment.rpid))) == {"1", "2"}
    assert db.scalar(select(Comment.like_count).where(Comment.rpid == "2")) == 80
    assert video.metadata_json["comment_capture"]["retention"] == "preserve_existing_except_pin_priority"


def test_comment_assets_are_deduplicated_thumbnails_and_emotes_have_tokens(db, monkeypatch):
    calls = []
    class Source(FakeClient):
        def comment_page(self, *args):
            return {"cursor": {"is_end": True}, "replies": [row(i, i, pictures=["https://i0.hdslb.com/pic.jpg"], emote=True) for i in (1, 2)]}
        def asset(self, url, **kwargs):
            calls.append(url)
            return b"\xff\xd8\xff" + (b"emote" if "96w" in url else b"image"), "image/jpeg"
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": True, "comment_asset_count_limit": 2, "comment_asset_bytes_limit": 100})
    assert not runner.run_job(db, job)["continuation"]
    assert len(calls) == 2 and all("@" in url for url in calls)
    assert set(db.scalars(select(CommentAsset.kind))) == {"attachment", "emote"}
    for item in db.scalars(select(Comment)):
        assert any(a["token"] == "[doge]" and a["asset_id"] for a in item.raw["asset_manifest"])
    again = setup(db, "refresh_comments", video.id, dict(job.policy))
    assert not runner.run_job(db, again)["continuation"]
    assert len(calls) == 2
    assert sum(item["bytes"] for item in video.metadata_json["comment_assets"].values()) == 16


def test_asset_count_bytes_and_refresh_url_changes_cannot_expand_budget(db, monkeypatch):
    calls, current = [], ["https://i0.hdslb.com/one.jpg", "https://i0.hdslb.com/two.jpg", "https://i0.hdslb.com/three.jpg"]
    class Source(FakeClient):
        def comment_page(self, *args): return {"cursor": {"is_end": True}, "replies": [row(1, 1, pictures=current)]}
        def asset(self, url, **kwargs):
            calls.append((url, kwargs["limit"]))
            return b"\xff\xd8\xff" + b"x" * 9, "image/jpeg"
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": True, "comment_asset_count_limit": 2, "comment_asset_bytes_limit": 20})
    assert not runner.run_job(db, job)["continuation"]
    assert [limit for _, limit in calls] == [20, 8]
    assert db.scalar(select(func.count()).select_from(Asset)) == 1
    assert not job.checkpoint["images"] and job.checkpoint["comment_assets_limited"]
    current[:] = ["https://i0.hdslb.com/new.jpg"]
    another = setup(db, "refresh_comments", video.id, dict(job.policy))
    assert not runner.run_job(db, another)["continuation"]
    assert len(calls) == 2
    assert sum(item["bytes"] for item in video.metadata_json["comment_assets"].values()) == 12


def test_shared_noncomment_image_cache_does_not_bypass_comment_byte_budget(db):
    video, job = prepare(db, {"images": True, "comment_asset_bytes_limit": 5})
    class Source(FakeClient):
        def asset(self, *_a, **_k): return b"\xff\xd8\xff" + b"x" * 20, "image/jpeg"
    ctx = runner.Context(db, job, Source())
    ctx.run.video_id = video.id
    comment_row = Comment(video_id=video.id, rpid="1", content="fixture")
    db.add(comment_row); db.flush()
    url = "https://i0.hdslb.com/shared.jpg"
    ctx.image(url, "video", video.id, "cover")
    ctx.image(url, "comment", comment_row.id, "attachment", comment_asset=True)
    assert runner._images(ctx)
    assert video.cover_asset_id
    assert db.scalar(select(func.count()).select_from(CommentAsset)) == 0
    assert sum(item["bytes"] for item in video.metadata_json["comment_assets"].values()) == 0


def test_legacy_assets_are_counted_before_refresh_can_add_new_material(db, monkeypatch):
    urls = ["https://i0.hdslb.com/old.jpg"]
    calls = []
    class Source(FakeClient):
        def comment_page(self, *args): return {"cursor": {"is_end": True}, "replies": [row(1, 1, pictures=urls)]}
        def asset(self, url, **kwargs):
            calls.append(url)
            return b"\xff\xd8\xff" + b"fixture", "image/jpeg"
    monkeypatch.setattr(runner, "BiliClient", Source)
    video, job = prepare(db, {"images": True, "comment_asset_count_limit": 1})
    assert not runner.run_job(db, job)["continuation"]
    video.metadata_json = {k: v for k, v in video.metadata_json.items() if k != "comment_assets"}
    db.commit()
    urls[:] = ["https://i0.hdslb.com/new.jpg"]
    another = setup(db, "refresh_comments", video.id, dict(job.policy))
    assert not runner.run_job(db, another)["continuation"]
    assert len(calls) == 1 and video.metadata_json["comment_assets"]["legacy"]["count"] == 1
