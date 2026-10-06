import pytest
from sqlalchemy import func, select

from app.ingest import runner
from app.ingest.errors import IngestError
from app.models import Asset, CaptureRun, Job, MediaVariant, Setting, Video, VideoPart
from test_ingest_runner import FakeClient, db, setup  # noqa: F401


def saved_archive(db):
    video = Video(bvid="BV1234567890", aid="123", capture_status="complete", metadata_json={
        "ingest_state": {"metadata": "ready", "media": "complete", "playback": "failed",
            "playback_error": "old_failure"}})
    asset = Asset(sha256="a" * 64, size=100, kind="media", mime_type="video/mp4")
    db.add_all([video, asset]); db.flush()
    part = VideoPart(video_id=video.id, cid="123", duration=10)
    db.add(part); db.flush()
    archive = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="original",
        video_codec="hevc", audio_codec="eac3")
    db.add(archive); db.flush()
    job = setup(db, "create_playback", archive.id)
    job.dedupe_key = "playback:" + archive.id
    db.commit()
    return video, part, archive, job


def unsupported(*args, **kwargs):
    raise IngestError("HDR conversion is not supported", code="hdr_conversion_unsupported", retryable=False)


def test_hdr_compatibility_is_explicitly_unsupported_without_inventing_a_copy(db, monkeypatch):
    video, part, archive, job = saved_archive(db)
    monkeypatch.setattr(runner, "ensure_playback_variant", unsupported)
    result = runner.run_job(db, job)
    db.expire_all()
    assert result["compatibility"] == "unsupported"
    assert result["reason"] == "hdr_conversion_unsupported"
    assert result["archive_variant_id"] == archive.id and result["variant_id"] is None
    assert video.capture_status == "complete"
    state = video.metadata_json["ingest_state"]
    assert state["media"] == "complete" and state["playback"] == "unsupported"
    assert state["playback_reason"] == "hdr_conversion_unsupported" and state["playback_error"] is None
    assert state["playback_jobs"][archive.id] == {"job_id": job.id, "part_id": part.id, "status": "unsupported"}
    run = db.get(CaptureRun, result["run_id"])
    assert run.status == "unsupported" and run.end_reason == "hdr_conversion_unsupported"
    assert run.finished_at is not None and job.checkpoint["progress"]["phase"] == "compatible_unsupported"
    assert db.scalar(select(func.count()).select_from(MediaVariant)) == 1
    assert db.get(Asset, archive.asset_id).size == 100


@pytest.mark.parametrize("retryable, expected", [(True, "partial"), (False, "failed")])
def test_other_conversion_errors_still_propagate_as_failures(db, monkeypatch, retryable, expected):
    video, _, archive, job = saved_archive(db)
    error = IngestError("converter failed", code="transcode_failed", retryable=retryable)
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(runner, "ensure_playback_variant", fail)
    with pytest.raises(IngestError) as caught:
        runner.run_job(db, job)
    assert caught.value is error
    db.expire_all()
    state = video.metadata_json["ingest_state"]
    assert state["playback"] == expected and state["playback_error"] == "transcode_failed"
    assert state["media"] == "complete"
    run = db.get(CaptureRun, job.checkpoint["run_id"])
    assert run.status == "partial" and run.end_reason == "transcode_failed"
    assert db.scalar(select(func.count()).select_from(MediaVariant)) == 1
    assert db.get(MediaVariant, archive.id).kind == "archive"


def test_unexpected_converter_exception_is_not_relabelled_as_unsupported(db, monkeypatch):
    _, _, _, job = saved_archive(db)
    error = RuntimeError("unexpected converter failure")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(runner, "ensure_playback_variant", fail)
    with pytest.raises(RuntimeError) as caught:
        runner.run_job(db, job)
    assert caught.value is error
    assert not job.result.get("compatibility")
    assert job.checkpoint["progress"]["phase"] == "compatible_copy"


def test_download_resume_reuses_unsupported_child_without_claiming_a_copy(db, monkeypatch):
    video, part, archive, compatibility_job = saved_archive(db)
    monkeypatch.setattr(runner, "ensure_playback_variant", unsupported)
    compatibility_job.result = runner.run_job(db, compatibility_job)
    compatibility_job.status = "succeeded"
    db.commit()  # The queue persists a successful bounded/unsupported outcome.
    job = setup(db, "download_media", video.id)
    job.checkpoint = {"part_ids": [part.id], "media_parts": [part.id], "media_variants": {part.id: archive.id}}
    db.commit()
    monkeypatch.setattr(runner, "BiliClient", FakeClient)
    monkeypatch.setattr(runner, "archive_media", lambda *a, **k: pytest.fail("original must be reused"))
    monkeypatch.setattr(runner, "ensure_playback_variant", lambda *a, **k: pytest.fail("completed compatibility job must not restart"))
    for _ in range(2):
        assert not runner.run_job(db, job)["continuation"]
        db.expire_all()
        state = video.metadata_json["ingest_state"]
        assert state["media"] == "complete" and state["playback"] == "unsupported"
        assert state["playback_jobs"][archive.id]["status"] == "unsupported"
        assert compatibility_job.status == "succeeded" and compatibility_job.result["variant_id"] is None
    assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "create_playback")) == 1
    assert db.scalar(select(func.count()).select_from(MediaVariant).where(MediaVariant.kind == "playback")) == 0
    assert video.capture_status == "complete"


