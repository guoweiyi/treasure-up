"""Comment checkpoint regressions in random, isolated PostgreSQL databases."""
import os

import pytest
from sqlalchemy import func, select

from app.ingest import runner
from app.models import Asset, CaptureRun, Comment, CommentVersion, Job, PlatformUser, Video
from test_postgres_integration import postgres_workspace  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
    reason="Explicit TREASURE_RUN_POSTGRES_TESTS=1 required")


def test_comment_selection_serializes_existing_avatar_inventory(postgres_workspace):
    with postgres_workspace.sessions() as db:
        avatar = Asset(sha256="a" * 64, size=17, kind="avatar", mime_type="image/jpeg")
        db.add(avatar); db.flush()
        db.add(PlatformUser(uid="456", display_name="existing author", signature="",
            avatar_asset_id=avatar.id, raw={"avatar_url": "https://i0.hdslb.com/existing.jpg"}))
        video = Video(bvid="BV1234567890", aid="123")
        db.add(video); db.flush()
        raw = {"rpid_str": "1", "oid": "123", "root_str": "0", "parent_str": "0", "like": 1,
            "content": {"message": "hello[smile]", "emote": {"[smile]": {"url": "https://i0.hdslb.com/emote.png"}}},
            "member": {"mid": "456", "uname": "existing author", "avatar": "https://i0.hdslb.com/existing.jpg"}}
        job = Job(kind="archive_video", target_id=video.id, status="running", dedupe_key="comment-replay",
            policy={"comment_asset_count_limit": 2, "comment_asset_bytes_limit": 64, "comment_reply_total_limit": 0},
            checkpoint={"comments": {"roots_done": True, "source_end": True, "candidates": {"1": raw}, "scanned": 1}})
        db.add(job); db.commit()
        ctx = runner.Context(db, job, None)
        ctx.run.video_id = video.id
        # No source requests or asset downloads: all input is already committed.
        assert runner._comments(ctx, video)
        db.expire_all()
        legacy = video.metadata_json["comment_assets"]["legacy"]
        assert legacy == {"status": "legacy", "bytes": 17, "count": 1}
        assert type(legacy["bytes"]) is int
        assert job.checkpoint["comments"]["selected"] == ["1"]
        assert job.checkpoint["comment_assets"]["legacy"] == legacy
        assert db.get(CaptureRun, job.checkpoint["run_id"]).checkpoint == job.checkpoint
        assert db.scalar(select(func.count()).select_from(Comment)) == 1
        assert db.scalar(select(func.count()).select_from(CommentVersion)) == 1
        assert runner._comments(runner.Context(db, job, None), video)
        assert db.scalar(select(func.count()).select_from(Comment)) == 1
        assert db.scalar(select(func.count()).select_from(CommentVersion)) == 1
