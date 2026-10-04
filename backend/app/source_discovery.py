"""Small, authenticated source pickers. Upstream credentials never leave this module."""
import hashlib
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingest.client import BiliClient
from app.ingest.errors import IngestDeferred, IngestError
from app.ingest.runner import account_request_scope, credential_lock, update_account_identity
from app.ingest.throttle import AccountPacer, utc
from app.models import Setting, SourceAccount, utcnow
from app.security import decrypt_secret, require_admin
from app.source_labels import source_title
from app.source_links import resolve_source

router = APIRouter(prefix="/api/v1/admin/source-discovery", tags=["source-discovery"])
CACHE_SECONDS = 300
_cache = OrderedDict()
_cache_lock = threading.Lock()


def _cached(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if entry[0] <= time.monotonic():
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return deepcopy(entry[1])


def _remember(key, value):
    with _cache_lock:
        _cache[key] = (time.monotonic() + CACHE_SECONDS, deepcopy(value))
        _cache.move_to_end(key)
        while len(_cache) > 128:
            _cache.popitem(last=False)


def _id(value):
    value = str(value or "")
    if not value.isascii() or not value.isdecimal() or len(value) > 32 or int(value) <= 0:
        raise IngestError("来源列表中的标识无效", code="invalid_response")
    return value


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000_000:
        raise IngestError("来源列表数量无效", code="invalid_response")
    return value


def _rows(data, maximum):
    if not isinstance(data, dict):
        raise IngestError("来源列表格式无效", code="invalid_response")
    rows = data.get("list")
    if rows is None and (data.get("count") == 0 or data.get("total") == 0):
        rows = []
    if not isinstance(rows, list) or len(rows) > maximum or any(not isinstance(row, dict) for row in rows):
        raise IngestError("来源列表内容无效", code="invalid_response")
    return rows


def _items(rows, category, nav):
    output, seen = [], set()
    for row in rows:
        creator = category == "following"
        identity = _id(row.get("mid") if creator else row.get("id"))
        if identity in seen:
            continue
        seen.add(identity)
        title = source_title(row.get("uname") if creator else row.get("title"))
        if not title:
            raise IngestError("来源列表缺少名称", code="invalid_response")
        item = {"kind": "creator" if creator else "favorite", "source_id": identity, "title": title}
        if not creator:
            owner = row.get("upper") or {}
            if not isinstance(owner, dict):
                raise IngestError("收藏夹作者信息无效", code="invalid_response")
            uid = owner.get("mid") or row.get("mid") or (nav["mid"] if category == "created" else None)
            item["owner_uid"] = _id(uid) if uid else None
            item["owner_name"] = source_title(owner.get("name") or (nav.get("uname") if category == "created" else ""))
            item["media_count"] = _count(row["media_count"]) if row.get("media_count") is not None else None
        output.append(item)
    return output


def _safe_error(error):
    if error.code == "account_changed":
        return HTTPException(429, "账号凭据已更新，请稍后重新加载来源", headers={
            "Retry-After": "1", "X-Source-Error": "account_changed"})
    if error.code in {"login_required", "invalid_cookie"}:
        return HTTPException(409, "B 站账号已失效或凭据不可用，请在账号管理中更新并验证")
    if isinstance(error, IngestDeferred) or error.code == "rate_limited":
        return HTTPException(429, "账号正在采集或冷却，请稍后加载来源", headers={
            "Retry-After": str(max(1, min(86400, int(error.retry_after_seconds or 30)))),
            "X-Source-Error": error.code if error.code in {"resource_busy", "account_cooldown", "rate_limited"} else "account_cooldown"})
    if error.code in {"not_found", "access_denied"}:
        return HTTPException(422, "该账号无法访问此来源，请检查链接和访问权限")
    return HTTPException(502, "B 站来源列表暂时不可用，请稍后重试")


def _account(db, account_id):
    account = db.get(SourceAccount, account_id)
    if not account:
        raise HTTPException(404, "采集账号不存在")
    if account.status in {"expired", "invalid", "disabled"}:
        raise HTTPException(409, "B 站账号已失效或停用，请在账号管理中更新并验证")
    return account


def _key(account, *parts):
    # Encryption ciphertext is hashed, not cached or exposed. Rotation invalidates pages.
    return (account.id, hashlib.sha256(account.secret_encrypted.encode()).hexdigest(), *parts)


def _changed_account():
    return IngestDeferred("账号凭据已更新，请重新验证来源", code="account_changed", retry_after_seconds=1)


def _response_cache_key(db, account, client, *parts):
    db.refresh(account, attribute_names=["secret_encrypted"])
    if _key(account) != client.discovery_generation:
        raise _changed_account()
    # Bind responses to the credential generation captured before HTTP. A
    # rotation after this check cannot place old data under a new cache key.
    return (*client.discovery_generation, *parts)


@contextmanager
def _client(db, account):
    client, pacer = None, None
    try:
        with credential_lock(db, account.id):
            db.refresh(account)
            _account(db, account.id)
            try:
                secret = decrypt_secret(account.secret_encrypted)
            except ValueError:
                raise IngestError("账号凭据不可用", code="invalid_cookie", retryable=False) from None
            initial_generation = _key(account)
        client = BiliClient(secret, interval=0)
        client.credential_generation = initial_generation
        # Ordinary discovery calls only share actual source rejection cooldown.
        # Long media transfers and legacy next_request_at do not block them.
        def guard():
            pass
        system = db.get(Setting, "ingest")
        policy = dict(system.value) if system and isinstance(system.value, dict) else {}
        pacer = AccountPacer(db, account, policy, guard, jitter=lambda _: 0)
        client.before_request = pacer.before_request
        generation, last_sent_generation = None, None
        @contextmanager
        def request_scope(url):
            nonlocal last_sent_generation
            with account_request_scope(db, account, client, pacer, url, decryptor=decrypt_secret):
                # Captured under the short credential mutex before HTTP starts.
                sent = client.credential_generation
                if generation is not None and sent != generation:
                    raise _changed_account()
                last_sent_generation = sent
                yield
        client.request_context = request_scope
        pacer.check_cooldown()
        try:
            identity_key = _key(account, "identity")
            nav = _cached(identity_key)
            if nav is None:
                verified = client.nav()
                nav = {"mid": _id(verified.get("mid")), "uname": source_title(verified.get("uname"))}
                generation = last_sent_generation or identity_key[:2]
                update_account_identity(db, account, generation, nav=nav)
                _remember((*generation, "identity"), nav)
            else:
                generation = identity_key[:2]
            client.discovery_generation = generation
            yield client, nav
        except IngestError as error:
            if error.code in {"login_required", "invalid_cookie"}:
                update_account_identity(db, account, client.credential_generation, invalid=True)
            elif not getattr(error, "account_backoff_applied", False):
                pacer.failed(error)
            raise
    except IngestError as error:
        raise _safe_error(error) from None
    finally:
        if client is not None:
            client.close()


def discover(db, account_id, category, page=1, *, refresh=False):
    account = _account(db, account_id)
    key = _key(account, category, 0 if category == "created" else page)
    data = None if refresh else _cached(key)
    cached = data is not None
    size = 24 if category == "following" else 20
    if data is None:
        with _client(db, account) as (client, nav):
            if category == "created":
                raw = client.json("/x/v3/fav/folder/created/list-all", {"up_mid": nav["mid"]})
                items = _items(_rows(raw, 1000), category, nav)
                data = {"items": items, "total": len(items)}
            else:
                params = {"pn": page, "ps": size}
                if category == "collected":
                    raw = client.json("/x/v3/fav/folder/collected/list", {**params, "up_mid": nav["mid"], "platform": "web"})
                else:
                    raw = client.json("/x/relation/followings", {**params, "vmid": nav["mid"],
                        "order": "desc", "order_type": "attention", "gaia_source": "main_web"})
                rows = _rows(raw, size)
                total = _count(raw["total"] if category == "following" else raw["count"]) if (
                    ("total" if category == "following" else "count") in raw) else None
                more = raw.get("has_more")
                if more is not None and (not isinstance(more, (bool, int)) or more not in (0, 1)):
                    raise IngestError("来源分页结束标记无效", code="invalid_response")
                more = bool(more) if more is not None else page * size < total if total is not None else len(rows) == size
                if not rows and more:
                    raise IngestError("来源分页提前结束", code="invalid_response")
                data = {"items": _items(rows, category, nav), "total": total, "has_more": more}
            _remember(_response_cache_key(db, account, client, category, 0 if category == "created" else page), data)
    if category == "created":
        data = {**data, "items": data["items"][(page - 1) * size:page * size], "has_more": page * size < data["total"]}
    return {**data, "page": page, "page_size": size, "category": category,
            "next_page": page + 1 if data["has_more"] else None, "cached": cached, "cache_seconds": CACHE_SECONDS}


@router.get("")
def list_sources(response: Response, account_id: str = Query(min_length=1, max_length=36),
                 category: Literal["created", "collected", "following"] = "created",
                 page: int = Query(1, ge=1, le=1000), refresh: bool = False,
                 _=Depends(require_admin), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return discover(db, account_id, category, page, refresh=refresh)


@router.get("/resolve")
def resolve(response: Response, account_id: str = Query(min_length=1, max_length=36),
            value: str = Query(min_length=1, max_length=2000), kind: Literal["favorite", "creator"] = "favorite",
            _=Depends(require_admin), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    try:
        parsed = resolve_source(value, kind)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    account = _account(db, account_id)
    key = _key(account, "resolve", parsed["kind"], parsed["source_id"])
    cached = _cached(key)
    if cached is not None:
        return cached
    with _client(db, account) as (client, nav):
        if parsed["kind"] == "creator":
            data = client.profile(parsed["source_id"])
            if not isinstance(data, dict) or _id(data.get("mid")) != parsed["source_id"]:
                raise IngestError("UP 资料标识不一致", code="invalid_response")
            title = source_title(data.get("name"))
        else:
            data = client.json("/x/v3/fav/folder/info", {"media_id": parsed["source_id"]})
            if not isinstance(data, dict) or _id(data.get("id")) != parsed["source_id"]:
                raise IngestError("收藏夹资料标识不一致", code="invalid_response")
            title = source_title(data.get("title"))
        if not title:
            raise IngestError("来源缺少名称", code="invalid_response")
        result = {**parsed, "title": title}
        _remember(_response_cache_key(db, account, client, "resolve", parsed["kind"], parsed["source_id"]), result)
        return result
