import hashlib
import json
import re
import time
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

from .errors import IngestError

API = "https://api.bilibili.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
MIXIN = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]


@dataclass(frozen=True)
class Cookie:
    domain: str
    path: str
    secure: bool
    expires: int
    name: str
    value: str
    include_subdomains: bool = True


def parse_cookies(text, now=None):
    """Accept a user-supplied Cookie header or Netscape export, never a file path."""
    now = int(time.time()) if now is None else now
    result = []
    if "\t" in text or "Netscape HTTP Cookie File" in text:
        for line in text.splitlines():
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) != 7:
                raise IngestError("Cookie 导出格式无效", code="invalid_cookie", retryable=False)
            domain, subs, path, secure, expiry, name, value = fields
            host = domain.lstrip(".").lower()
            if host != "bilibili.com" and not host.endswith(".bilibili.com"):
                continue
            try:
                expiry = int(expiry)
            except ValueError:
                raise IngestError("Cookie 有效期格式无效", code="invalid_cookie", retryable=False) from None
            if expiry and expiry <= now:
                continue
            result.append(Cookie(domain, path or "/", secure.upper() == "TRUE", expiry, name, value, subs.upper() == "TRUE"))
    else:
        if any(char in text for char in "\r\n\x00"):
            raise IngestError("Cookie 请求头格式无效", code="invalid_cookie", retryable=False)
        parsed = SimpleCookie()
        try:
            parsed.load(text)
        except Exception:
            raise IngestError("Cookie 请求头格式无效", code="invalid_cookie", retryable=False) from None
        result = [Cookie(".bilibili.com", "/", True, 0, k, v.value) for k, v in parsed.items()]
    if not result:
        raise IngestError("没有可用的 B站 Cookie", code="invalid_cookie", retryable=False)
    for c in result:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", c.name) or any(ch in c.value for ch in "\r\n\x00\t;"):
            raise IngestError("Cookie 字段格式无效", code="invalid_cookie", retryable=False)
    return result


def cookie_header(cookies, url):
    target = urlsplit(url)
    values = []
    for c in sorted(cookies, key=lambda item: -len(item.path)):
        domain = c.domain.lstrip(".").lower()
        host = target.hostname or ""
        path = target.path or "/"
        if host != domain and not (c.include_subdomains and host.endswith("." + domain)):
            continue
        if not (path == c.path or path.startswith(c.path.rstrip("/") + "/")):
            continue
        if c.secure and target.scheme != "https":
            continue
        if c.expires and c.expires <= time.time():
            continue
        values.append(f"{c.name}={c.value}")
    return "; ".join(values)


def sign_wbi(params, images, now=None):
    try:
        joined = "".join(urlsplit(images[key]).path.rsplit("/", 1)[-1].split(".")[0] for key in ("img_url", "sub_url"))
        if len(joined) < 64:
            raise ValueError()
        key = "".join(joined[index] for index in MIXIN)[:32]
    except (KeyError, IndexError, TypeError, ValueError):
        raise IngestError("无法取得有效 WBI 签名参数", code="wbi_unavailable") from None
    clean = {**params, "wts": int(time.time() if now is None else now)}
    clean = {key: re.sub(r"[!'()*]", "", str(value)) for key, value in sorted(clean.items()) if key != "w_rid"}
    clean["w_rid"] = hashlib.md5((urlencode(clean, quote_via=quote) + key).encode()).hexdigest()
    return clean


