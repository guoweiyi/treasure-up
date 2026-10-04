from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select

from app.models import User, Video, VideoAnnotation, VideoStar
from test_postgres_integration import postgres_workspace, pytestmark


def test_existing_shared_stars_migrate_to_independent_personal_stars(postgres_workspace):
    space = postgres_workspace
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with space.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "a9130d724e61")
    with space.sessions() as db:
        first, second = User(username="one", password_hash="test"), User(username="two", password_hash="test")
        marked, plain = Video(bvid="BVmigration01", title="marked"), Video(bvid="BVmigration02", title="plain")
        db.add_all([first, second, marked, plain]); db.flush()
        db.add_all([VideoAnnotation(video_id=marked.id, starred=True), VideoAnnotation(video_id=plain.id, starred=False)])
        db.commit()
        expected = {(first.id, marked.id), (second.id, marked.id)}
    with space.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with space.sessions() as db:
        assert set(db.execute(select(VideoStar.user_id, VideoStar.video_id)).all()) == expected
        assert all(column["nullable"] for column in inspect(space.engine).get_columns("playback_sessions") if column["name"] == "user_id")
