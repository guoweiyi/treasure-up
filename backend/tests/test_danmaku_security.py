import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import danmaku_delivery
from app.danmaku_delivery import DanmakuDelivery
from app.models import Asset, DanmakuSnapshot, utcnow
from app.playback_limits import client_hash
from app.storage import service
from test_api import context, seed_catalog  # noqa: F401


class Clock:
    value = 0

    def __call__(self):
        return self.value


@pytest.fixture(autouse=True)
def isolated_delivery(monkeypatch):
    clock = Clock()
    delivery = DanmakuDelivery(clock=clock)
    monkeypatch.setattr(danmaku_delivery, "delivery", delivery)
    return delivery, clock


def archive(db, tmp, part, items, name="danmaku.json", **values):
    path = tmp / name
    path.write_text(json.dumps(items), encoding="utf-8")
    asset = service.ingest_file(db, path, kind="danmaku", mime_type="application/json")
    snapshot = DanmakuSnapshot(part_id=part.id, run_id=name, data_asset_id=asset.id,
                               status="complete", **values)
    db.add(snapshot)
    db.commit()
    return snapshot


def test_public_repeated_requests_read_and_serialize_an_archive_once(context, monkeypatch):
    client, db, tmp = context
    _, part, _ = seed_catalog(db)
    archive(db, tmp, part, {"items": [{"text": "滚动", "time": 1, "mode": 1},
                                     {"text": "顶部", "time": 2, "mode": 5},
                                     {"text": "底部", "time": 3, "mode": 4},
                                     {"text": "BAS", "time": 4, "mode": 9}]})
    read = service.read_asset_bytes
    render = danmaku_delivery._render_asset
    reads, renders = [], []

    def counted_read(*args, **kwargs):
        reads.append(args[1])
        assert kwargs["max_bytes"] == 16 * 1024**2
        return read(*args, **kwargs)

    def counted_render(*args):
        renders.append(args[1])
        return render(*args)

    monkeypatch.setattr(service, "read_asset_bytes", counted_read)
    monkeypatch.setattr(danmaku_delivery, "_render_asset", counted_render)
    responses = [client.get(f"/api/v1/parts/{part.id}/danmaku") for _ in range(10)]
    assert all(response.status_code == 200 for response in responses)
    assert all(response.content == responses[0].content for response in responses)
    assert [item["mode"] for item in responses[0].json()] == [0, 1, 2]
    assert responses[0].headers["content-type"] == "application/json"
    assert len(reads) == len(renders) == 1


def test_latest_snapshot_and_changed_asset_take_effect_without_cache_expiry(context):
    client, db, tmp = context
    _, part, _ = seed_catalog(db)
    first = archive(db, tmp, part, [{"mode": 1, "text": "旧"}])
    url = f"/api/v1/parts/{part.id}/danmaku"
    assert client.get(url).json()[0]["text"] == "旧"
    second = archive(db, tmp, part, [{"mode": 1, "text": "新"}], "new.json",
                     created_at=utcnow() + timedelta(seconds=1))
    assert client.get(url).json()[0]["text"] == "新"
    # Ingest also updates a snapshot in place while completing it.
    second.data_asset_id = first.data_asset_id
    db.commit()
    assert client.get(url).json()[0]["text"] == "旧"
    db.delete(first)
    db.delete(second)
    db.commit()
    assert client.get(url).json() == []


def test_concurrent_cold_requests_do_not_multiply_expensive_work():
    delivery = DanmakuDelivery()
    started, release = Event(), Event()
    reads = []

    def read():
        reads.append(True)
        started.set()
        assert release.wait(5)
        return b'[{"mode":0}]'

    def attempt(key):
        try:
            return delivery.body(key, read)
        except HTTPException as error:
            assert error.headers["Retry-After"] == "1"
            return error.status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        first = executor.submit(attempt, "asset")
        try:
            assert started.wait(5)
            # Same-asset and different-asset misses both have bounded admission.
            blocked = [executor.submit(attempt, key) for key in ["asset"] * 5 + ["other"] * 5]
            assert [job.result(timeout=5) for job in blocked] == [503] * 10
            assert reads == [True]
        finally:
            release.set()
        body = first.result(timeout=5)
    assert delivery.body("asset", lambda: pytest.fail("cache hit reread storage")) is body


def test_interrupted_loader_releases_admission_without_caching_a_partial_result():
    delivery = DanmakuDelivery()

    def cancelled():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        delivery.body("asset", cancelled)
    assert delivery.body("asset", lambda: b"[]") == b"[]"


