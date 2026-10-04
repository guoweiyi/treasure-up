import json
import time

import httpx
import pytest

from app.ingest.client import BiliClient, clean_raw, cookie_header, parse_cookies, sign_wbi
from app.ingest.errors import IngestError
from app.ingest.protobuf import decode_danmaku


@pytest.mark.parametrize("status,code,retryable", [(404, "asset_not_found", False), (410, "asset_not_found", False), (500, "http_error", True), (429, "rate_limited", True)])
def test_public_image_missing_is_distinct_from_transient_failure(status, code, retryable):
    with_client = BiliClient("", transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    try:
        with pytest.raises(IngestError) as caught:
            with_client.asset("https://i0.hdslb.com/missing.jpg")
        assert caught.value.code == code and caught.value.retryable is retryable
    finally:
        with_client.close()


def varint(value):
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def integer(field, value):
    return varint(field << 3) + varint(value)


def blob(field, value):
    return varint(field << 3 | 2) + varint(len(value)) + value


def segment(identity=9007199254740993, content="中文弹幕🫧"):
    return blob(1, integer(1, identity) + integer(2, 1234) + integer(3, 5) + integer(4, 36) + integer(5, 0x123ABC) + blob(7, content.encode()))


def test_protobuf_preserves_long_id_unicode_and_unknown_fields():
    data = segment() + integer(99, 42) + blob(100, b"unknown") + varint(101 << 3 | 1) + b"12345678"
    assert decode_danmaku(data) == [{"id": "9007199254740993", "time": 1.234, "mode": 5, "size": 36, "color": "#123abc", "text": "中文弹幕🫧"}]
    assert decode_danmaku(b"") == []


@pytest.mark.parametrize("data", [segment()[:-1], "<html>风控</html>".encode(), b"\x00", b"\xff" * 11, blob(1, blob(7, b"\xff")), integer(1, 42), blob(1, integer(1, 2))])
def test_invalid_protobuf_is_not_empty_success(data):
    with pytest.raises(IngestError):
        decode_danmaku(data)


def test_closed_danmaku_is_explicit():
    with pytest.raises(IngestError, match="关闭") as error:
        decode_danmaku(integer(2, 1))
    assert not error.value.retryable


def test_netscape_cookie_host_path_expiry_httponly():
    cookies = parse_cookies("\n".join([
        "# Netscape HTTP Cookie File",
        "#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tsecret",
        "api.bilibili.com\tFALSE\t/x/space\tTRUE\t0\tspace\tlimited",
        ".bilibili.com\tTRUE\t/\tTRUE\t1\told\texpired",
        ".evil-bilibili.com\tTRUE\t/\tTRUE\t0\tbad\tbad",
    ]), now=10)
    assert cookie_header(cookies, "https://api.bilibili.com/x/space/wbi/info") == "space=limited; SESSDATA=secret"
    assert cookie_header(cookies, "https://api.bilibili.com/x/spaces") == "SESSDATA=secret"
    assert cookie_header(cookies, "https://i0.hdslb.com/face/a") == ""
    assert cookie_header(cookies, "http://api.bilibili.com/x/") == ""
    assert len(cookies) == 2


def test_wbi_known_vector():
    images = {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png", "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}
    # Published worked example uses 'zab' (the later Python demo uses 'baz').
    signed = sign_wbi({"foo": "114", "bar": "514", "zab": 1919810}, images, 1702204169)
    assert signed["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"
    assert signed["wts"] == "1702204169"


@pytest.mark.parametrize("status,body,code", [(429, b"SESSDATA=do-not-show", "rate_limited"), (200, b"<html>do-not-show</html>", "invalid_response"), (200, b'{"code":-101,"message":"do-not-show"}', "login_required")])
def test_safe_api_errors_never_quote_response(status, body, code):
    client = BiliClient("SESSDATA=secret", interval=0, transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body)))
    with pytest.raises(IngestError) as error:
        client.nav()
    assert error.value.code == code
    assert "do-not-show" not in str(error.value)
    assert "secret" not in str(error.value)
    client.close()


def test_nav_anonymous_wbi_but_auth_check_fails():
    data = {"code": -101, "data": {"isLogin": False, "wbi_img": {"img_url": "https://i0.hdslb.com/" + "a" * 32 + ".png", "sub_url": "https://i0.hdslb.com/" + "b" * 32 + ".png"}}}
    client = BiliClient(interval=0, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)))
    assert client.nav(allow_anonymous=True)["wbi_img"]
    with pytest.raises(IngestError):
        client.nav()
    client.close()


def test_image_hosts_no_auth_and_no_redirect_follow():
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    client = BiliClient("SESSDATA=secret", interval=0, transport=httpx.MockTransport(respond))
    with pytest.raises(IngestError):
        client.asset("https://i0.hdslb.com/image.png")
    assert len(seen) == 1 and "cookie" not in seen[0].headers
    with pytest.raises(IngestError):
        client.asset("https://hdslb.com.evil.test/image")
    assert len(seen) == 1
    client.close()


def test_raw_snapshot_redacts_tokens_and_url_queries():
    assert clean_raw({"url": "https://u:p@i0.hdslb.com/a?sign=SECRET#x", "Cookie": "SECRET", "nested": [{"access_token": "SECRET", "name": "公开昵称"}]}) == {"url": "https://i0.hdslb.com/a", "nested": [{"name": "公开昵称"}]}


def test_binary_segment_0a7b_is_not_mistaken_for_json():
    content = "x" * 119
    body = blob(1, integer(1, 1) + blob(7, content.encode()))
    assert body[:2] == b"\x0a\x7b"
    client = BiliClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        headers={"content-type": "application/octet-stream"}, content=body)))
    client.images = {"img_url": "https://i0.hdslb.com/" + "a" * 32 + ".png", "sub_url": "https://i0.hdslb.com/" + "b" * 32 + ".png"}
    client.images_at = time.monotonic()
    try:
        assert decode_danmaku(client.segment("1", "2", 2))[0]["text"] == content
    finally:
        client.close()


@pytest.mark.parametrize("mime", ["application/json", "application/octet-stream"])
@pytest.mark.parametrize("code,expected", [(-352, "rate_limited"), (-401, "rate_limited"), (-101, "login_required"), (-500, "invalid_danmaku")])
def test_real_json_danmaku_errors_keep_authentication_and_backoff_semantics(mime, code, expected):
    client = BiliClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        headers={"content-type": mime}, content=json.dumps({"code": code, "message": "SECRET"}).encode())))
    client.images = {"img_url": "https://i0.hdslb.com/" + "a" * 32 + ".png", "sub_url": "https://i0.hdslb.com/" + "b" * 32 + ".png"}
    client.images_at = time.monotonic()
    try:
        with pytest.raises(IngestError) as error:
            client.segment("1", "2", 1)
        assert error.value.code == expected and "SECRET" not in str(error.value)
    finally:
        client.close()