def safe_source_url(url):
    """Drop query, fragment and userinfo from any persisted source reference."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


def clean_raw(value):
    """Only retain public data; transient URL query strings are never persisted."""
    forbidden = {"cookie", "cookies", "sessdata", "bili_jct", "access_key", "access_token", "refresh_token", "w_rid", "wts", "token"}
    if isinstance(value, dict):
        return {str(k): clean_raw(v) for k, v in value.items() if str(k).lower() not in forbidden}
    if isinstance(value, list):
        return [clean_raw(v) for v in value]
    if isinstance(value, str) and (value.startswith(("http://", "https://", "//"))):
        return safe_source_url("https:" + value if value.startswith("//") else value)
    return value


def retry_after(value, now=None):
    """HTTP Retry-After accepts either delay seconds or an HTTP date."""
    if not value:
        return None
    value = str(value).strip()
    if re.fullmatch(r"\d{1,9}", value):
        return int(value)
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((parsed - (now or datetime.now(timezone.utc))).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class BiliClient:
    def __init__(self, cookie_text="", *, interval=0, transport=None, sleep=time.sleep):
        self.cookies = parse_cookies(cookie_text) if cookie_text else []
        self.interval = max(0, interval)
        self.sleep = sleep
        self.last_request = 0
        self.images = None
        self.images_at = 0
        self.before_request = None
        self.before_video = None
        self.request_context = lambda url: nullcontext()
        self.last_asset_request = 0
        self.asset_interval = 0.1
        self.http = httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False, transport=transport, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})

    def close(self):
        self.http.close()

    def _request(self, url, *, params=None, limit=16 * 1024 * 1024, authenticated=False):
        with self.request_context(url):
            return self._request_in_context(url, params=params, limit=limit, authenticated=authenticated)

    def _request_in_context(self, url, *, params=None, limit=16 * 1024 * 1024, authenticated=False):
        if self.before_request:
            self.before_request(url)
        # Public CDN assets carry no Cookie and do not consume the account API
        # interval. Keep their serial requests gently paced without per-image
        # multi-second account jitter.
        asset_request = (urlsplit(url).hostname or "").endswith(".hdslb.com")
        delay = (self.asset_interval if asset_request else self.interval) - (time.monotonic() - (self.last_asset_request if asset_request else self.last_request))
        if delay > 0:
            self.sleep(delay)
        if asset_request:
            self.last_asset_request = time.monotonic()
        else:
            self.last_request = time.monotonic()
        headers = {"Cookie": cookie_header(self.cookies, url)} if authenticated and self.cookies else {}
        try:
            with self.http.stream("GET", url, params=params, headers=headers) as response:
                if response.status_code in (403, 412, 429):
                    raise IngestError("源站限流或风控，请稍后恢复任务", code="rate_limited", retry_after_seconds=retry_after(response.headers.get("retry-after")))
                if asset_request and not authenticated and response.status_code in (404, 410):
                    raise IngestError("源站图片已不存在", code="asset_not_found", retryable=False)
                if response.status_code != 200:
                    raise IngestError(f"源站 HTTP 请求失败（{response.status_code}）", code="http_error")
                output = bytearray()
                for chunk in response.iter_bytes():
                    output.extend(chunk)
                    if len(output) > limit:
                        raise IngestError("源站响应超过允许大小", code="response_too_large", retryable=False)
                return bytes(output), response.headers.get("content-type", "").split(";")[0]
        except httpx.HTTPError:
            raise IngestError("源站连接失败或超时", code="network_error") from None

    def json(self, path, params=None, *, wbi=False, allow_anonymous_nav=False):
        if not path.startswith("/x/") or "?" in path or ".." in path:
            raise IngestError("不允许的接口路径", code="invalid_request", retryable=False)
        if wbi:
            if self.images is None or time.monotonic() - self.images_at > 30:
                self.nav(allow_anonymous=True)
            params = sign_wbi(params or {}, self.images)
        body, mime = self._request(API + path, params=params, authenticated=True)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise IngestError("源站返回非 JSON 数据，可能需要重新验证账号", code="invalid_response") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("code"), int):
            raise IngestError("源站返回结构异常", code="invalid_response")
        code = payload["code"]
        data = payload.get("data")
        if allow_anonymous_nav and code == -101 and isinstance(data, dict) and data.get("wbi_img"):
            return data
        if code:
            names = {-101: ("账号登录已失效", "login_required"), -352: ("源站风控，请稍后恢复", "rate_limited"), -401: ("源站认证检查未通过", "rate_limited"), -403: ("源站拒绝请求或签名失效", "access_denied"), 12002: ("评论区已关闭", "comments_closed"), -404: ("来源内容不可访问", "not_found")}
            message, category = names.get(code, (f"源站接口失败（代码 {code}）", "api_error"))
            raise IngestError(message, code=category, retryable=code not in (-101, -404, 12002))
        if data is None:
            raise IngestError("源站缺少数据对象", code="invalid_response")
        if isinstance(data, dict) and data.get("v_voucher"):
            raise IngestError("源站要求验证，请暂停并检查账号", code="rate_limited")
        return data

    def nav(self, allow_anonymous=False):
        data = self.json("/x/web-interface/nav", allow_anonymous_nav=allow_anonymous)
        if data.get("wbi_img"):
            self.images, self.images_at = data["wbi_img"], time.monotonic()
        if not allow_anonymous and not data.get("isLogin"):
            raise IngestError("账号登录已失效", code="login_required", retryable=False)
        return data

    def view(self, bvid):
        if not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid or ""):
            raise IngestError("视频 BV 号无效", code="invalid_video", retryable=False)
        return self.json("/x/web-interface/view", {"bvid": bvid})

    def tags(self, bvid):
        if not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid or ""):
            raise IngestError("视频 BV 号无效", code="invalid_video", retryable=False)
        return self.json("/x/tag/archive/tags", {"bvid": bvid})

    def favorite_page(self, source_id, page):
        return self.json("/x/v3/fav/resource/list", {"media_id": source_id, "pn": page, "ps": 20, "order": "mtime", "type": 0, "tid": 0})

    def creator_page(self, uid, page):
        return self.json("/x/space/wbi/arc/search", {"mid": uid, "pn": page, "ps": 30, "order": "pubdate",
            "order_avoided": "true", "platform": "web", "web_location": "1550101"}, wbi=True)

    def comment_page(self, aid, offset=""):
        return self.json("/x/v2/reply/wbi/main", {"oid": aid, "type": 1, "mode": 3, "pagination_str": json.dumps({"offset": offset}, separators=(",", ":"))}, wbi=True)

    def replies_page(self, aid, root, page):
        return self.json("/x/v2/reply/reply", {"oid": aid, "type": 1, "root": root, "pn": page, "ps": 20})

    def profile(self, uid):
        return self.json("/x/space/wbi/acc/info", {"mid": uid}, wbi=True)

    def player(self, aid, cid):
        return self.json("/x/player/wbi/v2", {"aid": aid, "cid": cid}, wbi=True)

    def playurl(self, bvid, cid):
        return self.json("/x/player/wbi/playurl", {"bvid": bvid, "cid": cid,
            "qn": 127, "fnval": 4048, "fnver": 0, "fourk": 1}, wbi=True)

    def segment(self, aid, cid, index):
        if self.images is None or time.monotonic() - self.images_at > 30:
            self.nav(allow_anonymous=True)
        params = sign_wbi({"type": 1, "pid": aid, "oid": cid, "segment_index": index}, self.images)
        body, mime = self._request(API + "/x/v2/dm/wbi/web/seg.so", params=params, limit=32 * 1024 * 1024, authenticated=True)
        if mime == "text/html":
            raise IngestError("弹幕接口返回了错误页面", code="invalid_danmaku")
        # Protobuf's tag 0x0a is ASCII newline and a valid following length may
        # be 0x7b ('{'). Whitespace sniffing must never classify those bytes as
        # JSON without actually decoding the complete response.
        if mime == "application/json" or body.lstrip().startswith((b"{", b"[")):
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                if mime == "application/json":
                    raise IngestError("弹幕接口返回无效 JSON", code="invalid_danmaku") from None
            else:
                code = payload.get("code") if isinstance(payload, dict) else None
                if code in (-352, -401, -403):
                    raise IngestError("弹幕源站风控，请稍后恢复", code="rate_limited")
                if code == -101:
                    raise IngestError("账号登录已失效", code="login_required", retryable=False)
                raise IngestError("弹幕接口返回 JSON 错误响应", code="invalid_danmaku")
        return body

    def asset(self, url, *, limit=16 * 1024 * 1024):
        if url.startswith("//"):
            url = "https:" + url
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        if p.scheme not in ("https", "http") or p.username or p.password or p.port not in (None, 80, 443) or not (host == "hdslb.com" or host.endswith(".hdslb.com")):
            raise IngestError("图片或字幕来源域名不受支持", code="unsupported_asset", retryable=False)
        # Bilibili public CDN supports HTTPS. Do not forward account cookies.
        return self._request(urlunsplit(("https", host, p.path, p.query, "")), limit=limit)