def test_registered_oversized_archive_is_rejected_before_opening_storage(context, monkeypatch):
    client, db, tmp = context
    _, part, _ = seed_catalog(db)
    snapshot = archive(db, tmp, part, [])
    db.get(Asset, snapshot.data_asset_id).size = danmaku_delivery.MAX_ARCHIVE_BYTES + 1
    db.commit()
    opened = []

    def forbidden(*args):
        opened.append(True)
        raise AssertionError("oversized archive must not open storage")

    monkeypatch.setattr(service, "get_adapter", forbidden)
    response = client.get(f"/api/v1/parts/{part.id}/danmaku")
    assert response.status_code == 503
    assert opened == []


def test_stream_that_exceeds_registered_size_is_read_with_a_hard_limit(context, monkeypatch):
    client, db, tmp = context
    _, part, _ = seed_catalog(db)
    archive(db, tmp, part, [])
    read_sizes = []

    class Stream:
        def read(self, size):
            read_sizes.append(size)
            assert size == danmaku_delivery.MAX_ARCHIVE_BYTES + 1
            return b"x" * size

    @contextmanager
    def reader(*args, **kwargs):
        yield Stream()

    monkeypatch.setattr(service, "get_adapter", lambda _: SimpleNamespace(reader=reader))
    url = f"/api/v1/parts/{part.id}/danmaku"
    assert client.get(url).status_code == 503
    assert client.get(url).status_code == 503
    assert read_sizes == [danmaku_delivery.MAX_ARCHIVE_BYTES + 1]


