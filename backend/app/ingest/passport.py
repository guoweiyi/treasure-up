"""Bounded Bilibili web passport protocol; persistence/rotation policy lives above.

Refresh deliberately does NOT confirm the old refresh token. The caller must
durably save the returned credential before confirm_refresh, and must not blindly
retry a refresh POST whose outcome is unknown. No real credentials are needed by
the synthetic protocol tests.
"""
import contextvars
import json
import logging
import re
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .client import UA, cookie_header, parse_cookies, retry_after
from .errors import IngestError

PASSPORT = "https://passport.bilibili.com"
GENERATE = PASSPORT + "/x/passport-login/web/qrcode/generate"
POLL = PASSPORT + "/x/passport-login/web/qrcode/poll"
INFO = PASSPORT + "/x/passport-login/web/cookie/info"
REFRESH = PASSPORT + "/x/passport-login/web/cookie/refresh"
CONFIRM = PASSPORT + "/x/passport-login/web/confirm/refresh"
BUVID = "https://api.bilibili.com/x/web-frontend/getbuvid"
CORRESPOND = "https://www.bilibili.com/correspond/1/"
MAX_JSON_BYTES = 64 * 1024
MAX_HTML_BYTES = 256 * 1024
MAX_COOKIE_BYTES = 32 * 1024
MAX_REQUEST_SECONDS = 25
PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""
_CORE = {"SESSDATA", "bili_jct", "DedeUserID"}
_private_io = contextvars.ContextVar("bili_passport_private_io", default=False)


class _PrivateProtocolLogFilter(logging.Filter):
    def filter(self, record):
        # httpx INFO includes the QR query; httpcore DEBUG includes Set-Cookie.
        # Context-local suppression avoids hiding concurrent non-passport logs.
        return not _private_io.get()


_log_filter = _PrivateProtocolLogFilter()
for _logger in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
                "httpcore.proxy", "httpcore.socks", "httpcore.connection_pool"):
    logging.getLogger(_logger).addFilter(_log_filter)


def _error(message="B站登录接口返回结构异常", *, code="invalid_response", retryable=False):
    return IngestError(message, code=code, retryable=retryable)


