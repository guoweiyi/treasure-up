"""Synthetic HTTP only: never contact Bilibili or use a real account credential."""
import logging
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.ingest import passport
from app.ingest.errors import IngestError

OLD_CSRF = "a" * 32
NEW_CSRF = "b" * 32
REFRESH_CSRF = "c" * 32
OLD_COOKIE = f"SESSDATA=old-sessdata%2Cepoch; bili_jct={OLD_CSRF}; DedeUserID=123; buvid3=device-three; buvid4=device-four; other=value"
OLD_TOKEN = "old-private-refresh-token"
NEW_TOKEN = "new-private-refresh-token"
QR_KEY = "private-qr-key"
QR_URL = f"https://passport.bilibili.com/h5-app/passport/login/scan?navhide=1&qrcode_key={QR_KEY}"
ACCOUNT_QR_URL = f"https://account.bilibili.com/h5/account-h5/auth/scan-web?navhide=1&callback=close&qrcode_key={QR_KEY}"


def headers(*, uid="123", omit=(), extra=()):
    cookies = {"SESSDATA": "new-sessdata%2Cepoch", "bili_jct": NEW_CSRF, "DedeUserID": uid}
    return [("set-cookie", f"{name}={value}; Path=/; Domain=.bilibili.com; Secure; HttpOnly")
            for name, value in cookies.items() if name not in omit] + list(extra)


def success(data=None, **kwargs):
    return httpx.Response(200, json={"code": 0, "data": data}, **kwargs)


def csrf_page():
    return httpx.Response(200, text=f'<html><div id="1-name">{REFRESH_CSRF}</div></html>')


def client(handler):
    return passport.BiliPassport(transport=httpx.MockTransport(handler), clock=lambda: 1_700_000_000)


def test_rotation_preserves_cookie_fields_and_confirmation_is_separate_new_cookie_old_token():
    requests = []
    phases = []
    def handler(request):
        requests.append(request)
        if request.url.path.startswith("/correspond/1/"):
            assert request.url.host == "www.bilibili.com"
            assert len(request.url.path.rsplit("/", 1)[1]) == 256
            assert request.headers["cookie"] == OLD_COOKIE
            phases.append("csrf")
            return csrf_page()
        if str(request.url) == passport.REFRESH:
            phases.append("rotate")
            assert phases == ["csrf", "before", "rotate"]
            assert request.method == "POST"
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            assert parse_qs(request.content.decode()) == {"csrf": [OLD_CSRF], "refresh_csrf": [REFRESH_CSRF],
                "refresh_token": [OLD_TOKEN], "source": ["main_web"]}
            return success({"refresh_token": NEW_TOKEN}, headers=headers())
        assert str(request.url) == passport.CONFIRM
        assert request.headers["cookie"] == credential.cookie_text
        assert parse_qs(request.content.decode()) == {"csrf": [NEW_CSRF], "refresh_token": [OLD_TOKEN]}
        return success()
    with client(handler) as protocol:
        credential = protocol.refresh(OLD_COOKIE, OLD_TOKEN, timestamp_ms=1_700_000_000_000,
                                      before_rotate=lambda: phases.append("before"))
        assert len(requests) == 2, "refresh must not invalidate the old token before durable persistence"
        assert credential.uid == "123" and credential.refresh_token == NEW_TOKEN and credential.bili_jct == NEW_CSRF
        assert "old-sessdata" not in credential.cookie_text
        assert "buvid3=device-three" in credential.cookie_text and "buvid4=device-four" in credential.cookie_text
        assert "other=value" in credential.cookie_text
        assert NEW_TOKEN not in repr(credential) and "new-sessdata" not in repr(credential)
        protocol.confirm_refresh(credential.cookie_text, OLD_TOKEN)
    assert len(requests) == 3


def test_before_rotate_failure_stops_the_mutation_request():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return csrf_page()
    def stop():
        raise RuntimeError("stale generation")
    with client(handler) as protocol, pytest.raises(RuntimeError, match="stale generation"):
        protocol.refresh(OLD_COOKIE, OLD_TOKEN, before_rotate=stop)
    assert len(calls) == 1


