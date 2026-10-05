"""Bound the work and traffic of the public, immutable danmaku archive view.

Budgets and cached bytes are per API process. The latest snapshot is still looked
up on every request; only its content-addressed, immutable asset is cached.
"""
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import HTTPException
from starlette.responses import Response

MIB = 1024**2
MAX_ARCHIVE_BYTES = 16 * MIB


@dataclass
class _Bucket:
    remaining: float
    updated: float

    def available(self, now, capacity, rate):
        self.remaining = min(capacity, self.remaining + max(0, now - self.updated) * rate)
        self.updated = now
        return self.remaining


@dataclass
class _Client:
    requests: _Bucket
    traffic: _Bucket
    seen: float


@dataclass(frozen=True)
class _Entry:
    body: bytes | None
    retry_at: float = 0


def _unavailable(retry=1):
    return HTTPException(503, "弹幕归档暂不可读取", headers={"Retry-After": str(max(1, math.ceil(retry)))})


class DanmakuDelivery:
    def __init__(self, *, clock=time.monotonic, cache_bytes=64 * MIB, cache_entries=128,
                 max_body_bytes=MAX_ARCHIVE_BYTES, max_clients=4096, request_burst=20,
                 request_rate=1 / 3, global_request_burst=120, global_request_rate=2,
                 load_burst=4, load_rate=1 / 10, failure_seconds=5,
                 client_bytes=64 * MIB, client_byte_rate=MIB,
                 global_bytes=128 * MIB, global_byte_rate=4 * MIB):
        self.clock = clock
        self.cache_bytes, self.cache_entries = cache_bytes, cache_entries
        # Never repeatedly load a response too large to retain in the cache.
        self.max_body_bytes = min(max_body_bytes, cache_bytes, client_bytes, global_bytes)
        self.max_clients = max_clients
        self.request_burst, self.request_rate = request_burst, request_rate
        self.global_request_burst, self.global_request_rate = global_request_burst, global_request_rate
        self.load_burst, self.load_rate, self.failure_seconds = load_burst, load_rate, failure_seconds
        self.client_bytes, self.client_byte_rate = client_bytes, client_byte_rate
        self.global_bytes, self.global_byte_rate = global_bytes, global_byte_rate
        self.client_idle_seconds = max(request_burst / request_rate, client_bytes / client_byte_rate)
        now = clock()
        self._requests = _Bucket(global_request_burst, now)
        self._loads = _Bucket(load_burst, now)
        self._traffic = _Bucket(global_bytes, now)
        self._clients = OrderedDict()
        self._cache = OrderedDict()
        self._cached_bytes = 0
        self._loading = False
        self._lock = threading.Lock()

    @staticmethod
    def _limited(wait):
        return HTTPException(429, "弹幕请求过于频繁，请稍后再试",
                             headers={"Retry-After": str(max(1, math.ceil(wait)))})

    def reserve_request(self, client):
        """Reserve before database/storage access; do not trust raw HTTP IP headers."""
        with self._lock:
            now = self.clock()
            while self._clients:
                key, oldest = next(iter(self._clients.items()))
                if now - oldest.seen < self.client_idle_seconds:
                    break
                self._clients.pop(key)
            budget = self._clients.get(client)
            global_left = self._requests.available(now, self.global_request_burst, self.global_request_rate)
            if global_left < 1:
                raise self._limited((1 - global_left) / self.global_request_rate)
            if budget is None:
                if len(self._clients) >= self.max_clients:
                    # Evicting an active client would reset its quota under IP churn.
                    raise self._limited(self.client_idle_seconds)
                budget = _Client(_Bucket(self.request_burst, now), _Bucket(self.client_bytes, now), now)
                self._clients[client] = budget
            budget.seen = now
            self._clients.move_to_end(client)
            left = budget.requests.available(now, self.request_burst, self.request_rate)
            if left < 1:
                raise self._limited((1 - left) / self.request_rate)
            budget.requests.remaining -= 1
            self._requests.remaining -= 1
            return budget

    def response(self, budget, body):
        # Even warm cache hits have a bandwidth budget, reserved atomically.
        with self._lock:
            now = self.clock()
            personal = budget.traffic.available(now, self.client_bytes, self.client_byte_rate)
            total = self._traffic.available(now, self.global_bytes, self.global_byte_rate)
            wait = max((len(body) - personal) / self.client_byte_rate,
                       (len(body) - total) / self.global_byte_rate)
            if wait > 0:
                raise self._limited(wait)
            budget.traffic.remaining -= len(body)
            self._traffic.remaining -= len(body)
        # Passing bytes avoids FastAPI's recursive encoder and JSON serialization.
        return Response(body, media_type="application/json")

    def body(self, asset_id, loader):
        with self._lock:
            now = self.clock()
            entry = self._cache.get(asset_id)
            if entry is not None:
                if entry.body is not None:
                    self._cache.move_to_end(asset_id)
                    return entry.body
                if now < entry.retry_at:
                    raise _unavailable(entry.retry_at - now)
                self._cache.pop(asset_id)
            # Reject promptly instead of letting a queue occupy all API threads.
            # This also coalesces simultaneous misses for the same asset.
            if self._loading:
                raise _unavailable()
            available = self._loads.available(now, self.load_burst, self.load_rate)
            if available < 1:
                raise _unavailable((1 - available) / self.load_rate)
            self._loads.remaining -= 1
            self._loading = True
        result = None
        try:
            body = loader()
            if not isinstance(body, bytes) or len(body) > self.max_body_bytes:
                raise ValueError("Danmaku response exceeds cache budget")
            result = _Entry(body)
        except Exception:
            # Storage failure/invalid content must not become a repeated hot read.
            result = _Entry(None, self.clock() + self.failure_seconds)
        finally:
            with self._lock:
                # BaseException also releases the admission slot without caching.
                if result is not None:
                    size = len(result.body) if result.body is not None else 0
                    while self._cache and (len(self._cache) >= self.cache_entries
                                           or self._cached_bytes + size > self.cache_bytes):
                        _, removed = self._cache.popitem(last=False)
                        self._cached_bytes -= len(removed.body) if removed.body is not None else 0
                    self._cache[asset_id] = result
                    self._cached_bytes += size
                self._loading = False
        if result.body is None:
            raise _unavailable(self.failure_seconds)
        return result.body


def _render_asset(db, asset_id):
    from app.storage.service import read_asset_bytes
    payload = json.loads(read_asset_bytes(db, asset_id, max_bytes=MAX_ARCHIVE_BYTES))
    items = payload if isinstance(payload, list) else payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Invalid danmaku archive")
    modes = {1: 0, 2: 0, 3: 0, 5: 1, 4: 2}
    visible = []
    for item in items:
        if item.get("mode") in modes:
            # Parsed objects are private to this one load. Avoid copying every row.
            item["mode"] = modes[item["mode"]]
            visible.append(item)
    return json.dumps(visible, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


delivery = DanmakuDelivery()


def render_asset(db, asset_id):
    return delivery.body(asset_id, lambda: _render_asset(db, asset_id))
