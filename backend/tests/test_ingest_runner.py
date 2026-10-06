import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest import runner
from app.ingest.errors import IngestError
from app.ingest.media import selected_fingerprint
from app.ingest import media
from app.models import (Base, CaptureRun, Collection, CollectionItem, Comment,
    CommentVersion, Creator, DanmakuSnapshot, Job, MediaVariant, PlatformUser, SourceAccount,
    SubtitleTrack, UserSnapshot, Video, VideoCreator, VideoPart)
from app.storage.service import read_asset_bytes


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(runner, "decrypt_secret", lambda value: "SESSDATA=test-secret")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def setup(db, kind, target, policy=None):
    account = SourceAccount(name="mock", secret_encrypted="not-a-real-credential")
    db.add(account)
    db.flush()
    job = Job(kind=kind, target_id=target, account_id=account.id, status="running", policy=policy or {"images": False}, dedupe_key=str(uuid4()))
    db.add(job)
    db.commit()
    return job


class FakeClient:
    def __init__(self, *args, **kwargs): pass
    def close(self): pass
    def nav(self): return {"isLogin": True, "mid": "123", "vip": {"status": 1}}
    def tags(self, bvid): return []


def test_account_rotation_is_read_after_acquiring_account_lock(db, monkeypatch):
    job = setup(db, "verify_account", "unused")
    account = db.get(SourceAccount, job.account_id)
    observed = []

    @contextmanager
    def rotate_before_lock(_db, key):
        if observed:
            yield
            return
        assert key == "account:" + account.id
        # Simulate another session's completed rotation while this job waited;
        # leave the ORM identity map stale, as it is in a real concurrent job.
        db.execute(update(SourceAccount).where(SourceAccount.id == account.id)
                   .values(secret_encrypted="rotated-ciphertext")
                   .execution_options(synchronize_session=False))
        db.commit()
        observed.append("locked")
        yield

    def decrypt(value):
        assert observed == ["locked"]
        observed.append(value)
        return "rotated-test-cookie"

    class Source(FakeClient):
        def __init__(self, secret, **kwargs):
            assert secret == "rotated-test-cookie"
        def close(self):
            observed.append("closed")

    monkeypatch.setattr(runner, "_lock", rotate_before_lock)
    monkeypatch.setattr(runner, "decrypt_secret", decrypt)
    monkeypatch.setattr(runner, "BiliClient", Source)
    assert runner.run_job(db, job)["logged_in"] is True
    assert observed == ["locked", "rotated-ciphertext", "closed"]


def test_decryption_failure_before_client_creation_keeps_safe_error(db, monkeypatch):
    job = setup(db, "verify_account", "unused")
    def broken_decrypt(_value):
        raise ValueError("sensitive internal detail")
    monkeypatch.setattr(runner, "decrypt_secret", broken_decrypt)
    with pytest.raises(IngestError, match="账号凭据无法解密") as caught:
        runner.run_job(db, job)
    assert caught.value.code == "invalid_cookie"
    assert "sensitive" not in str(caught.value)


def comment(identity, *, root="0", parent="0", replies=0, member=True):
    return {"rpid_str": str(identity), "oid": "123", "root_str": root, "parent_str": parent,
            "content": {"message": "正文"}, "ctime": 1700000000, "rcount": replies, "like": 1,
            "member": {"mid": "456", "uname": "昵称", "avatar": "https://i0.hdslb.com/a.png?signature=secret"} if member else None}


