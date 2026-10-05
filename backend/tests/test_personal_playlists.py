import pytest
from sqlalchemy import func, select

from app.models import PersonalPlaylist, PersonalPlaylistItem, User, Video, VideoStar
from test_api import context, login, seed_catalog  # noqa: F401


P = "/api/v1/me/playlists"


def lists(client):
    response = client.get(P)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_private_lists_require_session_and_csrf_and_hide_other_users(context):
    client, db, _ = context
    video, _, _ = seed_catalog(db)
    assert client.get(P).status_code == 401
    login(client)
    own = client.post(P, json={"name": "个人旅行", "description": "私有"}).json()
    assert client.put(f"{P}/{own['id']}/videos/{video.id}", json={"note": "私人备注"}).status_code == 200
    login(client, "reader")
    default = lists(client)[0]
    assert default["kind"] == "watch_later" and default["item_count"] == 0
    for method, path, body in [
        ("get", f"{P}/{own['id']}/videos", None),
        ("patch", f"{P}/{own['id']}", {"name": "越权"}),
        ("delete", f"{P}/{own['id']}", None),
        ("put", f"{P}/{own['id']}/videos/{video.id}", {}),
        ("delete", f"{P}/{own['id']}/videos/{video.id}", None),
    ]:
        assert client.request(method, path, **({"json": body} if body is not None else {})).status_code == 404
    assert client.get(f"/api/v1/me/videos/{video.id}/playlists").json() == {"playlist_ids": []}
    client.headers.pop("X-CSRF-Token")
    assert client.post(P, json={"name": "no csrf"}).status_code == 403
    assert client.put(f"{P}/{default['id']}/videos/{video.id}", json={}).status_code == 403
    # Neither public video detail nor list cards contain a different user's note.
    assert "私人备注" not in client.get(f"/api/v1/videos/{video.id}").text


def test_legacy_stars_backfill_once_and_default_membership_stays_bidirectional(context):
    client, db, _ = context
    video, _, _ = seed_catalog(db)
    user = db.scalar(select(User).where(User.username == "reader"))
    db.add(VideoStar(user_id=user.id, video_id=video.id)); db.commit()
    login(client, "reader")
    default = lists(client)[0]
    assert default["item_count"] == 1
    url = f"{P}/{default['id']}/videos/{video.id}"
    assert client.put(url, json={"watched": True, "note": "稍后整理"}).status_code == 200
    assert client.put(f"/api/v1/videos/{video.id}/star", json={"starred": True}).status_code == 200
    item = client.get(f"{P}/{default['id']}/videos").json()["items"][0]
    assert item["playlist_item"]["note"] == "稍后整理" and item["playlist_item"]["watched"] is True
    assert item["starred"] is True
    assert client.put(f"/api/v1/videos/{video.id}/star", json={"starred": False}).status_code == 200
    assert lists(client)[0]["item_count"] == 0
    assert client.put(url, json={}).status_code == 200
    assert client.get("/api/v1/videos?starred=true").json()["total"] == 1
    assert client.delete(url).status_code == 200
    assert client.get("/api/v1/videos?starred=true").json()["total"] == 0
    assert lists(client)[0]["item_count"] == 0  # No repeated legacy resurrection.
    assert client.delete(f"{P}/{default['id']}").status_code == 409
    assert client.patch(f"{P}/{default['id']}", json={"name": "改名"}).status_code == 409


def test_moves_are_atomic_merge_notes_and_do_not_cross_users(context):
    client, db, _ = context
    video, _, _ = seed_catalog(db)
    login(client)
    foreign = lists(client)[0]
    login(client, "reader")
    default = lists(client)[0]
    custom = client.post(P, json={"name": "旅途"}).json()
    source = f"{P}/{default['id']}/videos/{video.id}"
    target = f"{P}/{custom['id']}/videos/{video.id}"
    client.put(source, json={"note": "来源备注", "watched": True})
    assert client.post(source + "/move", json={"target_playlist_id": foreign["id"]}).status_code == 404
    assert client.get(f"{P}/{default['id']}/videos").json()["total"] == 1
    client.put(target, json={"note": "保留目标备注"})
    assert client.post(source + "/move", json={"target_playlist_id": custom["id"]}).status_code == 200
    assert client.get("/api/v1/videos?starred=true").json()["total"] == 0
    item = client.get(f"{P}/{custom['id']}/videos?watched=watched").json()["items"][0]
    assert item["playlist_item"]["note"] == "保留目标备注"
    assert client.get(f"{P}/{custom['id']}/videos?watched=unwatched").json()["total"] == 0
    assert client.post(target + "/move", json={"target_playlist_id": default["id"]}).status_code == 200
    assert client.get("/api/v1/videos?starred=true").json()["total"] == 1
    assert client.get(f"{P}/{custom['id']}/videos").json()["total"] == 0


