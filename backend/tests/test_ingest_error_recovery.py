from copy import deepcopy
import json

import pytest
from sqlalchemy import func, select

from app.ingest import runner
from app.ingest.errors import IngestError
from app.jobs import LeaseLost
from app.models import CaptureRun, PlatformUser, Video, VideoPart
from test_ingest_runner import FakeClient, db, setup  # noqa: F401


def archive_checkpoint(db):
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="123", duration=10)
    db.add(part); db.flush()
    job = setup(db, "archive_video", video.id, {"images": False, "danmaku": False,
        "subtitles": False, "profiles": False, "comments": True})
    job.checkpoint = {"metadata_done": True, "basics_done": True, "part_ids": [part.id],
        "comments": {"roots_done": True, "candidates": {}, "scanned": 125}}
    db.commit()
    return video, job


def test_internal_error_diagnostic_preserves_only_committed_checkpoint(db, monkeypatch):
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    video, job = archive_checkpoint(db)
    committed_comments = deepcopy(job.checkpoint["comments"])

    def fail(ctx, video):
        person = PlatformUser(uid="987", display_name="uncommitted")
        ctx.db.add(person); ctx.db.flush()
        ctx.cp["images"].append({"id": person.id, "entity": "user", "url": "https://invalid.example/SECRET"})
        ctx.cp["comments"]["selected"] = ["uncommitted"]
        raise RuntimeError("SECRET cookie and SQL parameters must never be published")

    monkeypatch.setattr(runner, "_comments", fail)
    with pytest.raises(IngestError) as caught:
        runner.run_job(db, job)
    assert caught.value.code == "internal_ingest_error"
    db.expire_all()
    assert job.checkpoint["comments"] == committed_comments
    assert job.checkpoint["images"] == []
    assert db.scalar(select(func.count()).select_from(PlatformUser)) == 0
    diagnostic = job.checkpoint["diagnostic"]
    assert diagnostic["type"] == "RuntimeError" and len(diagnostic["fingerprint"]) == 16
    assert "SECRET" not in json.dumps(job.checkpoint)
    run = db.get(CaptureRun, job.checkpoint["run_id"])
    assert run.status == "partial" and run.checkpoint == job.checkpoint


def test_lease_loss_is_not_reclassified_or_saved_as_internal_failure(db, monkeypatch):
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    _, job = archive_checkpoint(db)
    failure = LeaseLost("lost test lease")

    def fail(ctx, video):
        ctx.cp["uncommitted"] = True
        raise failure

    monkeypatch.setattr(runner, "_comments", fail)
    with pytest.raises(LeaseLost) as caught:
        runner.run_job(db, job)
    assert caught.value is failure
    db.expire_all()
    assert "diagnostic" not in job.checkpoint and "uncommitted" not in job.checkpoint
    assert not job.checkpoint.get("media_job_id")
