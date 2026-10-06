"""Real concurrent queue submissions, isolated PostgreSQL database only."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text

from app import schemas
from app.delivery_api import sync_batch, sync_video
from app.models import Job, OutboxEvent, StorageProfile, User
from test_delivery_api import seed_media
from test_postgres_integration import postgres_workspace

pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit isolated PostgreSQL test opt-in required")


def test_concurrent_batch_and_single_submission_share_one_job(postgres_workspace):
    space = postgres_workspace
    with space.sessions() as db:
        video, _, variant, _ = seed_media(db, space.root)
        target = StorageProfile(name="batch-target", kind="local", config={"root": str(space.root / "copy")})
        user = User(username="batch-admin", role="admin", password_hash="unused")
        db.add_all([target, user]); db.commit()
        video_id, variant_id, target_id, user_id = video.id, variant.id, target.id, user.id
    barrier = threading.Barrier(2, timeout=10)

    def submit(batch):
        with space.sessions() as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            user = db.get(User, user_id)
            barrier.wait()
            if batch:
                result = sync_batch(schemas.ReplicaBatchSyncInput(target_profile_id=target_id,
                    items=[{"video_id": video_id, "variant_ids": [variant_id]}]), user=user, db=db)
                return result["items"][0]["job"]["id"]
            return sync_video(video_id, schemas.VideoSyncInput(target_profile_id=target_id), user=user, db=db)["id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit, value) for value in (True, False)]
        ids = [future.result(timeout=15) for future in futures]
    assert len(set(ids)) == 1
    with space.sessions() as db:
        assert len(list(db.scalars(select(Job)))) == 1
        assert len(list(db.scalars(select(OutboxEvent)))) == 1
