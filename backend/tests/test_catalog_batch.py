"""Page serialization uses bounded queries and preserves archived display semantics."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import catalog
from app.models import (Asset, Base, CaptureRun, Collection, CollectionItem, Comment, CommentAsset,
                        Creator, MediaVariant, PlatformUser, SourceAccount, SourceSubscription,
                        UserSnapshot, Video, VideoAnnotation, VideoCreator, VideoPart, VideoStatSnapshot)


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


@pytest.fixture
def library(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        db.add(SourceAccount(id="account", name="synthetic", secret_encrypted="synthetic"))
        for index, name in enumerate(("media", "avatar-now", "avatar-then", "image-first", "image-last")):
            db.add(Asset(id=name, sha256=f"{index:064x}", size=100, kind="image"))
        for index in range(100):
            key = f"{index:03d}"
            db.add_all([
                PlatformUser(id=f"user-{key}", uid=str(index), display_name=f"current {index}",
                             signature="source signature", avatar_asset_id="avatar-now"),
                UserSnapshot(id=f"snapshot-{key}", user_id=f"user-{key}", display_name=f"archived {index}",
                             avatar_asset_id="avatar-then"),
                Creator(id=f"creator-{key}", user_id=f"user-{key}", alias="" if index == 0 else f"alias {index}",
                        description_override="", notes="private creator note", tags=["creator-tag"]),
                Video(id=f"video-{key}", bvid=f"BV{index:010d}", title=f"source {index}", description="source description",
                      created_at=NOW, published_at=NOW - timedelta(days=1), duration=600, cover_asset_id="avatar-now",
                      capture_status="complete", metadata_json={"media_properties": {
                          f"variant-{key}": {"codec": "old", "preserved": True}, "historical": {"keep": True}}}),
                VideoAnnotation(video_id=f"video-{key}", title_override="" if index == 0 else f"edited {index}",
                                description_override="", notes="private video note", tags=["video-tag"], starred=True),
                VideoCreator(video_id=f"video-{key}", creator_id=f"creator-{key}", role="owner"),
                VideoCreator(video_id=f"video-{key}", creator_id=f"creator-{key}", role="collaborator"),
                VideoPart(id=f"part-{key}-b", video_id=f"video-{key}", cid="2", position=1, title="second by ID"),
                VideoPart(id=f"part-{key}-a", video_id=f"video-{key}", cid="1", position=1, title="first by ID"),
                VideoPart(id=f"part-{key}-c", video_id=f"video-{key}", cid="3", position=2, title="later position"),
                MediaVariant(id=f"variant-{key}", part_id=f"part-{key}-a", asset_id="media", format_key="original",
                             quality="1080p", width=1920, height=1080, video_codec="hevc", audio_codec="aac",
                             metadata_json={"codec": "hevc"}),
                VideoStatSnapshot(id=f"stat-{key}-z", video_id=f"video-{key}", observed_at=NOW - timedelta(days=1),
                                  counts={"view": 1}),
                VideoStatSnapshot(id=f"stat-{key}-a", video_id=f"video-{key}", observed_at=NOW, counts={"view": 2}),
                VideoStatSnapshot(id=f"stat-{key}-b", video_id=f"video-{key}", observed_at=NOW,
                                  counts={"view": 900 + index, "like": 7, "custom_count": 3}),
                Collection(id=f"collection-{key}", source_id=key, title=f"folder {index}", enabled=True,
                           last_scan_at=NOW, monitor_state={"status": "complete"}),
                CollectionItem(collection_id=f"collection-{key}", source_resource_id="1", video_id=f"video-{key}"),
                CollectionItem(collection_id=f"collection-{key}", source_resource_id="2", video_id=f"video-{key}"),
                CollectionItem(collection_id=f"collection-{key}", source_resource_id="3", video_id=None),
                Comment(id=f"comment-{key}", video_id=f"video-{key}", rpid=key, root_rpid="12", parent_rpid="13",
                        author_user_id=f"user-{key}", author_snapshot_id=f"snapshot-{key}", content=f"comment {index}",
                        posted_at=NOW, like_count=5, reply_count=2),
                CommentAsset(comment_id=f"comment-{key}", asset_id="image-last", position=2),
                CommentAsset(comment_id=f"comment-{key}", asset_id="image-first", position=1),
            ])
            if index % 2 == 0:
                db.add(SourceSubscription(collection_id=f"collection-{key}", account_id="account", enabled=False,
                                          next_run_at=NOW + timedelta(hours=1)))
        for index in range(12):
            db.add(CaptureRun(id=f"capture-{index:02d}", video_id="video-000", status="complete",
                              created_at=NOW + timedelta(seconds=index), counts={"parts": index}))
        db.commit()
    yield engine, sessions
    engine.dispose()


@contextmanager
def queries(engine):
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.mark.parametrize("model,mapper,expected", [
    (Video, catalog.video_views, 7),
    (Creator, catalog.creator_views, 4),
    (Collection, catalog.collection_views, 4),
    (Comment, catalog.comment_views, 6),
])
def test_page_query_count_is_constant_for_one_twenty_four_and_one_hundred_rows(library, model, mapper, expected):
    engine, sessions = library
    for size in (1, 24, 100):
        # A fresh identity map prevents accidentally hiding per-row get() queries.
        with sessions() as db, queries(engine) as statements:
            result = catalog.page(db, select(model).order_by(model.id.desc()), page_size=size, batch_mapper=mapper)
        assert len(statements) == expected
        assert len(result["items"]) == size and result["total"] == 100
        assert result["page"] == 1 and result["page_size"] == size
        assert [item["id"] for item in result["items"]] == [f"{model.__name__.lower()}-{index:03d}"
                                                              for index in range(99, 99 - size, -1)]


def test_video_display_overrides_latest_stats_and_detail_order_are_preserved(library):
    _, sessions = library
    with sessions() as db:
        rows = list(db.scalars(select(Video).where(Video.id.in_(["video-000", "video-001"])).order_by(Video.id.desc())))
        views = catalog.video_views(db, rows)
        assert [item["title"] for item in views] == ["edited 1", ""]
        item = views[1]
        assert item["source_title"] == "source 0" and item["description"] == ""
        assert item["tags"] == ["video-tag"] and item["starred"] is True
        assert item["playable"] is True and item["parts_count"] == 3
        assert item["cover_url"] == "/api/v1/assets/avatar-now"
        assert [person["role"] for person in item["creators"]] == ["collaborator", "owner"]
        assert all(person["name"] == "" for person in item["creators"])
        assert item["stats"] == {"view": 900, "like": 7, "coin": None, "favorite": None, "share": None,
                                 "reply": None, "danmaku": None, "custom_count": 3,
                                 "observed_at": NOW.replace(tzinfo=None)}
        assert item["media_properties"] == {"variant-000": {"codec": "hevc", "preserved": True},
                                             "historical": {"keep": True}}
        assert "notes" not in item and "parts" not in item
        detail = catalog.video_view(db, rows[1], detail=True)
        assert {key: detail[key] for key in item} == item
        assert detail["notes"] == "private video note" and detail["title_override"] == ""
        assert detail["source_description"] == "source description" and detail["description_override"] == ""
        assert [part["id"] for part in detail["parts"]] == ["part-000-a", "part-000-b", "part-000-c"]
        assert detail["parts"][0]["variants"][0]["metadata"] == item["media_properties"]["variant-000"]
        assert detail["parts"][1]["variants"] == []
        assert [run["id"] for run in detail["capture_runs"]] == [f"capture-{index:02d}" for index in range(11, 1, -1)]


def test_creator_distinct_video_count_notes_and_collection_subscription_override(library):
    _, sessions = library
    with sessions() as db:
        creator = catalog.creator_view(db, db.get(Creator, "creator-000"))
        assert creator["saved_count"] == 1  # Two roles still represent one saved video.
        assert creator["name"] == "" and creator["source_name"] == "current 0"
        assert creator["notes"] == "private creator note" and creator["tags"] == ["creator-tag"]
        assert creator["description"] == "" and creator["source_description"] == "source signature"
        collections = list(db.scalars(select(Collection).where(Collection.id.in_(["collection-000", "collection-001"]))
                                      .order_by(Collection.id)))
        subscribed, plain = catalog.collection_views(db, collections)
        assert subscribed["saved_count"] == plain["saved_count"] == 2
        assert subscribed["subscribed"] is True and subscribed["enabled"] is False
        assert subscribed["monitor"] == {"status": "complete"} and subscribed["next_run_at"] is not None
        assert plain["subscribed"] is False and plain["enabled"] is True and plain["next_run_at"] is None
        assert catalog.collection_view(db, collections[0]) == subscribed


def test_comments_keep_historical_author_snapshot_and_image_order(library):
    _, sessions = library
    with sessions() as db:
        comment = db.get(Comment, "comment-000")
        item = catalog.comment_view(db, comment)
        assert item["author"] == {"uid": "0", "name": "archived 0", "avatar_url": "/api/v1/assets/avatar-then",
                                   "creator_id": "creator-000"}
        assert item["images"] == ["/api/v1/assets/image-first", "/api/v1/assets/image-last"]
        assert item["root_rpid"] == "12" and item["parent_rpid"] == "13"
        assert item["content"] == "comment 0" and item["like_count"] == 5 and item["reply_count"] == 2
        # The historical snapshot's missing avatar must not fall back to today's avatar.
        db.get(UserSnapshot, "snapshot-000").avatar_asset_id = None
        assert catalog.comment_view(db, comment)["author"]["avatar_url"] is None
        comment.author_snapshot_id = None
        assert catalog.comment_view(db, comment)["author"]["name"] == "current 0"
        comment.author_user_id = None
        assert catalog.comment_view(db, comment)["author"] == {
            "uid": None, "name": "未知作者", "avatar_url": None, "creator_id": None}
        comment.author_snapshot_id = "snapshot-000"
        assert catalog.comment_view(db, comment)["author"]["name"] == "archived 0"


def test_missing_relations_empty_pages_and_legacy_mapper_remain_supported(library):
    engine, sessions = library
    with sessions() as db:
        bare = Video(id="empty-video", bvid="BVempty00000", title="no archive")
        person = PlatformUser(id="empty-person", uid="empty", display_name="no videos")
        db.add_all([bare, person, Creator(id="empty-creator", user_id=person.id),
                    Collection(id="empty-folder", source_id="empty-folder")])
        db.commit()
        item = catalog.video_view(db, bare)
        assert item["title"] == "no archive" and item["tags"] == [] and item["starred"] is False
        assert item["creators"] == [] and item["parts_count"] == 0 and item["playable"] is False
        assert item["stats"] == dict.fromkeys(("view", "like", "coin", "favorite", "share", "reply", "danmaku", "observed_at"))
        assert catalog.creator_view(db, db.get(Creator, "empty-creator"))["saved_count"] == 0
        assert catalog.collection_view(db, db.get(Collection, "empty-folder"))["saved_count"] == 0
        result = catalog.page(db, select(Video).order_by(Video.id), page=2, page_size=1, mapper=lambda row: row.id)
        assert result["items"] == ["video-000"] and result["total"] == 101 and result["page"] == 2
        with queries(engine) as statements:
            for mapper in (catalog.video_views, catalog.creator_views, catalog.collection_views, catalog.comment_views):
                assert mapper(db, []) == []
        assert statements == []
        with queries(engine) as statements:
            result = catalog.page(db, select(Video).where(Video.id == "absent"), batch_mapper=catalog.video_views)
        assert result["items"] == [] and result["total"] == 0 and len(statements) == 2
