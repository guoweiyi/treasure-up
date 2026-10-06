"""Upstream tag and pin metadata survives bounded capture and later refresh."""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app import catalog
from app.ingest import runner
from app.ingest.client import BiliClient
from app.ingest.errors import IngestError, PartialCaptureError
from app.models import (Asset, AssetRef, Comment, CommentAsset, CommentVersion, Creator, PlatformUser,
                        Video, VideoAnnotation, VideoCreator, VideoPart, VideoStatSnapshot)
from test_ingest_runner import FakeClient, comment, db, setup  # noqa: F401


def test_tags_endpoint_and_archive_step_preserve_manual_annotations(db):
    calls = []
    def transport(request):
        calls.append((request.url.path, request.url.params["bvid"]))
        return httpx.Response(200, json={"code": 0, "data": [{"tag_name": "音乐"}, {"tag_name": "现场"}, {"tag_name": "音乐"}]})
    video = Video(bvid="BV1234567890", source_tags=["过期标签"])
    db.add(video); db.flush()
    annotation = VideoAnnotation(video_id=video.id, tags=["我的标签", "音乐"])
    db.add(annotation); db.commit()
    job = setup(db, "archive_video", video.id)
    client = BiliClient("SESSDATA=synthetic", transport=httpx.MockTransport(transport), interval=0)
    ctx = runner.Context(db, job, client)
    try:
        assert runner._tags(ctx, video)
        assert runner._tags(ctx, video)  # replay uses the durable checkpoint
    finally:
        client.close()
    assert calls == [("/x/tag/archive/tags", video.bvid)]
    assert annotation.tags == ["我的标签", "音乐"]
    payload = catalog.video_view(db, video)
    assert payload["source_tags"] == ["音乐", "现场"]
    assert payload["manual_tags"] == ["我的标签", "音乐"]
    assert payload["tags"] == ["音乐", "现场", "我的标签"]


def test_bad_tag_response_keeps_existing_tags_and_does_not_checkpoint_success(db):
    video = Video(bvid="BV1234567890", source_tags=["已有标签"])
    db.add(video); db.flush()
    job = setup(db, "archive_video", video.id)
    ctx = runner.Context(db, job, SimpleNamespace(tags=lambda _: {"unexpected": []}))
    with pytest.raises(IngestError, match="标签结构"):
        runner._tags(ctx, video)
    assert video.source_tags == ["已有标签"] and "tags_done" not in job.checkpoint


