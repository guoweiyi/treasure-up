"""Shared pacing and cooldown regressions without requests to the real source."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ingest.client import BiliClient
from app.ingest.errors import IngestDeferred, IngestError
from app.ingest.runner import account_request_scope
from app.ingest.throttle import AccountPacer, utc
from app.models import Base, Setting, SourceAccount


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.on_sleep = None
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.on_sleep:
            self.on_sleep()
        self.now += timedelta(seconds=seconds)


class SourcePacingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.folder.name) / "pacing.db"))
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.account = SourceAccount(name="pacing test", secret_encrypted="test-only", status="valid")
        self.db.add(self.account)
        self.db.commit()
        self.clock = Clock()
        self.pacer = self.make_pacer()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.folder.cleanup()

    def make_pacer(self, policy=None, **kwargs):
        return AccountPacer(self.db, self.account, policy or {}, lambda: None,
                            clock=self.clock, sleep=self.clock.sleep, jitter=lambda _: 0, **kwargs)

    def read(self):
        with Session(self.engine) as reader:
            account = reader.get(SourceAccount, self.account.id)
            reader.expunge(account)
            return account

    def change(self, **fields):
        with Session(self.engine) as writer:
            account = writer.get(SourceAccount, self.account.id)
            for key, value in fields.items():
                setattr(account, key, value)
            writer.commit()

    def setting(self, **policy):
        with Session(self.engine) as writer:
            writer.merge(Setting(key="ingest", value=policy))
            writer.commit()

    def test_separate_pacers_share_slots_despite_stale_caller_account(self):
        second = self.make_pacer({"request_interval_seconds": 1})
        self.pacer.before_request("https://api.bilibili.com/x/web-interface/nav")
        first = self.clock()
        second.before_request("https://api.bilibili.com/x/web-interface/view")
        self.assertEqual(self.clock() - first, timedelta(seconds=8))
        self.assertEqual(utc(self.read().next_request_at), first + timedelta(seconds=16))
        self.assertIsNone(self.account.next_request_at)

    def test_live_policy_slows_old_jobs_and_never_speeds_up_source_policy(self):
        self.setting(request_interval_seconds=15)
        old_job = self.make_pacer({"request_interval_seconds": 1})
        old_job.before_request()
        self.assertEqual(utc(self.read().next_request_at), self.clock() + timedelta(seconds=15))
        self.clock.now += timedelta(seconds=15)
        slower_source = self.make_pacer({"request_interval_seconds": 30})
        slower_source.before_request()
        self.assertEqual(utc(self.read().next_request_at), self.clock() + timedelta(seconds=30))

    def test_wait_holds_no_connection_and_cancellation_leaves_no_future_slot(self):
        self.pacer.before_request()
        reserved = utc(self.read().next_request_at)
        def cancel():
            self.assertEqual(self.engine.pool.checkedout(), 0)
            raise IngestError("canceled", code="job_stopped", retryable=False)
        self.clock.on_sleep = cancel
        with self.assertRaises(IngestError):
            self.pacer.before_request()
        self.assertEqual(utc(self.read().next_request_at), reserved)

    def test_new_cooldown_interrupts_a_waiting_request(self):
        self.pacer.before_request()
        self.clock.on_sleep = lambda: self.change(cooldown_until=self.clock() + timedelta(minutes=30))
        with self.assertRaises(IngestDeferred) as caught:
            self.make_pacer().before_request()
        self.assertEqual(caught.exception.code, "account_cooldown")
        self.assertEqual(len(self.clock.sleeps), 1)

    def test_cdn_does_not_consume_api_slot_but_observes_live_cooldown(self):
        self.pacer.before_request("https://i0.hdslb.com/bfs/archive/test.jpg")
        self.assertIsNone(self.read().next_request_at)
        self.change(cooldown_until=self.clock() + timedelta(minutes=30))
        with self.assertRaises(IngestDeferred):
            self.pacer.before_request("https://upos-sz-mirror.bilivideo.com/test.m4s")

    def test_foreground_wait_is_bounded_without_reserving_a_slot(self):
        deadline = self.clock() + timedelta(seconds=45)
        self.change(next_request_at=deadline)
        with self.assertRaises(IngestDeferred) as caught:
            self.make_pacer(max_wait_seconds=20).before_request()
        self.assertEqual(caught.exception.code, "request_interval")
        self.assertEqual(caught.exception.retry_after_seconds, 45)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(utc(self.read().next_request_at), deadline)

    def test_video_interval_is_measured_after_completion(self):
        self.pacer.before_video(defer=True)
        self.clock.now += timedelta(minutes=10)
        self.pacer.after_video()
        self.assertEqual(utc(self.read().next_video_at), self.clock() + timedelta(seconds=180))
        with self.assertRaises(IngestDeferred) as caught:
            self.make_pacer({"video_interval_seconds": 10}).before_video(defer=True)
        self.assertEqual(caught.exception.retry_after_seconds, 180)
        self.assertEqual(caught.exception.code, "video_interval")

    def test_one_episode_is_not_multiplied_by_inflight_rejections(self):
        first = IngestError("challenge", code="rate_limited")
        self.pacer.failed(first)
        self.pacer.failed(first)
        self.pacer.failed(IngestError("second response", code="rate_limited"))
        account = self.read()
        self.assertEqual(account.risk_failures, 1)
        self.assertEqual(utc(account.cooldown_until), self.clock() + timedelta(minutes=30))
        self.assertEqual(first.retry_after_seconds, 1800)
        self.assertFalse(first.blocked)

    def test_retry_after_longer_than_own_backoff_is_honored(self):
        error = IngestError("429", code="rate_limited", retry_after_seconds=172800)
        self.pacer.failed(error)
        self.assertEqual(utc(self.read().cooldown_until), self.clock() + timedelta(days=2))
        self.assertEqual(error.retry_after_seconds, 172800)

    def test_recent_success_does_not_reset_risk_escalation(self):
        self.pacer.failed(IngestError("challenge", code="rate_limited"))
        self.clock.now += timedelta(minutes=31)
        self.pacer.succeeded()
        self.db.commit()
        self.assertEqual(self.read().risk_failures, 1)
        self.pacer.failed(IngestError("challenge again", code="rate_limited"))
        self.assertEqual(self.read().risk_failures, 2)
        self.assertEqual(utc(self.read().cooldown_until), self.clock() + timedelta(hours=1))

    def test_risk_escalation_expires_after_a_quiet_day(self):
        self.change(risk_failures=5, cooldown_until=self.clock() - timedelta(days=2))
        self.pacer.failed(IngestError("new episode", code="rate_limited"))
        self.assertEqual(self.read().risk_failures, 1)
        self.clock.now += timedelta(days=2)
        self.pacer.succeeded()
        self.db.commit()
        self.assertEqual(self.read().risk_failures, 0)

    def client(self, handler):
        client = BiliClient("SESSDATA=test-only", transport=httpx.MockTransport(handler), sleep=lambda _: None)
        client.before_request = self.pacer.before_request
        client.request_context = lambda url: account_request_scope(
            self.db, self.account, client, self.pacer, url, decryptor=lambda _: "SESSDATA=test-only")
        self.addCleanup(client.close)
        return client

    def test_json_challenge_is_persisted_inside_request_scope_once(self):
        client = self.client(lambda _: httpx.Response(200, json={"code": -352, "data": None}))
        with self.assertRaises(IngestError) as caught:
            client.view("BV1TjTd6sEGZ")
        self.assertTrue(caught.exception.account_backoff_applied)
        self.assertEqual(caught.exception.source_endpoint, "/x/web-interface/view")
        self.pacer.failed(caught.exception)
        self.assertEqual(self.read().risk_failures, 1)

    def test_danmaku_json_challenge_is_also_inside_request_scope(self):
        client = self.client(lambda _: httpx.Response(200, json={"code": -401, "data": None}))
        client.images = {"img_url": "https://example.invalid/" + "a" * 32 + ".png",
                         "sub_url": "https://example.invalid/" + "b" * 32 + ".png"}
        client.images_at = 100
        with patch("app.ingest.client.time.monotonic", return_value=110):
            with self.assertRaises(IngestError) as caught:
                client.segment(1, 2, 1)
        self.assertTrue(caught.exception.account_backoff_applied)
        self.assertEqual(caught.exception.source_code, -401)
        self.assertEqual(self.read().risk_failures, 1)

    def test_http_retry_after_is_preserved_in_shared_cooldown(self):
        client = self.client(lambda _: httpx.Response(429, headers={"Retry-After": "7200"}))
        with self.assertRaises(IngestError) as caught:
            client.view("BV1TjTd6sEGZ")
        self.assertEqual(caught.exception.retry_after_seconds, 7200)
        self.assertEqual(utc(self.read().cooldown_until), self.clock() + timedelta(hours=2))

    def test_expired_foreground_deadline_sends_no_source_request(self):
        requests = []
        client = BiliClient(transport=httpx.MockTransport(lambda request:
            requests.append(request) or httpx.Response(200, json={"code": 0, "data": {}})))
        self.addCleanup(client.close)
        client.deadline = 5
        with patch("app.ingest.client.time.monotonic", return_value=6), self.assertRaises(IngestDeferred) as caught:
            client.view("BV1TjTd6sEGZ")
        self.assertEqual(caught.exception.code, "request_interval")
        self.assertEqual(requests, [])

    def test_slow_foreground_stream_is_stopped_by_total_deadline(self):
        now = [0]
        class SlowStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"code":0,'
                now[0] = 76
                yield b'"data":{}}'
        client = BiliClient(transport=httpx.MockTransport(lambda _:
            httpx.Response(200, stream=SlowStream())), sleep=lambda _: None)
        self.addCleanup(client.close)
        client.deadline = 75
        with patch("app.ingest.client.time.monotonic", side_effect=lambda: now[0]), self.assertRaises(IngestDeferred) as caught:
            client.view("BV1TjTd6sEGZ")
        self.assertEqual(caught.exception.code, "request_interval")
        self.assertEqual(caught.exception.retry_after_seconds, 8)

    def test_wbi_keys_are_reused_across_slow_pages(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={"code": 0, "data": {"ok": True}})
        client = BiliClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)
        self.addCleanup(client.close)
        client.images = {"img_url": "https://example.invalid/" + "a" * 32 + ".png",
                         "sub_url": "https://example.invalid/" + "b" * 32 + ".png"}
        client.images_at = 100
        with patch("app.ingest.client.time.monotonic", return_value=200):
            client.comment_page(1)
            client.comment_page(1, "next")
        self.assertEqual(calls, ["/x/v2/reply/wbi/main", "/x/v2/reply/wbi/main"])


if __name__ == "__main__":
    unittest.main()
