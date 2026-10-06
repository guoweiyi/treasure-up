import pytest
from app.models import Setting
from app.schemas import DisplaySettings
from test_api import context, login
from test_delivery_api import seed_media


def test_unconfigured_library_is_private_on_every_transport(context):
    client, db, tmp = context
    db.delete(db.get(Setting, "display")); db.commit()
    video, part, _, asset = seed_media(db, tmp, hls=True)
    paths = ["/videos", f"/videos/{video.id}", "/creators", "/collections", "/playlists", "/library/stats",
             f"/videos/{video.id}/comments", f"/videos/{video.id}/statistics", f"/parts/{part.id}/danmaku",
             f"/assets/{asset.id}", "/playback-sessions/missing/manifest.m3u8",
             f"/playback-sessions/missing/assets/{asset.id}", "/playback-sessions/missing/probe/missing"]
    for path in paths:
        assert client.get("/api/v1" + path).status_code == 401, path
    assert client.head(f"/api/v1/assets/{asset.id}").status_code == 401
    assert client.post("/api/v1/playback-sessions", json={"part_id": part.id}).status_code == 401
    assert client.post("/api/v1/playback-sessions/missing/observations", json={}).status_code == 401
    for path in ["/health", "/api/v1/server", "/api/v1/auth/status", "/api/v1/library/settings"]:
        assert client.get(path).status_code == 200
    assert DisplaySettings().allow_guest_access is False
    assert client.get("/api/v1/auth/status").json()["allow_guest_access"] is False
    login(client, "reader")
    assert client.get("/api/v1/videos").status_code == 200
    assert client.get(f"/api/v1/assets/{asset.id}", headers={"Range": "bytes=0-9"}).status_code == 206
    assert client.post("/api/v1/playback-sessions", json={"part_id": part.id}).status_code == 200


def test_disabling_guests_blocks_preexisting_playback_and_reopening_restores_reads(context):
    client, db, tmp = context
    _, part, _, _ = seed_media(db, tmp, hls=True)
    play = client.post("/api/v1/playback-sessions", json={"part_id": part.id}).json()
    assert client.get(play["url"]).status_code == 200
    db.get(Setting, "display").value = {"allow_guest_access": False}; db.commit()
    assert client.get(play["url"]).status_code == 401
    assert client.get("/api/v1/videos").status_code == 401
    login(client, "reader")
    assert client.patch("/api/v1/admin/settings", json={"display": {"allow_guest_access": True}}).status_code == 403
    login(client)
    assert client.patch("/api/v1/admin/settings", json={"display": {"allow_guest_access": True}}).status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/videos").status_code == 200


@pytest.mark.parametrize("value", [None, "true", 1, False])
def test_malformed_guest_setting_fails_closed(context, value):
    client, db, _ = context
    from sqlalchemy.orm.attributes import flag_modified
    row = db.get(Setting, "display")
    row.value = {"allow_guest_access": value}
    flag_modified(row, "value")
    db.commit()
    assert client.get("/api/v1/videos").status_code == 401
