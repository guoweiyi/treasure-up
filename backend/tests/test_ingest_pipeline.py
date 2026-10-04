"""Metadata-first queues, short account locks and honest media observations."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.ingest import media, runner
from app.ingest.errors import IngestDeferred
from app.ingest.throttle import AccountPacer
from app.models import Job, MediaVariant, PlatformUser, SourceAccount, UserSnapshot, Video, VideoCreator, VideoPart
from test_ingest_runner import FakeClient, db, setup  # noqa: F401


@pytest.mark.parametrize("old_response", ["success", "login_required"])
def test_old_cookie_response_never_changes_rotated_account_identity(db, monkeypatch, old_response):
    from sqlalchemy import update
    from app.ingest.errors import IngestError
    job = setup(db, "verify_account", "unused")
    class Source(FakeClient):
        def nav(self):
            # Credentials rotate after the old request was sent, before either
            # a successful nav or a source -101 arrives.
            db.execute(update(SourceAccount).where(SourceAccount.id == job.account_id)
                .values(secret_encrypted="rotated", uid="999", status="valid")
                .execution_options(synchronize_session=False))
            db.commit()
            if old_response == "login_required":
                raise IngestError("expired old session", code="login_required", retryable=False)
            return {"isLogin": True, "mid": "123"}
    monkeypatch.setattr(runner, "BiliClient", Source)
    with pytest.raises(IngestDeferred) as error:
        runner.run_job(db, job)
    assert error.value.code == "account_changed"
    db.expire_all()
    account = db.get(SourceAccount, job.account_id)
    assert account.uid == "999" and account.status == "valid" and account.risk_failures == 0


def test_component_failure_allows_one_media_child_but_preserves_partial_until_repaired(db, monkeypatch):
    from app.ingest.errors import IngestError, PartialCaptureError
    from app.models import CaptureRun
    from app.config import settings
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    video = Video(bvid="BV1234567890", aid="1")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="1", duration=10)
    db.add(part); db.flush()
    job = setup(db, "archive_video", video.id, {"images": False, "subtitles": False, "create_compatible_copy": False})
    job.checkpoint = {"metadata_done": True, "basics_done": True, "part_ids": [part.id]}; db.commit()
    def fail(code):
        def operation(*args): raise IngestError("component failed", code=code)
        return operation
    monkeypatch.setattr(runner, "_danmaku", fail("invalid_danmaku"))
    monkeypatch.setattr(runner, "_comments", fail("pagination_loop"))
    for _ in range(2):
        with pytest.raises(PartialCaptureError):
            runner.run_job(db, job)
    assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "download_media")) == 1
    child = db.get(Job, job.checkpoint["media_job_id"])
    assert child.checkpoint["metadata_parent_id"] == job.id
    assert set(job.result["components"]) == {"invalid_danmaku", "pagination_loop"}
    assert db.get(CaptureRun, job.checkpoint["run_id"]).status == "partial"
    assert video.metadata_json["ingest_state"]["metadata"] == "partial" and video.capture_status == "partial"
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    path = settings.scratch_dir / "fixture.mp4"; path.write_bytes(b"offline fixture")
    asset = runner.ingest_file(db, path, kind="media", mime_type="video/mp4")
    variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="fixture")
    db.add(variant); child.status = "running"; db.commit()
    monkeypatch.setattr(runner, "archive_media", lambda *args, **kwargs: (variant, False))
    assert not runner.run_job(db, child)["continuation"]
    assert video.capture_status == "partial" and video.metadata_json["ingest_state"]["media"] == "complete"
    child.status = "succeeded"; db.commit()
    monkeypatch.setattr(runner, "_danmaku", lambda *args: True)
    monkeypatch.setattr(runner, "_comments", lambda *args: True)
    assert not runner.run_job(db, job)["continuation"]
    assert video.capture_status == "complete" and video.metadata_json["ingest_state"]["component_errors"] == []
    assert child.status == "succeeded" and db.scalar(select(func.count()).select_from(Job).where(Job.kind == "download_media")) == 1


def test_source_risk_does_not_spawn_media_from_a_component_failure(db, monkeypatch):
    from app.ingest.errors import IngestError
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    video = Video(bvid="BV1234567890", aid="1")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="1", duration=10)
    db.add(part); db.flush()
    job = setup(db, "archive_video", video.id)
    job.checkpoint = {"metadata_done": True, "basics_done": True, "part_ids": [part.id]}; db.commit()
    def limited(*args): raise IngestError("source rejection", code="rate_limited")
    monkeypatch.setattr(runner, "_danmaku", limited)
    with pytest.raises(IngestError):
        runner.run_job(db, job)
    assert not job.checkpoint.get("media_job_id")
    assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "download_media")) == 0
    assert db.get(SourceAccount, job.account_id).cooldown_until is not None


def test_three_video_cards_and_profiles_are_ready_before_any_media_download(db, monkeypatch):
    events = []
    class Source(FakeClient):
        def view(self, bvid):
            events.append(("view", bvid))
            return {"bvid": bvid, "aid": bvid[-1], "title": bvid, "pic": "https://i0.hdslb.com/cover.jpg",
                "owner": {"mid": 1, "name": "author", "face": "https://i0.hdslb.com/face.jpg"},
                "staff": [{"mid": 2, "name": "camera", "title": "摄影"}],
                "pages": [{"cid": bvid[-1], "duration": 10}]}
        def profile(self, uid):
            events.append(("profile", uid))
            return {"mid": uid, "name": "author " + uid, "sign": "保存的简介", "face": "https://i0.hdslb.com/face.jpg"}
        def asset(self, url, **kwargs):
            events.append(("image", url))
            return b"\xff\xd8\xfffixture", "image/jpeg"
        def comment_page(self, *args):
            events.append(("comments", ""))
            return {"cursor": {"is_end": True}, "replies": []}
        def segment(self, *args):
            events.append(("danmaku", ""))
            return b""
    monkeypatch.setattr(runner, "BiliClient", Source)
    monkeypatch.setattr(runner, "archive_media", lambda *_a, **_k: pytest.fail("metadata worker downloaded media"))
    jobs = [setup(db, "archive_video", f"BV123456789{i}", {"subtitles": False}) for i in (1, 2, 3)]
    for job in jobs:
        assert runner.run_job(db, job)["continuation"]
    assert len(list(db.scalars(select(Video)))) == 3
    assert all(v.cover_asset_id for v in db.scalars(select(Video)))
    assert all(u.signature == "保存的简介" and u.avatar_asset_id for u in db.scalars(select(PlatformUser)))
    assert not any(event[0] in {"comments", "danmaku"} for event in events)
    assert any("@160w_160h_1c.webp" in event[1] for event in events if event[0] == "image")
    assert all(r.role_title == "摄影" for r in db.scalars(select(VideoCreator).where(VideoCreator.role == "staff")))
    for job in jobs:
        assert not runner.run_job(db, job)["continuation"]
        child = db.get(Job, job.checkpoint["media_job_id"])
        assert child.kind == "download_media" and child.policy == job.policy
        assert child.checkpoint["part_ids"]
    assert all(v.capture_status == "metadata_ready" for v in db.scalars(select(Video)))
    assert db.scalar(select(func.count()).select_from(MediaVariant)) == 0


def test_simplified_author_cannot_clear_full_profile_or_avatar(db, monkeypatch):
    ctx = runner.Context(db, setup(db, "archive_video", "unused"), FakeClient())
    user, _, _ = runner._user(ctx, {"mid": 1, "name": "作者", "sign": "已有简介", "face": "https://i0.hdslb.com/a.jpg"}, creator=True, full_profile=True)
    runner._user(ctx, {"mid": 1, "uname": "作者", "sign": "", "avatar": ""})
    assert user.signature == "已有简介" and user.raw["avatar_url"].endswith("a.jpg")
    assert user.raw["public_profile"]["sign"] == "已有简介"
    assert len(list(db.scalars(select(UserSnapshot)))) == 1
    runner._user(ctx, {"mid": 1, "name": "作者", "sign": ""}, full_profile=True)
    assert user.signature == ""  # A genuine later profile update can clear it.


def test_legacy_media_checkpoint_is_transferred_without_reviving_cancelled_child(db, monkeypatch):
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    video = Video(bvid="BV1234567890", aid="1")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="1", duration=10)
    db.add(part); db.flush()
    job = setup(db, "archive_video", video.id, {"profiles": False, "comments": False,
        "danmaku": False, "subtitles": False, "images": False})
    job.checkpoint = {"metadata_done": True, "part_ids": [part.id], "media_parts": [part.id],
        "media_variants": {part.id: "saved-variant"}}
    db.commit()
    assert not runner.run_job(db, job)["continuation"]
    child = db.get(Job, job.checkpoint["media_job_id"])
    assert child.checkpoint["media_variants"] == {part.id: "saved-variant"}
    child.status = "cancelled"; db.commit()
    assert not runner.run_job(db, job)["continuation"]
    assert child.status == "cancelled"
    assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "download_media")) == 1


def test_download_holds_distinct_lane_lock_and_short_api_lock_only(db, monkeypatch):
    held, observed = set(), []
    @contextmanager
    def lock(_db, key):
        assert key not in held
        held.add(key)
        try:
            yield
        finally:
            held.remove(key)
    monkeypatch.setattr(runner, "_lock", lock)
    class Source(FakeClient):
        def nav(self):
            with self.request_context("https://api.bilibili.com/x/web-interface/nav"):
                assert not any(key.startswith("account:") for key in held)
            return super().nav()
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="1")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="1", duration=10)
    db.add(part); db.flush()
    job = setup(db, "download_media", video.id, {"create_compatible_copy": False})
    job.checkpoint = {"part_ids": [part.id]}; db.commit()
    def download(session, client, _video, _part, policy, **kwargs):
        assert any(key.startswith("download-account:") for key in held)
        assert not any(key.startswith("account:") for key in held)
        with client.request_context("https://api.bilibili.com/x/player/wbi/playurl"):
            assert "account:" + job.account_id not in held
        assert "account:" + job.account_id not in held
        with client.request_context("https://example.bilivideo.com/media.m4s"):
            assert "account:" + job.account_id not in held
        observed.append(True)
        raise IngestDeferred("fixture stop", retry_after_seconds=10)
    monkeypatch.setattr(runner, "archive_media", download)
    with pytest.raises(IngestDeferred):
        runner.run_job(db, job)
    assert observed and not held


def test_api_contention_is_deferred_instead_of_becoming_component_failure(db, monkeypatch):
    class Source(FakeClient):
        def view(self, bvid):
            return {"bvid": bvid, "aid": "1", "owner": {"mid": 1}, "pages": [{"cid": 1, "duration": 10}]}
        def profile(self, uid):
            raise IngestDeferred("another account request", code="resource_busy")
    monkeypatch.setattr(runner, "BiliClient", Source)
    job = setup(db, "archive_video", "BV1234567890")
    with pytest.raises(IngestDeferred):
        runner.run_job(db, job)
    assert job.checkpoint["metadata_done"] and not job.checkpoint.get("basics_done")
    assert not job.checkpoint.get("media_job_id")


def test_cdn_images_do_not_spend_api_interval_or_skip_cooldown(db):
    account = SourceAccount(name="test", secret_encrypted="fixture")
    db.add(account); db.commit()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account.next_request_at = now + timedelta(seconds=60)
    pacer = AccountPacer(db, account, {}, lambda: None, clock=lambda: now,
        sleep=lambda *_: pytest.fail("public image used API pacing"))
    pacer.before_request("https://i0.hdslb.com/avatar.jpg@160w.webp")
    assert account.next_request_at == now + timedelta(seconds=60)
    account.cooldown_until = now + timedelta(seconds=30)
    with pytest.raises(IngestDeferred):
        pacer.before_request("https://i0.hdslb.com/avatar.jpg")


def test_video_spacing_defers_instead_of_sleeping_while_holding_account(db):
    account = SourceAccount(name="test", secret_encrypted="fixture")
    db.add(account); db.commit()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account.next_video_at = now + timedelta(seconds=60)
    pacer = AccountPacer(db, account, {}, lambda: None, clock=lambda: now,
        sleep=lambda *_: pytest.fail("worker occupied by long spacing"))
    with pytest.raises(IngestDeferred) as failure:
        pacer.before_video(defer=True)
    assert failure.value.retry_after_seconds == 60


def test_source_available_formats_and_saved_bitrate_evidence_are_distinct():
    video = SimpleNamespace(metadata_json={"media_properties": {"saved": {"video_bitrate_bps": 100}}})
    part = SimpleNamespace(id="part", cid="1")
    media.record_available_formats(video, part, {"formats": [
        {"format_id": "4k", "width": 2160, "height": 3840, "fps": 60, "vcodec": "av01", "acodec": "none", "tbr": 10000, "url": "https://cdn/?token=secret"},
        {"format_id": "1080", "width": 1920, "height": 1080, "fps": 120, "vcodec": "avc1", "acodec": "none"},
        {"format_id": "audio", "vcodec": "none", "acodec": "aac", "tbr": 320}]})
    observed = video.metadata_json["source_quality"]["parts"]["part"]
    assert observed["maximum"]["quality"] == "2160p"
    assert observed["maximum"]["video_bitrate_bps"] == 10_000_000
    assert observed["formats"][1]["video_bitrate_bps"] is None
    assert observed["maximum_audio"]["audio_bitrate_bps"] == 320_000
    assert "token" not in str(video.metadata_json)
    assert video.metadata_json["media_properties"]["saved"]["video_bitrate_bps"] == 100
    measured = media._measured_properties({"bit_rate": "800000"}, {}, 10, 1_000_000)
    assert measured["video_bitrate_bps"] == 800_000
    assert measured["audio_bitrate_bps"] is None
    assert measured["total_bitrate_basis"] == "file_size_over_duration"


@pytest.mark.parametrize("status, expected", [("queued", "active"), ("running", "active"),
    ("paused", "active"), ("blocked", "active"), ("cancelled", "needs_attention"), ("failed", "needs_attention")])
def test_source_scan_does_not_reschedule_metadata_while_media_child_exists(db, status, expected):
    video = Video(bvid="BV1234567890", capture_status="metadata_ready")
    db.add(video); db.flush()
    parent = setup(db, "scan_collection", "collection")
    ctx = runner.Context(db, parent, FakeClient())
    db.add_all([Job(kind="archive_video", target_id=video.id, status="succeeded", policy={}, dedupe_key="parent"),
        Job(kind="download_media", target_id=video.id, status=status, policy={}, dedupe_key="child")])
    db.commit()
    assert runner._enqueue_archive(ctx, video) == expected
    assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "archive_video")) == 1


@pytest.mark.parametrize("child_status", ["succeeded", "failed", "cancelled", "paused"])
def test_resumed_media_does_not_relabel_or_restart_existing_playback(db, monkeypatch, child_status):
    from app.config import settings
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    video = Video(bvid="BV1234567890", aid="1")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="1", duration=10)
    db.add(part); db.flush()
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    payload = settings.scratch_dir / "original.mp4"
    payload.write_bytes(b"fixture")
    asset = runner.ingest_file(db, payload, kind="media", mime_type="video/mp4")
    archive = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="fixture")
    db.add(archive); db.flush()
    playback = Job(kind="create_playback", target_id=archive.id, status=child_status, policy={}, dedupe_key="playback:" + archive.id)
    db.add(playback); db.commit()
    job = setup(db, "download_media", video.id)
    job.checkpoint = {"part_ids": [part.id], "media_parts": [part.id], "media_variants": {part.id: archive.id}}
    db.commit()
    monkeypatch.setattr(runner, "archive_media", lambda *_a, **_k: pytest.fail("already saved"))
    assert not runner.run_job(db, job)["continuation"]
    assert playback.status == child_status
    assert video.metadata_json["ingest_state"]["playback"] == ("complete" if child_status == "succeeded" else child_status)


@pytest.mark.parametrize("interruption", ["reserve_again", "cooldown"])
def test_request_ignores_legacy_interval_but_rechecks_cooldown_and_credentials(db, monkeypatch, interruption):
    account = SourceAccount(name="fixture", secret_encrypted="old")
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    account.next_request_at = clock[0] + timedelta(seconds=2)
    db.add(account); db.commit()
    held, acquisitions, waits = [], [], []
    @contextmanager
    def lock(_db, key):
        assert not held
        if not acquisitions:
            # A different lane reserved another slot or received a challenge
            # after we began waiting. Also rotate credentials in that window.
            account.secret_encrypted = "rotated"
            if interruption == "reserve_again":
                account.next_request_at = clock[0] + timedelta(seconds=2)
            else:
                account.cooldown_until = clock[0] + timedelta(seconds=30)
            db.commit()
        acquisitions.append(key)
        held.append(key)
        try:
            yield
        finally:
            held.pop()
    def sleep(seconds):
        assert not held, "API interval must never occupy the account lock"
        waits.append(seconds)
        clock[0] += timedelta(seconds=seconds)
    monkeypatch.setattr(runner, "_lock", lock)
    pacer = AccountPacer(db, account, {"request_interval_seconds": 3}, lambda: None,
        clock=lambda: clock[0], sleep=sleep, jitter=lambda _: 0)
    client = SimpleNamespace(cookies=[])
    secrets = []
    def decrypt(ciphertext):
        secrets.append(ciphertext)
        return "SESSDATA=fixture"
    def request():
        with runner.account_request_scope(db, account, client, pacer,
                "https://api.bilibili.com/x/web-interface/nav", decryptor=decrypt):
            assert not held
            pacer.before_request("https://api.bilibili.com/x/web-interface/nav")
    if interruption == "cooldown":
        with pytest.raises(IngestDeferred):
            request()
        assert not secrets
    else:
        request()
        assert len(acquisitions) == 1 and not waits
        assert secrets == ["rotated"]
    assert not held and not waits
