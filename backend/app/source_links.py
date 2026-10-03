"""Parse known Bilibili source links without requesting arbitrary remote URLs."""
import re
from urllib.parse import parse_qs, urlsplit


def resolve_source(value: str, kind: str = "favorite") -> dict:
    value = value.strip()
    if re.fullmatch(r"[1-9][0-9]{0,31}", value):
        return {"kind": kind, "source_id": value}
    try:
        url = urlsplit(value if "://" in value else "https://" + value)
        if url.scheme not in {"http", "https"} or url.username or url.password or url.port not in {None, 80, 443}:
            raise ValueError
        if url.hostname not in {"space.bilibili.com", "www.bilibili.com", "bilibili.com"}:
            raise ValueError
        query = parse_qs(url.query)
        favorite = query.get("fid", query.get("media_id", []))
        if len(favorite) == 1 and re.fullmatch(r"[1-9][0-9]{0,31}", favorite[0]):
            return {"kind": "favorite", "source_id": favorite[0]}
        if url.hostname == "space.bilibili.com":
            match = re.fullmatch(r"/([1-9][0-9]{0,31})(?:/(?:upload(?:/video)?|video))?/?", url.path)
            if match:
                return {"kind": "creator", "source_id": match[1]}
    except (ValueError, TypeError):
        pass
    raise ValueError("请填写 UP 主空间链接、带 fid 的收藏夹链接，或对应的数字 ID")