@pytest.mark.parametrize("duration, settings, expected", [
    (300, {"min_duration_seconds": 300, "segment_seconds": 10, "analyze_loudness": True}, (True, True)),
    (299, {"min_duration_seconds": 300, "analyze_loudness": False}, None),
    (1000, {"package_long_videos": False, "analyze_loudness": False}, None),
    (10, {"min_duration_seconds": 300, "analyze_loudness": True}, (False, True)),
])
def test_audio_only_derivative_preparation_uses_its_own_variant_and_current_settings(db, monkeypatch, duration, settings, expected):
    video, part, archive, job = saved_archive(db)
    db.add(Setting(key="playback", value=settings))
    copy = MediaVariant(part_id=part.id, asset_id=archive.asset_id, kind="playback", format_key="audio-only",
        duration=duration, metadata_json={"compatibility_mode": "audio_only", "source_variant_id": archive.id})
    db.add(copy); db.commit()
    monkeypatch.setattr(runner, "ensure_playback_variant", lambda *a, **k: (copy, False))
    result = runner.run_job(db, job)
    assert result["variant_id"] == copy.id and result["compatibility_mode"] == "audio_only"
    prepared = list(db.scalars(select(Job).where(Job.kind == "prepare_media")))
    if expected is None:
        assert prepared == [] and "prepare_job_id" not in result
    else:
        assert len(prepared) == 1 and prepared[0].id == result["prepare_job_id"]
        assert prepared[0].target_id == copy.id and prepared[0].target_id != archive.id
        assert prepared[0].policy == {"package": expected[0], "analyze_loudness": expected[1],
                                      "segment_seconds": settings.get("segment_seconds", 6)}


@pytest.mark.parametrize("prior_status", ["succeeded", "failed", "paused", "cancelled"])
def test_audio_only_preparation_reuses_its_job_without_reviving_stopped_work(db, monkeypatch, prior_status):
    from app.maintenance import enqueue_variant_preparation
    _, part, archive, job = saved_archive(db)
    original_preparation = enqueue_variant_preparation(db, archive)
    original_preparation.status = "succeeded"
    copy = MediaVariant(part_id=part.id, asset_id=archive.asset_id, kind="playback", format_key="audio-only", duration=600,
        metadata_json={"compatibility_mode": "audio_only", "source_variant_id": archive.id})
    db.add(copy); db.commit()
    monkeypatch.setattr(runner, "ensure_playback_variant", lambda *a, **k: (copy, True))
    first = runner.run_job(db, job)
    preparation = db.get(Job, first["prepare_job_id"])
    preparation.status = prior_status; db.commit()
    second = runner.run_job(db, job)
    assert second["prepare_job_id"] == preparation.id and preparation.status == prior_status
    assert original_preparation.status == "succeeded" and preparation.id != original_preparation.id
    assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "prepare_media")) == 2


@pytest.mark.parametrize("duration,size,expected", [(450, 100, True), (120, 70 * 1024**2, True), (120, 10 * 1024**2, False)])
def test_h264_compatibility_gets_own_preparation_without_audio_only_marker(db, monkeypatch, duration, size, expected):
    _, part, archive, job = saved_archive(db)
    copy_asset = Asset(sha256="b" * 64, size=size, kind="media", mime_type="video/mp4")
    db.add(copy_asset); db.flush()
    copy = MediaVariant(part_id=part.id, asset_id=copy_asset.id, kind="playback", format_key="h264-aac-sdr-v1",
                        video_codec="h264", audio_codec="aac", duration=duration)
    db.add(copy); db.commit()
    monkeypatch.setattr(runner, "ensure_playback_variant", lambda *a, **k: (copy, False))
    result = runner.run_job(db, job)
    prepared = db.get(Job, result["prepare_job_id"])
    assert prepared.target_id == copy.id
    assert prepared.policy["package"] is expected
    assert prepared.policy["analyze_loudness"]
    again = runner.run_job(db, job)
    assert again["prepare_job_id"] == prepared.id
    assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "prepare_media")) == 1
