"""Personal list locking and legacy migration on random isolated PostgreSQL DBs."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

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
    with space.sessions() as db:
        first, second = User(username="a", password_hash="fixture", role="reader"), User(username="b", password_hash="fixture", role="reader")
        videos = [Video(bvid="BV0000000001"), Video(bvid="BV0000000002")]
        db.add_all([first, second, *videos]); db.flush()
        db.add_all([VideoStar(user_id=first.id, video_id=videos[0].id), VideoStar(user_id=second.id, video_id=videos[1].id)])
        db.commit()
        expected = {first.id: videos[0].id, second.id: videos[1].id}
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
