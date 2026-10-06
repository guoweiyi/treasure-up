from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select

from app.models import VideoStar
from test_postgres_integration import postgres_workspace, pytestmark


def test_existing_shared_stars_migrate_to_independent_personal_stars(postgres_workspace):
    space = postgres_workspace
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with space.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "a9130d724e61")
    with space.engine.begin() as connection:
        # Reflect the downgraded schema so future ORM columns cannot alter the
        # legacy fixture whose upgrade behavior this test is meant to verify.
        metadata = MetaData()
        users = Table("app_users", metadata, autoload_with=connection, resolve_fks=False)
        videos = Table("videos", metadata, autoload_with=connection, resolve_fks=False)
        annotations = Table("video_annotations", metadata, autoload_with=connection, resolve_fks=False)
        now = datetime.now(timezone.utc)
        user_ids, marked_id = [str(uuid4()), str(uuid4())], None
        for user_id, username in zip(user_ids, ("one", "two")):
            connection.execute(users.insert().values(id=user_id, username=username, password_hash="test",
                role="reader", disabled=False, created_at=now, updated_at=now))
        for index, starred in enumerate((True, False), start=1):
            video_id = str(uuid4())
            connection.execute(videos.insert().values(id=video_id, bvid=f"BVmigration0{index}",
                title="marked" if starred else "plain", description="", duration=0, source_state="available",
                capture_status="pending", metadata_json={}, created_at=now, updated_at=now))
            connection.execute(annotations.insert().values(id=str(uuid4()), video_id=video_id, starred=starred,
                notes="", tags=[], created_at=now, updated_at=now))
            if starred:
                marked_id = video_id
        expected = {(user_id, marked_id) for user_id in user_ids}
    with space.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with space.sessions() as db:
        assert set(db.execute(select(VideoStar.user_id, VideoStar.video_id)).all()) == expected
        assert all(column["nullable"] for column in inspect(space.engine).get_columns("playback_sessions") if column["name"] == "user_id")