def test_two_level_comments_resume_and_dedupe(db, monkeypatch):
    calls = []
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            calls.append(("root", offset))
            if not offset:
                root = comment("9007199254740993", replies=2)
                root["replies"] = [comment("9007199254740994", root=root["rpid_str"], parent=root["rpid_str"])]
                return {"cursor": {"is_end": False, "pagination_reply": {"next_offset": "opaque"}}, "replies": [root], "top_replies": [root]}
            return {"cursor": {"is_end": True}, "replies": [comment("9007199254740996", member=False)]}
        def replies_page(self, aid, root, pn):
            calls.append(("reply", pn))
            return {"page": {"num": pn, "size": 1, "count": 2}, "replies": [comment(str(9007199254740993 + pn), root=root, parent=root if pn == 1 else "9007199254740994")]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    job = setup(db, "refresh_comments", video.id, {"images": False, "request_budget": 1})
    results = [runner.run_job(db, job) for _ in range(4)]
    assert [result["continuation"] for result in results] == [True, True, True, False]
    assert calls == [("root", ""), ("root", "opaque"), ("reply", 1), ("reply", 2)]
    assert db.scalar(select(func.count()).select_from(Comment)) == 4
    assert db.scalar(select(func.count()).select_from(CommentVersion)) == 4
    assert db.scalar(select(func.count()).select_from(CaptureRun)) == 1
    assert db.scalar(select(func.count()).select_from(PlatformUser)) == 1
    child = db.scalar(select(Comment).where(Comment.rpid == "9007199254740995"))
    assert child.parent_rpid == "9007199254740994"
    assert "signature=" not in json.dumps(child.raw)
    assert video.capture_status != "complete"  # comments alone are not a media archive


def test_repeated_cursor_does_not_commit_skipped_page(db, monkeypatch):
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            return {"cursor": {"is_end": False, "pagination_reply": {"next_offset": "same"}}, "replies": [comment("1")]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    job = setup(db, "refresh_comments", video.id)
    with pytest.raises(IngestError, match="游标"):
        runner.run_job(db, job)
    assert job.checkpoint["comments"]["offset"] == "same"
    run = db.get(CaptureRun, job.checkpoint["run_id"])
    assert run.status == "partial" and run.end_reason == "pagination_loop"


def test_collection_checkpoint_does_not_remove_unseen_until_terminal(db, monkeypatch):
    class Source(FakeClient):
        def favorite_page(self, source_id, page):
            if page == 1:
                return {"has_more": True, "medias": [{"id": 123, "type": 2, "bvid": "BV1234567890", "title": "视频"}]}
            return {"has_more": False, "medias": [{"id": 999, "type": 2, "bvid": "", "title": "已失效"}]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    collection = Collection(source_id="1", title="收藏")
    db.add(collection); db.flush()
    old = CollectionItem(collection_id=collection.id, source_resource_id="2:777", source_state="available")
    db.add(old)
    job = setup(db, "scan_collection", collection.id, {"archive": False, "request_budget": 1})
    assert runner.run_job(db, job)["continuation"] is True
    assert old.source_state == "available"
    assert runner.run_job(db, job)["continuation"] is True
    assert old.source_state == "available"  # Terminal page alone is not a consistency check.
    assert runner.run_job(db, job)["continuation"] is False
    assert old.source_state == "not_observed"
    failed = db.scalar(select(CollectionItem).where(CollectionItem.source_resource_id == "2:999"))
    assert failed.video_id is None and failed.source_state == "unavailable"


def test_account_verification_does_not_return_cookie(db, monkeypatch):
    class Source(FakeClient):
        def nav(self):
            return {"isLogin": True, "mid": 123, "vip": {"status": 1}, "uname": "private-name"}
    monkeypatch.setattr(runner, "BiliClient", Source)
    job = setup(db, "verify_account", "unused")
    result = runner.run_job(db, job)
    assert result["uid"] == "123" and result["vip"] is True
    assert "secret" not in json.dumps(result)


def test_paused_job_never_fetches(db, monkeypatch):
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    job = setup(db, "verify_account", "unused")
    job.status = "paused"; db.commit()
    with pytest.raises(IngestError) as error:
        runner.run_job(db, job)
    assert error.value.code == "job_stopped"


def test_metadata_preserves_up_staff_and_multiple_parts(db, monkeypatch):
    class Source(FakeClient):
        def view(self, bvid):
            return {"bvid": bvid, "aid": "123", "title": "多P视频", "desc": "介绍", "owner": {"mid": 456, "name": "投稿者"}, "staff": [{"mid": 789, "name": "合作者", "title": "作曲"}], "pages": [{"cid": 11, "page": 1, "part": "上", "duration": 3}, {"cid": 12, "page": 2, "part": "下", "duration": 4}]}
    monkeypatch.setattr(runner, "BiliClient", Source)
    policy = {"media": False, "danmaku": False, "subtitles": False, "comments": False, "profiles": False, "images": False}
    job = setup(db, "archive_video", "BV1234567890", policy)
    assert runner.run_job(db, job)["continuation"] is False
    assert db.scalar(select(func.count()).select_from(VideoPart)) == 2
    assert db.scalar(select(func.count()).select_from(Creator)) == 2
    assert {v.role for v in db.scalars(select(VideoCreator))} == {"owner", "staff"}


def test_public_component_toggles_disable_network_stages(db, monkeypatch):
    class Source(FakeClient):
        def view(self, bvid):
            return {"bvid": bvid, "aid": "123", "title": "仅元数据", "pages": [{"cid": 11, "duration": 3}]}
        def comment_page(self, *args): pytest.fail("comments disabled")
        def segment(self, *args): pytest.fail("danmaku disabled")
        def player(self, *args): pytest.fail("subtitles disabled")
    monkeypatch.setattr(runner, "BiliClient", Source)
    monkeypatch.setattr(runner, "archive_media", lambda *args, **kwargs: pytest.fail("media disabled"))
    policy = {"download_media": False, "fetch_comments": False, "fetch_danmaku": False, "fetch_subtitles": False}
    job = setup(db, "archive_video", "BV1234567890", policy)
    assert runner.run_job(db, job)["continuation"] is False


def test_media_fingerprint_excludes_urls_and_title():
    info = {"format_id": "80", "height": 1080, "vcodec": "avc1", "url": "https://a/?token=first", "title": "before"}
    other = {**info, "url": "https://other/?token=second", "title": "after"}
    assert selected_fingerprint(info) == selected_fingerprint(other)
    assert selected_fingerprint(info) != selected_fingerprint({**info, "height": 2160})
    assert selected_fingerprint(info) != selected_fingerprint(info, generation=1)


def test_danmaku_resume_raw_assets_and_subtitles(db, monkeypatch):
    # Minimal valid protobuf fixture: dmid 1/2, progress 1s, text 'a'.
    calls = []
    class Source(FakeClient):
        def view(self, bvid):
            return {"bvid": bvid, "aid": "123", "title": "视频", "pages": [{"cid": 11, "duration": 361, "part": "P1"}]}
        def segment(self, aid, cid, index):
            calls.append(index)
            return b"\x0a\x08\x08" + bytes([index]) + b"\x10\xe8\x07\x3a\x01a"
        def player(self, aid, cid):
            return {"subtitle": {"subtitles": [{"lan": "zh-CN", "lan_doc": "中文", "subtitle_url": "https://i0.hdslb.com/subtitle.json"}]}}
        def asset(self, url, **kwargs):
            return json.dumps({"body": [{"from": 0, "to": 1.25, "content": "字幕<script>"}]}).encode(), "application/json"
    monkeypatch.setattr(runner, "BiliClient", Source)
    policy = {"media": False, "comments": False, "profiles": False, "images": False, "request_budget": 1}
    job = setup(db, "archive_video", "BV1234567890", policy)
    # The resumable tag API request has its own slot after the initial basics.
    assert [runner.run_job(db, job)["continuation"] for _ in range(5)] == [True, True, True, True, False]
    assert calls == [1, 2]
    snapshot = db.scalar(select(DanmakuSnapshot))
    assert snapshot.completed_segments == 2 and snapshot.status == "visible_traversal_complete"
    assert len(snapshot.raw_asset_ids) == 2
    assert len(json.loads(read_asset_bytes(db, snapshot.data_asset_id))) == 2
    track = db.scalar(select(SubtitleTrack))
    assert "00:00:01.250" in read_asset_bytes(db, track.asset_id).decode()
    assert "&lt;script&gt;" in read_asset_bytes(db, track.asset_id).decode()


def test_bad_subtitle_time_is_not_success():
    with pytest.raises(IngestError):
        runner.subtitle_vtt({"body": [{"from": 2, "to": 1, "content": "bad"}]})


def test_danmaku_disk_merge_deduplicates_sorts_and_streams_result(db, monkeypatch):
    from test_ingest_client import blob, integer
    def item(identity, time, text):
        return blob(1, integer(1, identity) + integer(2, time) + blob(7, text.encode()))
    class Source(FakeClient):
        def view(self, bvid):
            return {"bvid": bvid, "aid": "123", "pages": [{"cid": 11, "duration": 361}]}
        def segment(self, aid, cid, index):
            return (item(1, 2000, "old") + item(2, 1000, "second") if index == 1 else
                    item(1, 0, "updated") + item(3, 1000, "third"))
    monkeypatch.setattr(runner, "BiliClient", Source)
    dumps = json.dumps
    def bounded_encode(value, *args, **kwargs):
        # Segment JSON has two rows; the merged three-row output must be
        # streamed, never rebuilt and encoded as one in-memory list.
        assert not (isinstance(value, list) and len(value) > 2 and all(isinstance(x, dict) and "text" in x for x in value))
        return dumps(value, *args, **kwargs)
    monkeypatch.setattr(runner.json, "dumps", bounded_encode)
    job = setup(db, "archive_video", "BV1234567890", {"media": False, "comments": False,
        "subtitles": False, "profiles": False, "images": False})
    assert runner.run_job(db, job)["continuation"]  # Give other video cards their first turn.
    assert not runner.run_job(db, job)["continuation"]
    snapshot = db.scalar(select(DanmakuSnapshot))
    result = json.loads(read_asset_bytes(db, snapshot.data_asset_id))
    assert [(row["id"], row["time"], row["text"]) for row in result] == [
        ("1", 0, "updated"), ("2", 1, "second"), ("3", 1, "third")]
    assert read_asset_bytes(db, snapshot.data_asset_id) == dumps(result, ensure_ascii=False).encode()
    assert snapshot.count == 3 and snapshot.completed_segments == 2
    assert not list(settings.scratch_dir.glob("treasure-danmaku-*"))


def test_danmaku_merge_stops_between_fragments_without_publishing_snapshot(db, monkeypatch):
    from app.storage import service
    class Source(FakeClient):
        def view(self, bvid):
            return {"bvid": bvid, "aid": "123", "pages": [{"cid": 11, "duration": 361}]}
        def segment(self, aid, cid, index):
            return b"\x0a\x08\x08" + bytes([index]) + b"\x10\xe8\x07\x3a\x01a"
    monkeypatch.setattr(runner, "BiliClient", Source)
    # An earlier completed snapshot must remain readable if this refresh stops.
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="11", duration=361)
    previous_run = CaptureRun(video_id=video.id, status="complete", scope={})
    db.add_all([part, previous_run]); db.flush()
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    saved = settings.scratch_dir / "previous-danmaku.json"
    saved.write_bytes(b"[]")
    previous_asset = runner.ingest_file(db, saved, kind="danmaku", mime_type="application/json")
    previous = DanmakuSnapshot(part_id=part.id, run_id=previous_run.id, status="complete",
                              data_asset_id=previous_asset.id, raw_asset_ids=[], count=0)
    db.add(previous); db.commit()
    job = setup(db, "archive_video", "BV1234567890", {"media": False, "comments": False,
        "subtitles": False, "profiles": False, "images": False})
    original = service.materialize_asset
    restored = []
    def stop_after_first(session, asset_id, path):
        result = original(session, asset_id, path)
        restored.append(asset_id)
        job.status = "cancelled"
        db.commit()
        return result
    monkeypatch.setattr(service, "materialize_asset", stop_after_first)
    assert runner.run_job(db, job)["continuation"]
    with pytest.raises(IngestError) as error:
        runner.run_job(db, job)
    assert error.value.code == "job_stopped" and len(restored) == 1
    snapshot = db.scalar(select(DanmakuSnapshot).where(DanmakuSnapshot.run_id != previous_run.id))
    assert snapshot.data_asset_id is None and snapshot.completed_segments == 2
    assert previous.status == "complete" and read_asset_bytes(db, previous.data_asset_id) == b"[]"
    assert not list(settings.scratch_dir.glob("treasure-danmaku-*"))


def test_media_is_reused_before_second_download(db, monkeypatch):
    import yt_dlp
    from pathlib import Path
    from app.ingest.client import parse_cookies
    downloads = []
    class Downloader:
        def __init__(self, options):
            self.params = {**options, "outtmpl": {"default": options["outtmpl"]}}
        def build_format_selector(self, expression): return lambda context: []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            return {"id": "BV1234567890", "format_id": "80", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "width": 1920, "height": 1080, "filesize": 8, "url": "https://cdn.invalid/?token=never-persist"}
        def process_info(self, info):
            downloads.append(info["format_id"])
            Path(self.params["outtmpl"]["default"].replace("%(ext)s", "mp4")).write_bytes(b"test-mp4")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    monkeypatch.setattr(media, "_probe", lambda path: ({"codec_name": "h264", "width": 1920, "height": 1080}, {"codec_name": "aac"}, 10))
    verified_audio = []
    monkeypatch.setattr(media, "_verify_audio_decode", lambda path, *args: verified_audio.append(path))
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="11", position=1, duration=10)
    db.add(part); db.flush()
    client = FakeClient(); client.cookies = parse_cookies("SESSDATA=mock")
    client.view = lambda bvid: {"pages": [{"cid": "11", "page": 1}]}
    first, reused1 = media.archive_media(db, client, video, part, {})
    db.commit()
    second, reused2 = media.archive_media(db, client, video, part, {})
    assert first.id == second.id and not reused1 and reused2
    assert downloads == ["80"]
    assert len(verified_audio) == 1
    assert not list(settings.scratch_dir.glob("treasure-media-*/cookies.txt"))


def test_failed_image_queue_keeps_comments_and_retries_only_assets(db, monkeypatch):
    roots_called = []
    class Source(FakeClient):
        failing = True
        def comment_page(self, aid, offset):
            roots_called.append(offset)
            return {"cursor": {"is_end": True}, "replies": [comment("1")]}
        def asset(self, url, **kwargs):
            if Source.failing:
                raise IngestError("模拟图片网络错误", code="network_error")
            return b"\x89PNG\r\n\x1a\nmock-image", "image/png"
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    job = setup(db, "refresh_comments", video.id, {"images": True})
    with pytest.raises(IngestError, match="图片"):
        runner.run_job(db, job)
    assert db.scalar(select(func.count()).select_from(Comment)) == 1
    assert job.checkpoint["images"]
    Source.failing = False
    assert runner.run_job(db, job)["continuation"] is False
    assert roots_called == [""]
    assert db.scalar(select(UserSnapshot)).avatar_asset_id is not None
    assert job.checkpoint["images"] == []


def test_failed_playback_resumes_without_redownloading_archive(db, monkeypatch):
    from app.storage.service import ingest_file
    from pathlib import Path
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="11", position=1, duration=10)
    db.add(part); db.flush()
    job = setup(db, "archive_video", video.id, {"media": True, "danmaku": False,
        "subtitles": False, "profiles": False, "comments": False, "images": False})
    job.checkpoint = {"metadata_done": True, "part_ids": [part.id]}
    db.commit()
    downloads, conversions = [], []
    def archive(*args, **kwargs):
        downloads.append(part.id)
        settings.scratch_dir.mkdir(parents=True, exist_ok=True)
        source = settings.scratch_dir / "mock-source.mp4"
        source.write_bytes(b"mock-source")
        asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="mock", quality="1080p")
        db.add(variant); db.flush()
        return variant, False
    def compatible(session, selected_part, original, policy, **kwargs):
        conversions.append(original.id)
        if len(conversions) == 1:
            raise IngestError("转换暂时失败", code="conversion_failed")
        variant = MediaVariant(part_id=part.id, asset_id=original.asset_id, kind="playback", format_key="mock-playback", quality="1080p")
        db.add(variant); db.flush()
        return variant, False
    monkeypatch.setattr(runner, "archive_media", archive)
    monkeypatch.setattr(runner, "ensure_playback_variant", compatible)
    assert not runner.run_job(db, job)["continuation"]
    assert not downloads and not conversions and video.capture_status == "metadata_ready"
    download_job = db.get(Job, job.checkpoint["media_job_id"])
    download_job.status = "running"; db.commit()
    assert not runner.run_job(db, download_job)["continuation"]
    assert download_job.checkpoint["media_parts"] == [part.id]
    assert video.capture_status == "complete" and not conversions
    playback_job = db.scalar(select(Job).where(Job.kind == "create_playback"))
    playback_job.status = "running"; db.commit()
    with pytest.raises(IngestError, match="转换暂时失败"):
        runner.run_job(db, playback_job)
    assert video.capture_status == "complete"  # Optional conversion cannot hide the original.
    result = runner.run_job(db, playback_job)
    assert result["variant_id"] and video.capture_status == "complete"
    assert len(downloads) == 1 and len(conversions) == 2