def test_pins_beat_likes_survive_duplicate_previews_and_have_uploader_badge(db, monkeypatch):
    pin, ordinary = comment("1"), comment("2")
    pin["like"], ordinary["like"] = 0, 99999
    class Source(FakeClient):
        def comment_page(self, *_):
            return {"cursor": {"is_end": True}, "top_replies": [pin], "replies": [pin, ordinary]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    job = setup(db, "refresh_comments", video.id, {"images": False, "comment_top_limit": 1})
    assert not runner.run_job(db, job)["continuation"]
    stored = db.scalar(select(Comment))
    assert stored.rpid == "1" and stored.is_pinned and stored.raw["is_pinned"] is True
    assert db.scalar(select(func.count()).select_from(Comment)) == 1
    creator = Creator(user_id=stored.author_user_id)
    db.add(creator); db.flush()
    db.add(VideoCreator(video_id=video.id, creator_id=creator.id, role="owner")); db.commit()
    displayed = catalog.comment_view(db, stored)
    assert displayed["is_uploader"] and displayed["is_pinned"]
    other = Video(bvid="BV1234567891", aid="124")
    db.add(other); db.flush()
    cross = Comment(video_id=other.id, rpid="8", author_user_id=stored.author_user_id)
    db.add(cross); db.commit()
    assert not catalog.comment_view(db, cross)["is_uploader"]  # A known UP is not this video's owner.


def test_duplicate_pin_containers_do_not_consume_three_distinct_pin_slots(db, monkeypatch):
    first = comment("1")
    numeric_first = {key: value for key, value in first.items() if key != "rpid_str"}
    numeric_first["rpid"] = 1
    payload = {"cursor": {"is_end": True}, "replies": [],
        "top_replies": [numeric_first, first, {**first, "rpid_str": "001"}],
        "top": {"upper": first, "admin": comment("2"), "vote": comment("3"), "other": comment("4")}}
    assert [item["rpid_str"] for item in runner._pinned(payload)] == ["1", "2", "3"]
    class Source(FakeClient):
        def comment_page(self, *_): return payload
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    job = setup(db, "refresh_comments", video.id, {"images": False, "comment_top_limit": 3})
    assert not runner.run_job(db, job)["continuation"]
    assert set(db.scalars(select(Comment.rpid).where(Comment.is_pinned.is_(True)))) == {"1", "2", "3"}
    assert db.scalar(select(func.count()).select_from(Comment)) == 3


def test_new_pin_replaces_lowest_ordinary_tree_within_cap_and_keeps_shared_assets(db, monkeypatch):
    rows, pins = [comment("1"), comment("2")], []
    rows[0]["like"], rows[1]["like"] = 1, 20
    class Source(FakeClient):
        def comment_page(self, *_):
            return {"cursor": {"is_end": True}, "top_replies": pins, "replies": rows}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    policy = {"images": False, "comment_top_limit": 2}
    assert not runner.run_job(db, setup(db, "refresh_comments", video.id, policy))["continuation"]
    victim = db.scalar(select(Comment).where(Comment.rpid == "1"))
    asset = Asset(sha256="e" * 64, size=123, kind="avatar")
    db.add(asset); db.flush()
    person = db.get(PlatformUser, victim.author_user_id)
    person.avatar_asset_id = asset.id
    child = Comment(video_id=video.id, rpid="11", root_rpid="1", parent_rpid="1", author_user_id=person.id)
    db.add(child); db.flush()
    db.add_all([CommentAsset(comment_id=victim.id, asset_id=asset.id, kind="attachment"),
        AssetRef(asset_id=asset.id, entity_type="comment", entity_id=victim.id, purpose="attachment"),
        AssetRef(asset_id=asset.id, entity_type="comment", entity_id=child.id, purpose="emote")])
    db.commit()
    removed_ids = {victim.id, child.id}
    pins[:] = [comment("3")]
    # Also returning the evicted row later in the candidate list cannot recreate
    # it and accidentally exceed the cap after the pin consumed its slot.
    rows[:] = [comment("3"), rows[0], rows[1]]
    assert not runner.run_job(db, setup(db, "refresh_comments", video.id, policy))["continuation"]
    assert set(db.scalars(select(Comment.rpid))) == {"2", "3"}
    assert db.scalar(select(Comment).where(Comment.rpid == "3")).is_pinned
    assert db.scalar(select(func.count()).select_from(CommentAsset)) == 0
    assert db.scalar(select(func.count()).select_from(AssetRef).where(AssetRef.entity_id.in_(removed_ids))) == 0
    assert db.scalar(select(func.count()).select_from(CommentVersion).where(CommentVersion.comment_id.in_(removed_ids))) == 0
    assert db.get(Asset, asset.id) and db.get(PlatformUser, person.id).avatar_asset_id == asset.id
    assert video.metadata_json["comment_capture"]["pin_replacements"] == 1
    pins.clear()
    assert not runner.run_job(db, setup(db, "refresh_comments", video.id, policy))["continuation"]
    assert not any(db.scalars(select(Comment.is_pinned)))


def test_statistics_refresh_captures_tags_and_pins_without_media(db, monkeypatch):
    class Source(FakeClient):
        def view(self, bvid): return {"bvid": bvid, "aid": 123, "stat": {"view": 999, "like": 10}}
        def tags(self, _): return [{"tag_name": "知识"}]
        def comment_page(self, *_): return {"cursor": {"is_end": True}, "top_replies": [comment("9")], "replies": []}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890")
    db.add(video); db.flush()
    job = setup(db, "refresh_stats", video.id, {"images": False, "refresh_comments": True, "refresh_tags": True})
    assert not runner.run_job(db, job)["continuation"]
    assert video.aid == "123" and video.source_tags == ["知识"]
    assert db.scalar(select(VideoStatSnapshot)).counts["view"] == 999
    assert db.scalar(select(Comment)).is_pinned
    assert not job.checkpoint.get("media_job_id")


def test_closed_comments_do_not_fail_default_statistics_refresh_or_erase_archive(db, monkeypatch):
    class Source(FakeClient):
        def view(self, bvid): return {"bvid": bvid, "aid": 123, "stat": {"view": 1}}
        def comment_page(self, *_): raise IngestError("closed", code="comments_closed", retryable=False)
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    saved = Comment(video_id=video.id, rpid="1", is_pinned=True, content="Archived pin")
    db.add(saved); db.commit()
    job = setup(db, "refresh_stats", video.id, {"images": False, "refresh_comments": True, "refresh_tags": True})
    assert not runner.run_job(db, job)["continuation"]
    assert db.get(Comment, saved.id).is_pinned and video.metadata_json["comment_capture"]["end_reason"] == "comments_closed"


def test_tag_failure_keeps_repairable_checkpoint_without_starving_media(db, monkeypatch):
    class Source(FakeClient):
        def tags(self, _): raise IngestError("temporary tag failure", code="network_error")
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="1")
    db.add(part); db.flush()
    job = setup(db, "archive_video", video.id, {"images": False, "media": True})
    job.checkpoint = {"metadata_done": True, "basics_done": True, "part_ids": [part.id]}
    db.commit()
    ctx = runner.Context(db, job, Source())
    ctx.run.video_id = video.id
    scheduled = []
    monkeypatch.setattr(runner, "_enqueue_media", lambda _ctx, item, **options: scheduled.append((item.id, options)))
    with pytest.raises(PartialCaptureError):
        runner._archive(ctx, video)
    assert scheduled == [(video.id, {"metadata_state": "partial"})]
    assert job.checkpoint["component_errors"] == ["network_error"] and not job.checkpoint.get("tags_done")


@pytest.mark.parametrize("uid,mode,paid,reason", [
    ("999", "all", False, "creator_changed"),
    ("456", "only", False, "capture_filter_changed"),
    ("456", "exclude", True, "capture_filter_changed"),
])
def test_capture_worker_revalidates_creator_and_paid_selection(db, uid, mode, paid, reason):
    video = Video(bvid="BV1234567890", title="Keep saved metadata")
    db.add(video); db.flush()
    job = setup(db, "archive_video", video.id, {"capture_creator_uid": "456", "capture_paid_filter": mode})
    source = SimpleNamespace(view=lambda bvid: {"bvid": bvid, "aid": 123, "title": "New title", "owner": {"mid": uid},
        "is_upower_exclusive": paid, "pages": [{"cid": "1"}]})
    ctx = runner.Context(db, job, source)
    with pytest.raises(IngestError) as failure:
        runner._metadata(ctx, video)
    assert failure.value.code == reason and video.title == "Keep saved metadata"
