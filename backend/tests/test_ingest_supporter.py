from pathlib import Path

import httpx
import pytest
import yt_dlp

from app.ingest import media
from app.ingest.client import BiliClient, parse_cookies
from app.ingest.errors import IngestError
from app.models import Video, VideoPart
from test_ingest_runner import db  # noqa: F401


def playinfo(duration=1588):
    return {"timelength": duration * 1000 - 63, "quality": 120,
        "accept_description": ["4K 超清"], "support_formats": [{"quality": 120, "new_description": "4K 超清"}],
        "dash": {"duration": duration, "video": [{"id": 120, "width": 3840, "height": 1634,
            "frameRate": "60", "codecs": "avc1.640033", "mimeType": "video/mp4", "bandwidth": 8615000,
            "baseUrl": "https://example.bilivideo.com/video-120.m4s?token=NEVER_PERSIST"}],
            "audio": [{"id": 30280, "codecs": "mp4a.40.2", "mimeType": "audio/mp4", "bandwidth": 187830,
                "baseUrl": "https://example.bilivideo.com/audio-30280.m4s?token=NEVER_PERSIST"}],
            "dolby": {"type": 0, "audio": []}}}


def test_explicit_playurl_requests_highest_modes_without_preview_flag():
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.path.endswith("/nav"):
            data = {"isLogin": True, "wbi_img": {"img_url": "https://i0.hdslb.com/" + "a"*32 + ".png", "sub_url": "https://i0.hdslb.com/" + "b"*32 + ".png"}}
        else:
            data = playinfo()
        return httpx.Response(200, json={"code": 0, "data": data})
    client = BiliClient("SESSDATA=fixture", transport=httpx.MockTransport(transport))
    try:
        client.playurl("BV1234567890", "123")
    finally:
        client.close()
    params = dict(requests[-1].url.params)
    assert params["qn"] == "127" and params["fnval"] == "4048" and params["fourk"] == "1"
    assert "try_look" not in params and params["w_rid"] and params["fnver"] == "0"


@pytest.mark.parametrize("case,code", [("preview_flag", "supporter_access_required"),
    ("no_access", "supporter_access_required"), ("short_duration", "preview_only"),
    ("preview_label", "preview_only"), ("dash_mismatch", "preview_only"), ("drm", "unsupported_media")])
def test_supporter_preview_or_unverified_full_stream_is_rejected(case, code):
    source = {"is_upower_exclusive": True, "is_upower_play": True, "is_upower_preview": False}
    data = playinfo()
    if case == "preview_flag": source["is_upower_preview"] = True
    if case == "no_access": source["is_upower_play"] = False
    if case == "short_duration": data["timelength"] = 300000
    if case == "preview_label": data["accept_description"] = ["试看"]
    if case == "dash_mismatch": data["dash"]["duration"] = 300
    if case == "drm": data["is_drm"] = True
    with pytest.raises(IngestError) as error:
        media.validate_supporter_playinfo(source, data, 1588)
    assert error.value.code == code and not error.value.retryable


def test_supporter_uses_explicit_full_playurl_and_official_formats_without_ssr(db, monkeypatch):
    video = Video(bvid="BV1234567890", aid="123", title="fixture")
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid="999", position=1, duration=1588)
    db.add(part); db.commit()
    calls = []
    class Client:
        cookies = parse_cookies("SESSDATA=fixture")
        def view(self, _):
            return {"pages": [{"cid": "999", "page": 1}], "is_upower_exclusive": True,
                "is_upower_play": True, "is_upower_preview": False}
        def playurl(self, bvid, cid):
            calls.append((bvid, cid))
            return playinfo()
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", lambda *_a, **_k: pytest.fail("supporter flow must not use stale webpage preview"))
    def write_fixture(downloader, info):
        Path(downloader.prepare_filename(info)).write_bytes(b"offline media fixture")
    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_info", write_fixture)
    monkeypatch.setattr(media, "_probe", lambda _: ({"codec_name": "h264", "width": 3840, "height": 1634,
        "avg_frame_rate": "60/1"}, {"codec_name": "aac"}, 1588))
    monkeypatch.setattr(media, "_verify_audio_decode", lambda *a: None)
    variant, reused = media.archive_media(db, Client(), video, part, {"quality": "best"})
    assert not reused and variant.video_codec == "h264" and calls == [(video.bvid, "999")]
    assert video.metadata_json["access"]["upower_exclusive"] is True
    quality = video.metadata_json["source_quality"]["parts"][part.id]
    assert quality["maximum"]["video_bitrate_bps"] == 8615000
    assert quality["maximum_audio"]["audio_bitrate_bps"] == 187830
    assert quality["features"] == {"dolby_vision_available": False, "hdr10_available": False, "dolby_atmos_candidate": False}
    props = video.metadata_json["media_properties"][variant.id]
    assert not props["dolby_vision"] and not props["dolby_atmos"]
    assert "NEVER_PERSIST" not in str(video.metadata_json)
