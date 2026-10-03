"""Real PostgreSQL pacing/statistics/heartbeat interaction in random test databases."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app import jobs
from app.ingest import runner
from app.ingest.throttle import AccountPacer
from app.models import Job, SourceAccount, Video, VideoPart, VideoStatSnapshot, utcnow
from test_postgres_integration import postgres_workspace  # noqa: F401; isolated test DB fixture


pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit isolated PostgreSQL test opt-in required")


def test_stats_resume_and_pacing_do_not_lock_out_heartbeat(postgres_workspace, monkeypatch):
    space = postgres_workspace
    with space.sessions() as db:
        account = SourceAccount(name="mock", secret_encrypted="fixture-not-a-credential")
        video = Video(bvid="BV1234567890", aid="123", capture_status="complete")
        db.add_all([account, video]); db.flush()
        db.add(VideoPart(video_id=video.id, cid="11", position=1, duration=361))
        job = jobs.enqueue(db, "refresh_stats", video.id, account.id, policy={"refresh_danmaku": True,
            "request_budget": 1, "request_interval_seconds": 3, "interval_jitter_seconds": 0})
        db.commit()
        job_id = job.id
    heartbeat_checks, views, segments = [], [], []

    def pulse():
        with space.sessions() as heart:
            heart.execute(text("SET LOCAL lock_timeout = '500ms'"))
            owner = heart.scalar(select(Job.lease_owner).where(Job.id == job_id))
            assert jobs.renew_lease(heart, job_id, owner)
            heartbeat_checks.append(True)

    virtual_clock = [utcnow()]
    def sleep(seconds):
        pulse()  # In the middle of pacing, while the runner session is open.
        virtual_clock[0] += timedelta(seconds=seconds)
    def pacer(db, account, policy, guard):
        return AccountPacer(db, account, policy, guard, clock=lambda: virtual_clock[0], sleep=sleep, jitter=lambda _: 0)
    monkeypatch.setattr(runner, "AccountPacer", pacer)
    monkeypatch.setattr(runner, "decrypt_secret", lambda _: "SESSDATA=mock")
    save = runner._save_stats
    def save_with_heartbeat(*args):
        result = save(*args)
        pulse()  # The new statistics row has flushed, but is not committed yet.
        return result
    monkeypatch.setattr(runner, "_save_stats", save_with_heartbeat)

    class Source:
        def __init__(self, *args, **kwargs): pass
        def close(self): pass
        def nav(self):
            self.before_request("https://api.bilibili.com/x/web-interface/nav")
            pulse()
            return {"isLogin": True, "mid": "1"}
        def view(self, bvid):
            self.before_request("https://api.bilibili.com/x/web-interface/view")
            views.append(bvid)
            # Fragment callbacks use an independent read session, not the
            # runner's session from a foreign thread.
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(self.before_request.__self__.guard).result(timeout=2)
            pulse()
            return {"bvid": bvid, "aid": "123", "stat": {"view": 9}, "pages": [{"cid": "11"}]}
        def segment(self, aid, cid, index):
            self.before_request("https://api.bilibili.com/x/v2/dm/wbi/web/seg.so")
            segments.append(index)
            pulse()
            return b""
    monkeypatch.setattr(runner, "BiliClient", Source)
    assert jobs.run_job_id(job_id)["status"] == "queued"
    with space.sessions() as db:
        job = db.get(Job, job_id)
        job.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert jobs.run_job_id(job_id)["status"] == "succeeded"
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(VideoStatSnapshot)) == 1
        assert db.get(Video, video.id).capture_status == "complete"
    assert len(views) == 1 and segments == [1, 2] and len(heartbeat_checks) >= 10
