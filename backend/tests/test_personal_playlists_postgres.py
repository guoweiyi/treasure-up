"""Personal list locking and legacy migration on random isolated PostgreSQL DBs."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import threading
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, func, select, text

from app.models import PersonalPlaylist, PersonalPlaylistItem, User, Video, VideoStar
from app.personal_playlists import ItemInput, playlists, save_item, set_star
from test_postgres_integration import postgres_workspace  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit isolated PostgreSQL opt-in required")


def test_old_personal_stars_migrate_to_watch_later_with_user_isolation(postgres_workspace):
    space = postgres_workspace
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with space.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "c8412d60fa31")
    with space.engine.begin() as connection:
        # Seed the historical schema, not today's ORM (which may require
        # columns introduced after the revision under test).
        metadata = MetaData()
        users = Table("app_users", metadata, autoload_with=connection, resolve_fks=False)
        videos = Table("videos", metadata, autoload_with=connection, resolve_fks=False)
        stars = Table("video_stars", metadata, autoload_with=connection, resolve_fks=False)
        now = datetime.now(timezone.utc)
        expected = {}
        for index, username in enumerate(("a", "b"), start=1):
            user_id, video_id = str(uuid4()), str(uuid4())
            connection.execute(users.insert().values(id=user_id, username=username, password_hash="fixture",
                role="reader", disabled=False, created_at=now, updated_at=now))
            connection.execute(videos.insert().values(id=video_id, bvid=f"BV{index:010d}", title="", description="",
                duration=0, source_state="available", capture_status="pending", metadata_json={}, created_at=now, updated_at=now))
            connection.execute(stars.insert().values(id=str(uuid4()), user_id=user_id, video_id=video_id,
                created_at=now, updated_at=now))
            expected[user_id] = video_id
    with space.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with space.sessions() as db:
        rows = db.execute(select(PersonalPlaylist, PersonalPlaylistItem).join(PersonalPlaylistItem,
            PersonalPlaylistItem.playlist_id == PersonalPlaylist.id)).all()
        assert len(rows) == 2
        assert {playlist.user_id: item.video_id for playlist, item in rows} == expected
        assert all(playlist.system_key == "watch_later" and not item.watched and item.note == "" for playlist, item in rows)
        assert db.scalar(select(func.count()).select_from(VideoStar)) == 2


def test_same_user_first_default_and_star_mutations_serialize_across_sessions(postgres_workspace):
    space = postgres_workspace
    with space.sessions() as db:
        user = User(username="reader", password_hash="fixture", role="reader")
        video = Video(bvid="BV0000000001")
        db.add_all([user, video]); db.commit()
        user_id, video_id = user.id, video.id
    barrier = threading.Barrier(2)
    def initialize(kind):
        with space.sessions() as db:
            db.execute(text("SET lock_timeout = '5s'"))
            user = db.get(User, user_id)
            barrier.wait(timeout=5)
            if kind == "list":
                return playlists(identity=(user, None), db=db)["items"][0]["id"]
            set_star(db, user_id, video_id, True)
            return db.scalar(select(PersonalPlaylist.id).where(PersonalPlaylist.user_id == user_id))
    with ThreadPoolExecutor(max_workers=2) as pool:
        identifiers = list(pool.map(initialize, ["list", "star"]))
    assert identifiers[0] == identifiers[1]
    barrier = threading.Barrier(2)
    def edit(kind):
        with space.sessions() as db:
            db.execute(text("SET lock_timeout = '5s'"))
            user = db.get(User, user_id)
            barrier.wait(timeout=5)
            if kind == "star":
                set_star(db, user_id, video_id, True)
            else:
                save_item(identifiers[0], video_id, ItemInput(watched=True, note="preserve"), (user, None), db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(edit, ["star", "watched"]))
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(PersonalPlaylist)) == 1
        assert db.scalar(select(func.count()).select_from(PersonalPlaylistItem)) == 1
        assert db.scalar(select(func.count()).select_from(VideoStar)) == 1
        item = db.scalar(select(PersonalPlaylistItem))
        assert item.watched and item.note == "preserve"
