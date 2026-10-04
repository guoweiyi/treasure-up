import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest import runner
from app.ingest.client import BiliClient, retry_after
from app.ingest.errors import IngestError
from app.ingest.throttle import AccountPacer, utc
from app.models import Base, DanmakuSnapshot, Job, SourceAccount, Video, VideoPart, VideoStatSnapshot


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(runner, "decrypt_secret", lambda value: "SESSDATA=mock")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


class Source:
    views = 0
    def __init__(self, *args, **kwargs): pass
    def nav(self): return {"isLogin": True, "mid": "1"}
    def close(self): pass
    def view(self, bvid):
        Source.views += 1
        return {"bvid": bvid, "aid": "123", "title": "source title should not overwrite", "pages": [{"cid": "11"}],
                "stat": {"view": 100, "like": 3, "coin": 2, "favorite": 4, "danmaku": 5, "reply": -1, "share": True}}


def setup(db, policy=None):
    account = SourceAccount(name="mock", secret_encrypted="mock")
    video = Video(bvid="BV1234567890", aid="123", title="saved title", capture_status="complete", metadata_json={"kept": True})
    db.add_all([account, video]); db.flush()
    job = Job(kind="refresh_stats", target_id=video.id, account_id=account.id, status="running",
              dedupe_key=str(uuid4()), policy=policy or {"interval_jitter_seconds": 0})
    db.add(job); db.commit()
    return account, video, job


def test_stats_only_has_no_media_or_people_side_effects(db, monkeypatch):
    Source.views = 0
    monkeypatch.setattr(runner, "BiliClient", Source)
    for name in ("archive_media", "_comments", "_metadata", "_images", "_danmaku"):
        monkeypatch.setattr(runner, name, lambda *args, **kwargs: pytest.fail("stats-only side effect"))
    account, video, job = setup(db)
    assert runner.run_job(db, job)["continuation"] is False
    assert runner.run_job(db, job)["continuation"] is False
    snapshots = db.scalars(select(VideoStatSnapshot)).all()
    assert len(snapshots) == 1 and Source.views == 1
    assert snapshots[0].counts == {"view": 100, "like": 3, "coin": 2, "favorite": 4, "danmaku": 5, "reply": None, "share": None}
    assert video.title == "saved title" and video.metadata_json == {"kept": True}
    assert video.capture_status == "complete"


def test_stats_and_current_danmaku_resume_share_one_observation(db, monkeypatch):
    Source.views = 0
    segments = []
    class WithDanmaku(Source):
        def segment(self, aid, cid, index):
            segments.append((cid, index))
            return b""
    monkeypatch.setattr(runner, "BiliClient", WithDanmaku)
    monkeypatch.setattr(runner, "archive_media", lambda *args, **kwargs: pytest.fail("statistics downloaded media"))
    account, video, job = setup(db, {"refresh_danmaku": True, "request_budget": 1})
    db.add_all([VideoPart(video_id=video.id, cid="11", position=1, duration=361),
                VideoPart(video_id=video.id, cid="22", position=2, duration=60)])
    db.commit()
    assert runner.run_job(db, job)["continuation"] is True
    assert runner.run_job(db, job)["continuation"] is False
    assert Source.views == 1 and segments == [("11", 1), ("11", 2)]
    assert db.scalar(select(func.count()).select_from(VideoStatSnapshot)) == 1
    snapshot = db.scalar(select(DanmakuSnapshot))
    assert snapshot.status == "visible_traversal_complete" and snapshot.completed_segments == 2
    assert video.capture_status == "complete"


def test_failed_refresh_does_not_mark_media_archive_partial(db, monkeypatch):
    class Broken(Source):
        def view(self, bvid): return {"bvid": bvid, "stat": "bad"}
    monkeypatch.setattr(runner, "BiliClient", Broken)
    account, video, job = setup(db)
    with pytest.raises(IngestError) as failure:
        runner.run_job(db, job)
    assert failure.value.code == "invalid_stats"
    assert video.capture_status == "complete"
    assert db.scalar(select(func.count()).select_from(VideoStatSnapshot)) == 0


def test_only_bulk_video_spacing_persists_across_pacer_instances(db):
    account, video, job = setup(db)
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    pauses = []
    def sleep(seconds):
        pauses.append(seconds)
        clock[0] += timedelta(seconds=seconds)
    policy = {"request_interval_seconds": 3, "video_interval_seconds": 60, "interval_jitter_seconds": 0}
    pacer = AccountPacer(db, account, policy, lambda: None, clock=lambda: clock[0], sleep=sleep, jitter=lambda limit: 0)
    pacer.before_request(); pacer.before_video()
    db.expire(account)
    other = AccountPacer(db, account, policy, lambda: None, clock=lambda: clock[0], sleep=sleep, jitter=lambda limit: 0)
    other.before_request()
    assert clock[0] == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    other.before_video()
    assert clock[0] == datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    assert max(pauses) <= 0.5


def test_retry_after_and_risk_backoff_suspend_entire_account(db, monkeypatch):
    calls = []
    class Limited(Source):
        def nav(self):
            calls.append("nav")
            raise IngestError("源站限流", code="rate_limited", retry_after_seconds=5000)
    monkeypatch.setattr(runner, "BiliClient", Limited)
    account, video, job = setup(db)
    with pytest.raises(IngestError) as failure:
        runner.run_job(db, job)
    assert failure.value.retry_after_seconds >= 4999
    assert failure.value.blocked is False and not getattr(failure.value, "deferred", False)
    assert account.risk_failures == 1
    assert (utc(account.cooldown_until) - datetime.now(timezone.utc)).total_seconds() > 4900
    with pytest.raises(IngestError) as second:
        runner.run_job(db, job)
    assert second.value.code == "account_cooldown" and calls == ["nav"]
    assert second.value.deferred is True and not second.value.blocked


def test_http429_retry_after_is_safe_and_never_immediately_retried():
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "1800"}, content=b"sensitive error body must not escape")
    client = BiliClient("SESSDATA=mock", interval=0, transport=httpx.MockTransport(transport))
    with pytest.raises(IngestError) as failure:
        client.nav()
    assert failure.value.retry_after_seconds == 1800 and len(requests) == 1
    assert "sensitive" not in str(failure.value)
    client.close()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert retry_after("Thu, 01 Jan 2026 00:05:00 GMT", now=now) == 300
    assert retry_after("invalid") is None