def test_failed_reads_are_temporarily_cached_and_recover(context, monkeypatch, isolated_delivery):
    client, db, tmp = context
    _, clock = isolated_delivery
    _, part, _ = seed_catalog(db)
    archive(db, tmp, part, [{"mode": 1, "text": "恢复"}])
    original = service.read_asset_bytes
    attempts = []

    def failure(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("private provider credentials")

    monkeypatch.setattr(service, "read_asset_bytes", failure)
    url = f"/api/v1/parts/{part.id}/danmaku"
    for _ in range(3):
        response = client.get(url)
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "5"
        assert "credentials" not in response.text
    assert attempts == [True]
    monkeypatch.setattr(service, "read_asset_bytes", original)
    clock.value += 5
    assert client.get(url).json()[0]["text"] == "恢复"


@pytest.mark.parametrize("data", [b"{", b'{"items":null}', b'[null]', b'[ {"mode": 1, "time": NaN} ]'])
def test_invalid_archives_are_bounded_failures_that_do_not_wedge_loader(monkeypatch, data):
    calls = []

    def read(*args, **kwargs):
        calls.append(True)
        return data

    monkeypatch.setattr(service, "read_asset_bytes", read)
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            danmaku_delivery.render_asset(None, "broken")
        assert error.value.status_code == 503
    assert calls == [True]
    monkeypatch.setattr(service, "read_asset_bytes", lambda *args, **kwargs: b"[]")
    assert danmaku_delivery.render_asset(None, "healthy") == b"[]"


def test_eviction_is_bounded_by_bytes_and_entry_count_and_miss_budget():
    clock = Clock()
    delivery = DanmakuDelivery(clock=clock, cache_bytes=8, cache_entries=2,
                               load_burst=4, load_rate=1)
    counts = {}

    def load(key, value=b"1234"):
        def read():
            counts[key] = counts.get(key, 0) + 1
            return value
        return delivery.body(key, read)

    load("a")
    load("b")
    load("a")  # Keep a most recently used.
    load("c")  # b is evicted, total retained bytes remain bounded.
    assert delivery._cached_bytes == 8
    assert len(delivery._cache) == 2
    load("a")
    load("b")
    assert counts == {"a": 1, "b": 2, "c": 1}
    with pytest.raises(HTTPException) as error:
        load("different")
    assert error.value.status_code == 503
    assert "different" not in counts  # Cache churn cannot bypass cold-load budget.
    clock.value += 1
    assert load("different", b"") == b""
    assert len(delivery._cache) == 2  # Zero-byte/failed entries also have a cap.


def test_uncacheable_large_responses_are_not_reloaded_on_every_request():
    delivery = DanmakuDelivery(cache_bytes=8)
    reads = []

    def read():
        reads.append(True)
        return b"123456789"

    for _ in range(3):
        with pytest.raises(HTTPException) as error:
            delivery.body("too-large", read)
        assert error.value.status_code == 503
    assert reads == [True]
    assert delivery._cached_bytes == 0
    assert delivery.body("fits", lambda: b"12345678") == b"12345678"


def test_public_ip_throttle_cannot_be_reset_with_headers_or_cookies(context, monkeypatch):
    client, db, _ = context
    _, part, _ = seed_catalog(db)
    clock = Clock()
    delivery = DanmakuDelivery(clock=clock, request_burst=2, request_rate=1)
    monkeypatch.setattr(danmaku_delivery, "delivery", delivery)
    url = f"/api/v1/parts/{part.id}/danmaku"
    assert client.get(url).status_code == 200
    assert client.get(url, headers={"X-Forwarded-For": "192.0.2.2"}).status_code == 200
    response = client.get(url, headers={"X-Forwarded-For": "192.0.2.3", "Cookie": "treasure_session=new"})
    assert response.status_code == 429 and response.headers["Retry-After"] == "1"
    clock.value += 1
    assert client.get(url).status_code == 200


def test_missing_parts_are_rate_limited_before_database_work(context, monkeypatch):
    client, _, _ = context
    monkeypatch.setattr(danmaku_delivery, "delivery", DanmakuDelivery(request_burst=1))
    assert client.get("/api/v1/parts/missing/danmaku").status_code == 404
    assert client.get("/api/v1/parts/another-missing/danmaku").status_code == 429


def test_global_request_budget_and_client_table_fail_closed():
    clock = Clock()
    delivery = DanmakuDelivery(clock=clock, max_clients=2, request_burst=1, request_rate=1,
                               global_request_burst=2, global_request_rate=1,
                               client_bytes=1, client_byte_rate=1)
    delivery.reserve_request("a")
    delivery.reserve_request("b")
    with pytest.raises(HTTPException) as error:
        delivery.reserve_request("c")
    assert error.value.status_code == 429
    assert len(delivery._clients) == 2
    clock.value += 0.5
    with pytest.raises(HTTPException):
        delivery.reserve_request("a")
    clock.value += 0.5
    delivery.reserve_request("c")  # Fully replenished idle clients can be pruned.
    assert len(delivery._clients) == 1


def test_client_table_does_not_evict_active_quotas_to_admit_new_clients():
    clock = Clock()
    delivery = DanmakuDelivery(clock=clock, max_clients=1, request_burst=1, request_rate=1)
    delivery.reserve_request("a")
    with pytest.raises(HTTPException):
        delivery.reserve_request("b")
    with pytest.raises(HTTPException):
        delivery.reserve_request("a")
    assert list(delivery._clients) == ["a"]


def test_ip_normalization_groups_ipv6_prefixes_and_mapped_ipv4():
    def key(host):
        return client_hash(SimpleNamespace(client=SimpleNamespace(host=host)))

    assert key("2001:db8::1") == key("2001:db8::ffff")
    assert key("2001:db8::1") != key("2001:db8:0:1::1")
    assert key("::ffff:192.0.2.1") == key("192.0.2.1")
    clock = Clock()
    delivery = DanmakuDelivery(clock=clock, request_burst=1)
    delivery.reserve_request(key("2001:db8::1"))
    with pytest.raises(HTTPException) as error:
        delivery.reserve_request(key("2001:db8::2"))
    assert error.value.status_code == 429


def test_warm_responses_have_atomic_per_client_and_global_byte_budgets():
    clock = Clock()
    delivery = DanmakuDelivery(clock=clock, client_bytes=4, client_byte_rate=1,
                               global_bytes=6, global_byte_rate=1)
    first = delivery.reserve_request("a")
    assert delivery.response(first, b"1234").body == b"1234"
    with pytest.raises(HTTPException) as error:
        delivery.response(first, b"1")
    assert error.value.status_code == 429
    second = delivery.reserve_request("b")
    with pytest.raises(HTTPException):
        delivery.response(second, b"123")
    assert delivery.response(second, b"12").body == b"12"
    clock.value += 4
    assert delivery.response(first, b"1234").body == b"1234"


def test_concurrent_byte_reservations_do_not_overspend():
    clock = Clock()
    delivery = DanmakuDelivery(clock=clock, client_bytes=8, global_bytes=8)
    budget = delivery.reserve_request("a")

    def send():
        try:
            return delivery.response(budget, b"1234").status_code
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(lambda _: send(), range(16)))
    assert statuses.count(200) == 2
    assert statuses.count(429) == 14
