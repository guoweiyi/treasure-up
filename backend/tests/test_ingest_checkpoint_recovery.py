from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ingest import runner
from app.ingest.errors import IngestError
from app.models import Asset, Job, MediaVariant, Video, VideoPart
from test_ingest_runner import FakeClient, db, setup  # noqa: F401


@pytest.mark.parametrize("pending_last_part", [False, True])
def test_completed_multipart_danmaku_cannot_consume_every_comment_slice(db, monkeypatch, pending_last_part):
    clock, calls, segments = [0.0], [], []
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    class Source(FakeClient):
        def comment_page(self, aid, offset):
            calls.append(offset)
            return {"cursor": {"is_end": True}, "replies": []}
        def segment(self, aid, cid, number):
            segments.append((cid, number))
            return b""
    monkeypatch.setattr(runner, "BiliClient", Source)
    video = Video(bvid="BV1234567890", aid="123")
    db.add(video); db.flush()
    parts = [VideoPart(video_id=video.id, cid=str(index + 1), position=index + 1, duration=10) for index in range(8)]
    db.add_all(parts); db.flush()
    job = setup(db, "archive_video", video.id, {"images": False, "profiles": False, "subtitles": False})
    job.checkpoint = {"metadata_done": True, "basics_done": True, "part_ids": [part.id for part in parts],
        "danmaku": {part.id: {"done": not (pending_last_part and part == parts[-1])} for part in parts}}
    db.commit()
    stage = runner.Context.stage
    def delayed_completed_stage(ctx, phase, **details):
        # Simulate a remote database whose redundant per-P commits take six
        # seconds. Replaying eight completed stages exhausts every 45s slice.
        if phase == "danmaku":
            clock[0] += 6
        return stage(ctx, phase, **details)
    monkeypatch.setattr(runner.Context, "stage", delayed_completed_stage)
    result = runner.run_job(db, job)
    assert not result["continuation"] and calls == [""]
    assert segments == ([(parts[-1].cid, 1)] if pending_last_part else [])
    child = db.scalar(select(Job).where(Job.kind == "download_media"))
    assert child and child.checkpoint["part_ids"] == [part.id for part in parts]


def test_multipart_download_failure_retries_only_unfinished_original(db, monkeypatch):
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    video = Video(bvid="BV1234567890", aid="123", metadata_json={"ingest_state": {"metadata": "ready"}})
    db.add(video); db.flush()
    parts = [VideoPart(video_id=video.id, cid=str(index + 1), position=index + 1, duration=10) for index in range(2)]
    db.add_all(parts); db.flush()
    job = setup(db, "download_media", video.id, {"create_compatible_copy": False})
    job.checkpoint = {"part_ids": [part.id for part in parts]}
    db.commit()
    calls = []
    def download(session, client, saved, part, policy, **kwargs):
        calls.append(part.id)
        if calls == [parts[0].id, parts[1].id]:
            raise IngestError("temporary source failure", code="download_failed")
        asset = Asset(sha256=part.cid.zfill(64), size=100, kind="media")
        session.add(asset); session.flush()
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="source")
        session.add(variant); session.flush()
        return variant, False
    monkeypatch.setattr(runner, "archive_media", download)
    with pytest.raises(IngestError) as caught:
        runner.run_job(db, job)
    assert caught.value.code == "download_failed"
    assert job.checkpoint["media_parts"] == [parts[0].id]
    first_id = job.checkpoint["media_variants"][parts[0].id]
    assert video.capture_status == "partial"
    assert not runner.run_job(db, job)["continuation"]
    assert calls == [parts[0].id, parts[1].id, parts[1].id]
    assert job.checkpoint["media_parts"] == [part.id for part in parts]
    assert job.checkpoint["media_variants"][parts[0].id] == first_id
    assert len(list(db.scalars(select(MediaVariant)))) == 2
    assert video.capture_status == "complete"
