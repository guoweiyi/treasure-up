"""Useful error locations without exception messages, query parameters or locals."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


_APP = Path(__file__).resolve().parents[1]
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")


def summarize(error):
    kind = type(error).__name__
    kind = kind if _IDENTIFIER.fullmatch(kind) else "Exception"
    frames = []
    traceback = error.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        try:
            path = Path(code.co_filename).resolve().relative_to(_APP)
        except (ValueError, OSError):
            pass
        else:
            # Only trusted application code locations are useful to operators.
            if path.suffix == ".py" and all(_IDENTIFIER.fullmatch(part) for part in path.with_suffix("").parts):
                function = code.co_name
                frames.append({"module": "app." + ".".join(path.with_suffix("").parts),
                               "function": function if _IDENTIFIER.fullmatch(function) else "unknown",
                               "line": traceback.tb_lineno})
                frames = frames[-8:]
        traceback = traceback.tb_next
    details = {"type": kind, "frames": frames}
    details["fingerprint"] = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()[:16]
    return details


# Paths are application endpoints, never user-controlled IDs or resource names.
_SOURCE_PATHS = frozenset({
    "/x/web-interface/nav", "/x/web-interface/view", "/x/web-interface/view/detail",
    "/x/tag/archive/tags", "/x/v3/fav/resource/list", "/x/space/wbi/arc/search",
    "/x/v2/reply/wbi/main", "/x/v2/reply/reply", "/x/space/wbi/acc/info",
    "/x/player/wbi/v2", "/x/player/v2", "/x/player/wbi/playurl", "/x/player/playurl",
    "/x/v2/dm/wbi/web/seg.so", "/x/web-frontend/getbuvid",
    "/x/passport-login/web/qrcode/generate", "/x/passport-login/web/qrcode/poll",
    "/x/passport-login/web/cookie/info", "/x/passport-login/web/cookie/refresh",
    "/x/passport-login/web/confirm/refresh",
})
_SOURCE_LABELS = frozenset({"bilibili_api", "bilibili_page", "media_cdn", "image_cdn", "source_resource", "unknown"})


def canonical_source_endpoint(value):
    """Classify a source location without retaining queries or dynamic paths."""
    if not isinstance(value, str) or not value:
        return "unknown"
    if value in _SOURCE_LABELS:
        return value
    try:
        parsed = urlsplit(value)
        host, path = (parsed.hostname or "").lower(), parsed.path
    except (ValueError, UnicodeError):
        return "unknown"
    if path in _SOURCE_PATHS and host in {"", "api.bilibili.com", "passport.bilibili.com"}:
        return path
    if host == "api.bilibili.com":
        return "bilibili_api"
    if host == "bilibili.com" or host.endswith(".bilibili.com"):
        return "bilibili_page"
    if any(host == domain or host.endswith("." + domain) for domain in ("bilivideo.com", "bilivideo.cn")):
        return "media_cdn"
    if host == "hdslb.com" or host.endswith(".hdslb.com"):
        return "image_cdn"
    return "source_resource"


def source_rejection(error, when):
    """Describe an actual rejection; waiting for someone else's cooldown is not one."""
    if getattr(error, "code", None) != "rate_limited" or getattr(error, "deferred", False):
        return None
    details = {"occurred_at": when.isoformat(),
               "endpoint": canonical_source_endpoint(getattr(error, "source_endpoint", None))}
    status = getattr(error, "http_status", None)
    if type(status) is int and status in {403, 412, 429}:
        details["http_status"] = status
    source_code = getattr(error, "source_code", None)
    if type(source_code) is int and -(2 ** 31) <= source_code < 2 ** 31:
        details["source_code"] = source_code
    return details