def test_correspond_is_rsa_oaep_sha256_and_uses_server_time_with_lookback(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    monkeypatch.setattr(passport, "PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    encoded = passport.correspond_path(1_700_000_100_000)
    assert len(encoded) == 256
    decoded = key.decrypt(bytes.fromhex(encoded), padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    assert decoded == b"refresh_1700000080000"
    assert passport.correspond_path(1_700_000_100_000) != encoded


@pytest.mark.parametrize("code,status", [(86101, "pending"), (86090, "scanned"), (86038, "expired"), (0, "success")])
def test_qrcode_statuses_and_success_do_not_make_followup_network_calls(code, status):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url).split("?")[0] == passport.POLL
        assert request.url.params["qrcode_key"] == QR_KEY
        assert not request.headers.get("cookie")
        return success({"code": code, "refresh_token": NEW_TOKEN}, headers=headers() if code == 0 else [])
    with client(handler) as protocol:
        result = protocol.poll_qrcode(QR_KEY, OLD_COOKIE)
    assert result.status == status
    assert len(calls) == 1
    assert (result.credential is not None) is (code == 0)
    if result.credential:
        assert "buvid3=device-three" in result.credential.cookie_text
        assert result.credential.refresh_token == NEW_TOKEN


def test_generate_and_info_validate_types_and_never_inherit_response_cookie_jar():
    calls = []
    def handler(request):
        calls.append(request)
        if str(request.url) == passport.GENERATE:
            assert not request.headers.get("cookie")
            return success({"url": QR_URL, "qrcode_key": QR_KEY}, headers=headers())
        assert request.url.path.endswith("/cookie/info")
        assert request.headers["cookie"] == OLD_COOKIE
        assert request.url.params["csrf"] == OLD_CSRF
        return success({"refresh": True, "timestamp": 1_700_000_000_000})
    with client(handler) as protocol:
        qr = protocol.generate_qrcode()
        assert qr.url == QR_URL and qr.qrcode_key == QR_KEY
        assert QR_KEY not in repr(qr)
        state = protocol.refresh_info(OLD_COOKIE)
        assert state.refresh is True and state.timestamp_ms == 1_700_000_000_000
        assert len(protocol.http.cookies) == 0


@pytest.mark.parametrize("url", [QR_URL, ACCOUNT_QR_URL, QR_URL + "&callback=close"])
def test_generate_accepts_both_exact_official_qr_destinations(url):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return success({"url": url, "qrcode_key": QR_KEY})
    with client(handler) as protocol:
        qr = protocol.generate_qrcode()
        assert qr.url == url and qr.qrcode_key == QR_KEY
        assert QR_KEY not in repr(qr)
    assert calls == [passport.GENERATE], "QR destination must be returned, never followed by the server"


@pytest.mark.parametrize("url", [
    ACCOUNT_QR_URL.replace("https:", "http:"),
    ACCOUNT_QR_URL.replace("account.bilibili.com", "account.bilibili.com.evil.invalid"),
    ACCOUNT_QR_URL.replace("account.bilibili.com", "account.bilibili.com:443"),
    ACCOUNT_QR_URL.replace("account.bilibili.com", "user@account.bilibili.com"),
    ACCOUNT_QR_URL.replace("account.bilibili.com", "account.bilibili.com@evil.invalid"),
    ACCOUNT_QR_URL.replace("account.bilibili.com", "passport.bilibili.com"),
    ACCOUNT_QR_URL.replace("/auth/scan-web", "/auth/other"),
    ACCOUNT_QR_URL.replace("/auth/scan-web", "/auth/scan-web/"),
    ACCOUNT_QR_URL.replace("callback=close", "callback=https%3A%2F%2Fevil.invalid"),
    ACCOUNT_QR_URL.replace("callback=close&", ""),
    ACCOUNT_QR_URL.replace("callback=close", "callback="),
    ACCOUNT_QR_URL + "&callback=close",
    ACCOUNT_QR_URL + "&qrcode_key=" + QR_KEY,
    ACCOUNT_QR_URL.replace(QR_KEY, "different-key"),
    ACCOUNT_QR_URL.replace("&qrcode_key=" + QR_KEY, ""),
    ACCOUNT_QR_URL + "#callback=close",
    ACCOUNT_QR_URL + "\x7f",
    " " + ACCOUNT_QR_URL,
    QR_URL.replace(QR_KEY, "different-key"),
    QR_URL + "&callback=https%3A%2F%2Fevil.invalid",
])
def test_generate_rejects_wrong_destination_callback_or_mismatched_poll_key(url):
    with client(lambda _: success({"url": url, "qrcode_key": QR_KEY})) as protocol, pytest.raises(IngestError) as error:
        protocol.generate_qrcode()
    assert QR_KEY not in str(error.value)


@pytest.mark.parametrize("data", [None, {}, {"refresh": 1}, {"refresh": "true"},
    {"refresh": True, "timestamp": True}, {"refresh": True, "timestamp": "1700000000000"},
    {"refresh": True, "timestamp": -1}])
def test_bad_refresh_info_is_not_treated_as_no_refresh(data):
    with client(lambda _: success(data)) as protocol, pytest.raises(IngestError):
        protocol.refresh_info(OLD_COOKIE)


@pytest.mark.parametrize("bad", [None, "", "with space", "x\r\nCookie:secret", "x\tbad", "x\x00bad", "x;bad", "x" * 4097, "中文"])
def test_refresh_token_validation_rejects_unsafe_values_without_echo(bad):
    with pytest.raises(IngestError) as caught:
        passport.validate_refresh_token(bad)
    assert caught.value.code == "invalid_refresh_token"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("mutate", [
    lambda c: c.replace("old-sessdata%2Cepoch", ""),
    lambda c: c.replace(OLD_CSRF, "not-hex"),
    lambda c: c.replace("DedeUserID=123", "DedeUserID=0"),
    lambda c: c + "\r\nX-Evil: secret",
    lambda c: c + "; SESSDATA=other-value",
    lambda c: c + "\ud800",
])
def test_invalid_core_cookies_never_reach_network(mutate):
    with client(lambda _: pytest.fail("unexpected network")) as protocol, pytest.raises(IngestError):
        protocol.refresh_info(mutate(OLD_COOKIE))


@pytest.mark.parametrize("cookie_headers,data", [
    (headers(omit=("SESSDATA",)), {"refresh_token": NEW_TOKEN}),
    (headers(uid="456"), {"refresh_token": NEW_TOKEN}),
    (headers(), {}),
    (headers(), {"refresh_token": "bad\nvalue"}),
    (headers(extra=[("set-cookie", f"bili_jct={OLD_CSRF}; Domain=.bilibili.com")]), {"refresh_token": NEW_TOKEN}),
    (headers(extra=[("set-cookie", "evil=value; Domain=example.org")]), {"refresh_token": NEW_TOKEN}),
])
def test_rotated_credential_requires_complete_new_fields_and_same_account(cookie_headers, data):
    with client(lambda request: csrf_page() if request.url.path.startswith("/correspond/")
                else success(data, headers=cookie_headers)) as protocol, pytest.raises(IngestError) as caught:
        protocol.refresh(OLD_COOKIE, OLD_TOKEN)
    assert caught.value.mutation_started is True and caught.value.mutation_uncertain is True


@pytest.mark.parametrize("response,code,uncertain", [
    (httpx.Response(503, text="SESSDATA=never-log"), "http_error", True),
    (httpx.Response(200, text="not-json private-token"), "invalid_response", True),
    (httpx.Response(200, json={"code": -101, "message": "private-token"}), "login_required", False),
    (httpx.Response(200, json={"code": 86095, "message": "private-token"}), "passport_rejected", False),
])
def test_refresh_post_error_has_safe_outcome_metadata(response, code, uncertain):
    with client(lambda request: csrf_page() if request.url.path.startswith("/correspond/") else response) as protocol:
        with pytest.raises(IngestError) as caught:
            protocol.refresh(OLD_COOKIE, OLD_TOKEN)
    assert caught.value.code == code and caught.value.mutation_started is True
    assert caught.value.mutation_uncertain is uncertain
    assert "private-token" not in str(caught.value) and "never-log" not in str(caught.value)


def test_network_failure_is_redacted_and_rotation_is_not_retried():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.startswith("/correspond/"):
            return csrf_page()
        raise httpx.ReadTimeout(f"private-token at {request.url}", request=request)
    with client(handler) as protocol, pytest.raises(IngestError) as caught:
        protocol.refresh(OLD_COOKIE, OLD_TOKEN)
    assert caught.value.code == "network_error" and caught.value.mutation_uncertain is True
    assert "private-token" not in str(caught.value)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_never_followed(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://attacker.invalid/steal"})
    with client(handler) as protocol, pytest.raises(IngestError) as caught:
        protocol.refresh_info(OLD_COOKIE)
    assert caught.value.code == "passport_redirect" and len(calls) == 1


def test_response_stream_size_and_encoding_are_bounded(monkeypatch):
    class Stream(httpx.SyncByteStream):
        consumed = 0
        closed = False
        def __iter__(self):
            for _ in range(50):
                self.consumed += 1
                yield b"x" * 16384
        def close(self):
            self.closed = True
    stream = Stream()
    with client(lambda _: httpx.Response(200, stream=stream)) as protocol, pytest.raises(IngestError) as caught:
        protocol.refresh_info(OLD_COOKIE)
    assert caught.value.code == "response_too_large"
    assert stream.consumed == 5 and stream.closed
    with client(lambda _: httpx.Response(200, headers={"content-length": "999999999"}, stream=Stream())) as protocol:
        with pytest.raises(IngestError) as caught:
            protocol.refresh_info(OLD_COOKIE)
        assert caught.value.code == "response_too_large"
    with client(lambda _: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Stream())) as protocol:
        with pytest.raises(IngestError) as caught:
            protocol.refresh_info(OLD_COOKIE)
        assert caught.value.code == "invalid_response"


def test_slow_trickle_cannot_bypass_whole_request_deadline(monkeypatch):
    ticks = iter([0, 10, 20, 30])
    monkeypatch.setattr(passport.time, "monotonic", lambda: next(ticks))
    class Trickle(httpx.SyncByteStream):
        reads = 0
        closed = False
        def __iter__(self):
            for _ in range(100):
                self.reads += 1
                yield b" "
        def close(self):
            self.closed = True
    stream = Trickle()
    with client(lambda _: httpx.Response(200, stream=stream)) as protocol, pytest.raises(IngestError) as caught:
        protocol.refresh_info(OLD_COOKIE)
    assert caught.value.code == "network_error" and stream.reads == 3 and stream.closed


def test_debug_logs_never_expose_cookie_qr_key_body_or_correspond_path(caplog):
    caplog.set_level(logging.DEBUG)
    def handler(request):
        logging.getLogger("httpcore.http11").debug("headers=%s body=%s", list(request.headers.items()), request.content)
        if request.url.path.startswith("/correspond/"):
            return csrf_page()
        if request.url.path.endswith("/poll"):
            return success({"code": 86101})
        return success({"refresh_token": NEW_TOKEN}, headers=headers())
    with client(handler) as protocol:
        protocol.poll_qrcode(QR_KEY)
        credential = protocol.refresh(OLD_COOKIE, OLD_TOKEN)
    logging.getLogger("httpx").info("unrelated request remains visible")
    assert "unrelated request remains visible" in caplog.text
    for secret in (OLD_CSRF, OLD_TOKEN, NEW_TOKEN, "old-sessdata", "new-sessdata", QR_KEY, "/correspond/1/"):
        assert secret not in caplog.text
    assert credential.cookie_text not in repr(credential)


def test_fixed_url_guard_rejects_non_official_destinations():
    with client(lambda _: pytest.fail("unexpected network")) as protocol, pytest.raises(IngestError):
        protocol._request("POST", "https://passport.bilibili.com.attacker.invalid/x", cookies={"SESSDATA": "private"})


def test_qr_success_with_no_existing_cookie_is_returned_without_optional_buvid_io():
    calls = []
    def handler(request):
        calls.append(request)
        return success({"code": 0, "refresh_token": NEW_TOKEN}, headers=headers())
    with client(handler) as protocol:
        result = protocol.poll_qrcode(QR_KEY)
    assert result.status == "success" and result.credential.uid == "123" and len(calls) == 1


def test_netscape_cookie_import_keeps_only_passport_root_eligible_values():
    text = "\n".join(["# Netscape HTTP Cookie File", *[
        f".bilibili.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}" for name, value in
        [("SESSDATA", "old-sessdata%2Cepoch"), ("bili_jct", OLD_CSRF), ("DedeUserID", "123"), ("buvid3", "device-three")]],
        "other.invalid\tFALSE\t/\tFALSE\t0\tbad\tdont-send",
        ".bilibili.com\tTRUE\t/other\tTRUE\t0\tpath_specific\tdont-send",
    ])
    def handler(request):
        assert "device-three" in request.headers["cookie"]
        assert "dont-send" not in request.headers["cookie"]
        return success({"refresh": False})
    with client(handler) as protocol:
        assert protocol.refresh_info(text).refresh is False


@pytest.mark.parametrize("url", ["http://passport.bilibili.com/h5-app/passport/login/scan", "https://evil.invalid/scan",
    "https://passport.bilibili.com@evil.invalid/scan", "https://passport.bilibili.com:443/h5-app/passport/login/scan"])
def test_qr_generation_rejects_unexpected_url(url):
    with client(lambda _: success({"url": url, "qrcode_key": QR_KEY})) as protocol, pytest.raises(IngestError):
        protocol.generate_qrcode()