def test_personal_pagination_search_and_deletion_only_affect_membership(context):
    client, db, _ = context
    login(client, "reader")
    personal = client.post(P, json={"name": "资料"}).json()
    videos = [Video(bvid=f"BV{i:010d}", title=f"episode {i}") for i in range(5)]
    db.add_all(videos); db.commit()
    for video in videos:
        client.put(f"{P}/{personal['id']}/videos/{video.id}", json={})
    found = []
    for page in (1, 2, 3):
        result = client.get(f"{P}/{personal['id']}/videos", params={"page": page, "page_size": 2}).json()
        assert result["total"] == 5 and result["scope_title"] == "资料"
        found.extend(row["id"] for row in result["items"])
    assert len(set(found)) == 5
    assert client.get(f"{P}/{personal['id']}/videos?q=episode%204").json()["total"] == 1
    assert client.delete(f"{P}/{personal['id']}").status_code == 200
    assert db.scalar(select(func.count()).select_from(PersonalPlaylistItem)) == 0
    assert db.scalar(select(func.count()).select_from(Video)) == 5


def test_admin_cannot_inspect_reader_private_lists(context):
    client, db, _ = context
    login(client, "reader")
    private = client.post(P, json={"name": "读者私人"}).json()
    login(client)
    assert client.get(f"{P}/{private['id']}/videos").status_code == 404
    assert all(row["id"] != private["id"] for row in lists(client))


@pytest.mark.parametrize("change", ["remove", "move"])
def test_membership_changes_during_card_hydration_do_not_break_list_response(context, monkeypatch, change):
    from sqlalchemy import delete, update
    from sqlalchemy.orm import Session
    from app import catalog
    client, db, _ = context
    video, _, _ = seed_catalog(db)
    login(client, "reader")
    source = client.post(P, json={"name": "来源"}).json()
    target = client.post(P, json={"name": "目标"}).json()
    client.put(f"{P}/{source['id']}/videos/{video.id}", json={"note": "读取时备注", "watched": True})
    original = catalog.video_views
    def while_hydrating(session, videos, **kwargs):
        mapped = original(session, videos, **kwargs)
        # Commit an independent session's mutation after the video page was
        # selected, before the old implementation fetched membership a second time.
        with Session(db.get_bind()) as concurrent:
            where = (PersonalPlaylistItem.playlist_id == source["id"], PersonalPlaylistItem.video_id == video.id)
            if change == "remove":
                concurrent.execute(delete(PersonalPlaylistItem).where(*where))
            else:
                concurrent.execute(update(PersonalPlaylistItem).where(*where)
                    .values(playlist_id=target["id"], note="修改后备注"))
            concurrent.commit()
        return mapped
    monkeypatch.setattr(catalog, "video_views", while_hydrating)
    response = client.get(f"{P}/{source['id']}/videos")
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["playlist_item"]["note"] == "读取时备注" and item["playlist_item"]["watched"] is True
    monkeypatch.setattr(catalog, "video_views", original)
    assert client.get(f"{P}/{source['id']}/videos").json()["items"] == []
    assert db.get(Video, video.id) is not None


def test_playable_only_filters_before_pagination_without_hiding_management_items(context):
    from app.models import Asset, MediaVariant, VideoPart
    client, db, _ = context
    login(client, "reader")
    personal = lists(client)[0]
    videos = [Video(bvid=f"BV{i:010d}") for i in range(3)]
    asset = Asset(sha256="c" * 64, size=1, kind="video", mime_type="video/mp4")
    db.add_all([*videos, asset]); db.flush()
    for index, video in enumerate(videos):
        part = VideoPart(video_id=video.id, cid=str(index))
        db.add(part); db.flush()
        if index != 1:
            db.add_all([MediaVariant(part_id=part.id, asset_id=asset.id, kind=kind, format_key=kind)
                        for kind in ("archive", "playback")])
    db.commit()
    for video in videos:
        client.put(f"{P}/{personal['id']}/videos/{video.id}", json={})
    assert client.get(f"{P}/{personal['id']}/videos").json()["total"] == 3
    pages = [client.get(f"{P}/{personal['id']}/videos", params={"playable_only": True,
        "page_size": 1, "page": page}).json() for page in (1, 2)]
    assert all(page["total"] == 2 for page in pages)
    assert {page["items"][0]["id"] for page in pages} == {videos[0].id, videos[2].id}


def test_source_monitor_reports_late_paid_details_and_only_saved_consent(context):
    from app.models import Collection, CollectionItem, SourceAccount, SourceSubscription
    client, db, _ = context
    account = SourceAccount(name="fixture", secret_encrypted="fixture")
    collection = Collection(source_id="123", kind="creator", title="UP")
    # A metadata-only successful policy is not a saved media archive.
    video = Video(bvid="BV0000000001", metadata_json={"access": {"upower_exclusive": True},
        "ingest_state": {"media": "disabled"}}, capture_status="complete")
    db.add_all([account, collection, video]); db.flush()
    source = SourceSubscription(collection_id=collection.id, account_id=account.id)
    db.add_all([source, CollectionItem(collection_id=collection.id, source_resource_id="2:1", video_id=video.id, source_state="available")]); db.commit()
    login(client)
    response = client.get("/api/v1/admin/sources").json()["items"][0]
    assert response["monitor"]["paid"] == {"detected_count": 1, "pending_count": 1, "consent_required": True}
    response = client.patch(f"/api/v1/admin/sources/{source.id}", json={"policy": {"include_paid_videos": True}})
    assert response.status_code == 200 and response.json()["monitor"]["paid"]["consent_required"] is False