def validate_refresh_token(value):
    """Opaque token: bounded safe ASCII, no whitespace/control/header delimiters."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,4096}", value):
        raise _error("B站刷新令牌格式无效", code="invalid_refresh_token")
    return value


def _validate_core(values, *, response=False):
    valid = (isinstance(values.get("SESSDATA"), str)
             and re.fullmatch(r"[A-Za-z0-9%._~+/*=,-]{6,4096}", values["SESSDATA"])
             and isinstance(values.get("bili_jct"), str)
             and re.fullmatch(r"[a-fA-F0-9]{32}", values["bili_jct"])
             and isinstance(values.get("DedeUserID"), str)
             and re.fullmatch(r"[1-9][0-9]{0,19}", values["DedeUserID"]))
    if not valid:
        raise _error("B站登录凭据缺少有效的核心 Cookie", code="invalid_response" if response else "invalid_cookie")


def _cookies(text, *, required=True):
    if not isinstance(text, str) or len(text) > MAX_COOKIE_BYTES:
        raise _error("Cookie 超过允许大小", code="invalid_cookie")
    try:
        if len(text.encode("utf-8")) > MAX_COOKIE_BYTES:
            raise _error("Cookie 超过允许大小", code="invalid_cookie")
    except UnicodeError:
        raise _error("Cookie 格式无效", code="invalid_cookie") from None
    if not text:
        if required:
            raise _error("请先提供 B站 Cookie", code="invalid_cookie")
        return {}
    # A header is the stored contract. Netscape imports retain only cookies
    # applicable to the official passport root; unrelated domains/paths stay out.
    if "\t" not in text:
        names = [part.partition("=")[0].strip() for part in text.split(";") if "=" in part]
        if len(names) != len(set(names)):
            raise _error("Cookie 包含重复字段", code="invalid_cookie")
    cookies = parse_cookies(text)
    header = cookie_header(cookies, PASSPORT + "/")
    parsed = SimpleCookie()
    try:
        parsed.load(header)
    except Exception:
        raise _error("Cookie 格式无效", code="invalid_cookie") from None
    values = {name: item.value for name, item in parsed.items()}
    if len(values) != len(header.split(";")) or any(
        not re.fullmatch(r"[A-Za-z0-9_-]+", name)
        or not value.isascii() or any(ord(c) < 0x21 or ord(c) > 0x7e or c in ';"\\' for c in value)
        for name, value in values.items()
    ):
        raise _error("Cookie 格式无效", code="invalid_cookie")
    if required:
        _validate_core(values)
    return values


def _header(values):
    return "; ".join(f"{name}={value}" for name, value in values.items())


@dataclass(frozen=True)
class Credential:
    cookie_text: str = field(repr=False)
    refresh_token: str = field(repr=False)
    uid: str
    bili_jct: str = field(repr=False)


@dataclass(frozen=True)
class QrCode:
    url: str = field(repr=False)
    qrcode_key: str = field(repr=False)


@dataclass(frozen=True)
class QrPoll:
    status: str
    credential: Credential | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RefreshInfo:
    refresh: bool
    timestamp_ms: int | None = None


def correspond_path(timestamp_ms):
    if type(timestamp_ms) is not int or not 10**11 <= timestamp_ms < 10**15:
        raise _error("B站刷新时间戳无效", code="invalid_request")
    # Server time is preferable; the small look-back also tolerates clock skew.
    payload = f"refresh_{timestamp_ms - 20_000}".encode("ascii")
    key = serialization.load_pem_public_key(PUBLIC_KEY)
    return key.encrypt(payload, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                            algorithm=hashes.SHA256(), label=None)).hex()


class BiliPassport:
    """One short-lived protocol client per operation/account; no shared cookie jar."""
    def __init__(self, *, transport=None, clock=time.time):
        self.clock = clock
        self.http = httpx.Client(timeout=httpx.Timeout(10, connect=5, pool=5),
            follow_redirects=False, trust_env=False, transport=transport,
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Accept-Encoding": "identity"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.http.close()

    def _request(self, method, url, *, cookies=None, params=None, data=None, html=False):
        fixed = url in {GENERATE, POLL, INFO, REFRESH, CONFIRM, BUVID}
        if not fixed and not re.fullmatch(re.escape(CORRESPOND) + r"[a-f0-9]{256}", url):
            raise _error("不允许的 B站登录接口", code="invalid_request")
        token = _private_io.set(True)
        limit = MAX_HTML_BYTES if html else MAX_JSON_BYTES
        started = time.monotonic()
        try:
            # Explicit Cookie, including an empty value, overrides any cookies
            # httpx learned from earlier calls on this instance.
            with self.http.stream(method, url, params=params, data=data,
                                  headers={"Cookie": _header(cookies or {})}) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise _error("B站登录接口发生重定向，已停止请求", code="passport_redirect")
                if status in (403, 412, 429):
                    raise IngestError("B站登录接口暂时限流，请稍后重试", code="rate_limited",
                                      retry_after_seconds=retry_after(response.headers.get("retry-after")))
                if status != 200:
                    raise _error(f"B站登录接口 HTTP {status}", code="http_error", retryable=status >= 500)
                if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                    raise _error("B站登录接口未返回请求的未压缩响应", code="invalid_response")
                raw_size = response.headers.get("content-length", "")
                if raw_size.isdigit() and int(raw_size) > limit:
                    raise _error("B站登录响应超过允许大小", code="response_too_large")
                output = bytearray()
                # Do not request an aggregate chunk size: a slow trickle must
                # reach the wall-clock budget check on every network read.
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > MAX_REQUEST_SECONDS:
                        raise _error("B站登录接口读取超时", code="network_error", retryable=True)
                    if len(output) + len(chunk) > limit:
                        raise _error("B站登录响应超过允许大小", code="response_too_large")
                    output.extend(chunk)
                if time.monotonic() - started > MAX_REQUEST_SECONDS:
                    raise _error("B站登录接口读取超时", code="network_error", retryable=True)
                return bytes(output), response.headers.get_list("set-cookie")
        except httpx.HTTPError:
            raise _error("B站登录接口连接失败或超时", code="network_error", retryable=True) from None
        finally:
            self.http.cookies.clear()
            _private_io.reset(token)

    def _json(self, method, url, **kwargs):
        raw, headers = self._request(method, url, **kwargs)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise _error() from None
        if not isinstance(value, dict) or type(value.get("code")) is not int:
            raise _error()
        code = value["code"]
        if code:
            if code == -101:
                error = _error("B站登录已失效，请重新登录", code="login_required")
            elif code in (-352, -401, -412, -429):
                error = _error("B站登录接口要求验证或稍后重试", code="rate_limited", retryable=True)
            else:
                error = _error(f"B站登录接口拒绝请求（代码 {code}）", code="passport_rejected")
            error.mutation_uncertain = False
            error.source_code = code
            error.source_endpoint = urlsplit(url).path
            raise error
        return value.get("data"), headers

    def _credential(self, data, headers, previous):
        if not isinstance(data, dict):
            raise _error()
        new_values = {}
        for raw in headers:
            if len(raw) > MAX_COOKIE_BYTES or any(c in raw for c in "\r\n\x00"):
                raise _error()
            parsed = SimpleCookie()
            names = [part.partition("=")[0].strip() for part in raw.split(";") if "=" in part]
            if any(names.count(name) > 1 for name in _CORE):
                raise _error("B站登录响应包含重复 Cookie")
            try:
                parsed.load(raw)
            except Exception:
                raise _error() from None
            for name, item in parsed.items():
                domain = item["domain"].lstrip(".").lower()
                if domain and domain != "bilibili.com":
                    raise _error("B站登录响应 Cookie 作用域异常")
                if item["path"] not in ("", "/"):
                    raise _error("B站登录响应 Cookie 路径异常")
                if name in new_values:
                    raise _error("B站登录响应包含重复 Cookie")
                new_values[name] = item.value
        _validate_core(new_values, response=True)
        refresh_token = validate_refresh_token(data.get("refresh_token"))
        # Never silently replace an existing account with another signed-in UID.
        if previous.get("DedeUserID") and previous["DedeUserID"] != new_values["DedeUserID"]:
            raise _error("B站刷新后的账号身份不匹配", code="passport_identity_mismatch")
        merged = {**previous, **new_values}
        cookie_text = _header(merged)
        _cookies(cookie_text)
        return Credential(cookie_text, refresh_token, merged["DedeUserID"], merged["bili_jct"])

    def generate_qrcode(self):
        data, _ = self._json("GET", GENERATE)
        if not isinstance(data, dict):
            raise _error()
        url, key = data.get("url"), data.get("qrcode_key")
        if not isinstance(url, str) or len(url) > 4096 or any(ord(c) <= 0x20 or ord(c) == 0x7f for c in url):
            raise _error()
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", key):
            raise _error("B站二维码标识无效")
        try:
            parsed = urlsplit(url)
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=16)
        except ValueError:
            raise _error("B站二维码地址无效") from None
        # The official generate endpoint now also returns the account H5 page.
        # Compare exact netloc/path pairs: no alternate ports, userinfo, or
        # arbitrary Bilibili pages. The QR and polling key must be one login.
        destinations = {
            ("passport.bilibili.com", "/h5-app/passport/login/scan"),
            ("account.bilibili.com", "/h5/account-h5/auth/scan-web"),
        }
        if (parsed.scheme != "https" or (parsed.netloc, parsed.path) not in destinations
                or parsed.fragment or query.get("qrcode_key") != [key]
                or any(len(values) != 1 for values in query.values())
                or ("callback" in query and query["callback"] != ["close"])
                or (parsed.netloc == "account.bilibili.com" and query.get("callback") != ["close"])):
            raise _error("B站二维码地址无效")
        return QrCode(url, key)

    def poll_qrcode(self, qrcode_key, cookie_text=""):
        if not isinstance(qrcode_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", qrcode_key):
            raise _error("B站二维码标识无效", code="invalid_request")
        previous = _cookies(cookie_text, required=False)
        data, headers = self._json("GET", POLL, params={"qrcode_key": qrcode_key})
        if not isinstance(data, dict) or type(data.get("code")) is not int:
            raise _error()
        statuses = {86101: "pending", 86090: "scanned", 86038: "expired"}
        if data["code"] in statuses:
            return QrPoll(statuses[data["code"]])
        if data["code"] != 0:
            raise _error("B站二维码登录状态异常", code="passport_rejected")
        # The successful poll has already consumed the QR. Do not risk losing
        # its credential by making another fallible network request afterwards.
        return QrPoll("success", self._credential(data, headers, previous))

    def refresh_info(self, cookie_text):
        cookies = _cookies(cookie_text)
        data, _ = self._json("GET", INFO, cookies=cookies, params={"csrf": cookies["bili_jct"]})
        if not isinstance(data, dict) or type(data.get("refresh")) is not bool:
            raise _error()
        timestamp = data.get("timestamp")
        if timestamp is not None and (type(timestamp) is not int or not 10**11 <= timestamp < 10**15):
            raise _error("B站刷新时间戳无效")
        return RefreshInfo(data["refresh"], timestamp)

    def refresh(self, cookie_text, refresh_token, *, timestamp_ms=None, before_rotate=None):
        cookies = _cookies(cookie_text)
        refresh_token = validate_refresh_token(refresh_token)
        path = correspond_path(int(self.clock() * 1000) if timestamp_ms is None else timestamp_ms)
        raw, _ = self._request("GET", CORRESPOND + path, cookies=cookies, html=True)
        try:
            document = raw.decode("utf-8")
        except UnicodeError:
            raise _error("B站刷新校验页面无效") from None
        matches = re.findall(r'<div\s+id=[\"\']1-name[\"\']\s*>\s*([a-fA-F0-9]{32})\s*</div>', document)
        if len(matches) != 1:
            raise _error("未取得 B站刷新校验值")
        if before_rotate is not None:
            before_rotate()
        try:
            data, headers = self._json("POST", REFRESH, cookies=cookies, data={
                "csrf": cookies["bili_jct"], "refresh_csrf": matches[0],
                "refresh_token": refresh_token, "source": "main_web"})
            return self._credential(data, headers, cookies)
        except IngestError as error:
            error.mutation_started = True
            if not hasattr(error, "mutation_uncertain"):
                error.mutation_uncertain = True
            raise

    def confirm_refresh(self, new_cookie_text, old_refresh_token):
        cookies = _cookies(new_cookie_text)
        self._json("POST", CONFIRM, cookies=cookies, data={
            "csrf": cookies["bili_jct"], "refresh_token": validate_refresh_token(old_refresh_token)})
