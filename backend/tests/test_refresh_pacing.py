"""Established credentials share request slots without risking a duplicate rotation."""
import unittest
from datetime import timedelta
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import source_credentials
from app.ingest.errors import IngestDeferred, IngestError
from app.ingest.passport import BiliPassport, CONFIRM, CORRESPOND, INFO, REFRESH
from app.ingest.throttle import AccountPacer, utc
from app.models import Base, SourceAccount, utcnow

OLD_COOKIE = "SESSDATA=oldsession; bili_jct=" + "a" * 32 + "; DedeUserID=1"
NEW_COOKIE = "SESSDATA=newsession; bili_jct=" + "b" * 32 + "; DedeUserID=1"


class RefreshPacingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.now = utcnow()
        self.account = SourceAccount(name="Account", uid="1", status="valid",
            secret_encrypted=OLD_COOKIE, refresh_token_encrypted="old-token", refresh_phase="ready")
        self.db.add(self.account)
        self.db.commit()
        self.account_id = self.account.id
        self.requests = []
        self.handlers = {}

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def account_row(self):
        with Session(self.engine, expire_on_commit=False) as reader:
            row = reader.get(SourceAccount, self.account_id)
            reader.expunge(row)
            return row

    def change_account(self, **changes):
        with Session(self.engine) as writer:
            row = writer.get(SourceAccount, self.account_id)
            for key, value in changes.items():
                setattr(row, key, value)
            writer.commit()

    def transport(self, request):
        url = str(request.url).split("?")[0]
        self.requests.append((url, self.now))
        handler = self.handlers.get(url, self.handlers.get("correspond") if url.startswith(CORRESPOND) else None)
        if handler:
            return handler(request)
        if url == INFO:
            return httpx.Response(200, json={"code": 0, "data": {"refresh": True, "timestamp": 1_790_000_000_000}})
        if url.startswith(CORRESPOND):
            return httpx.Response(200, text='<div id="1-name">' + "c" * 32 + "</div>")
        if url == REFRESH:
            self.assertEqual(self.account_row().refresh_phase, "refreshing")
            return httpx.Response(200, json={"code": 0, "data": {"refresh_token": "new-token"}}, headers=[
                ("set-cookie", "SESSDATA=newsession; Domain=.bilibili.com; Path=/"),
                ("set-cookie", "bili_jct=" + "b" * 32 + "; Domain=.bilibili.com; Path=/"),
                ("set-cookie", "DedeUserID=1; Domain=.bilibili.com; Path=/")])
        if url == CONFIRM:
            row = self.account_row()
            self.assertEqual(row.secret_encrypted, NEW_COOKIE)
            self.assertEqual(row.refresh_pending_encrypted, "old-token")
            self.assertEqual(row.refresh_phase, "pending_confirm")
            return httpx.Response(200, json={"code": 0, "data": {}})
        raise AssertionError("Unexpected protocol endpoint")

    def run_refresh(self, *, hook=None):
        def sleep(seconds):
            self.now += timedelta(seconds=seconds)
        def pacer(db, account, policy, guard):
            instance = AccountPacer(db, account, policy, guard, clock=lambda: self.now,
                sleep=sleep, jitter=lambda _: 0)
            if hook:
                before_request = instance.before_request
                def before(url):
                    before_request(url)
                    hook(url)
                instance.before_request = before
            return instance
        factory = lambda: BiliPassport(transport=httpx.MockTransport(self.transport))
        with patch("app.source_credentials.decrypt_secret", side_effect=lambda value: value), \
             patch("app.source_credentials.encrypt_secret", side_effect=lambda value: value), \
             patch("app.source_credentials.AccountPacer", side_effect=pacer):
            return source_credentials.refresh_account(self.db, self.account_id, passport_factory=factory)

    def test_every_refresh_request_is_paced_and_credentials_saved_before_confirm(self):
        result = self.run_refresh()
        self.assertTrue(result["rotated"])
        self.assertEqual([url for url, _ in self.requests if not url.startswith(CORRESPOND)], [INFO, REFRESH, CONFIRM])
        self.assertEqual(len(self.requests), 4)
        for (_, earlier), (_, later) in zip(self.requests, self.requests[1:]):
            self.assertGreaterEqual((later - earlier).total_seconds(), 8)
        row = self.account_row()
        self.assertEqual(row.refresh_phase, "ready")
        self.assertEqual(row.secret_encrypted, NEW_COOKIE)
        self.assertIsNone(row.refresh_pending_encrypted)
        self.assertEqual(row.refresh_failures, 0)

    def test_cooldown_before_refresh_post_does_not_mark_an_unsent_rotation(self):
        def correspond(request):
            self.change_account(cooldown_until=self.now + timedelta(hours=1))
            return httpx.Response(200, text='<div id="1-name">' + "c" * 32 + "</div>")
        self.handlers["correspond"] = correspond
        with self.assertRaises(IngestDeferred) as caught:
            self.run_refresh()
        self.assertEqual(caught.exception.code, "account_cooldown")
        self.assertFalse(getattr(caught.exception, "mutation_started", False))
        self.assertEqual(len(self.requests), 2)
        row = self.account_row()
        self.assertEqual(row.refresh_phase, "checking")
        self.assertEqual(row.secret_encrypted, OLD_COOKIE)
        self.assertEqual(row.refresh_failures, 0)

    def test_generation_change_during_pacing_stops_before_sending_stale_cookies(self):
        def hook(url):
            if url == REFRESH:
                self.change_account(secret_encrypted="replaced")
        with self.assertRaises(IngestDeferred) as caught:
            self.run_refresh(hook=hook)
        self.assertEqual(caught.exception.code, "account_changed")
        self.assertFalse(getattr(caught.exception, "mutation_started", False))
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.account_row().secret_encrypted, "replaced")
        self.assertEqual(self.account_row().refresh_phase, "checking")

    def test_http_rejection_sets_shared_cooldown(self):
        self.handlers[INFO] = lambda _: httpx.Response(429, headers={"Retry-After": "7200"})
        result = self.run_refresh()
        self.assertEqual(result["error_code"], "rate_limited")
        row = self.account_row()
        self.assertGreaterEqual(utc(row.cooldown_until), self.now + timedelta(seconds=7200))
        self.assertEqual(row.risk_failures, 1)
        self.assertEqual(row.refresh_failures, 1)
        self.assertEqual(len(self.requests), 1)

    def test_structured_rejection_does_not_claim_uncertain_rotation(self):
        self.handlers[REFRESH] = lambda _: httpx.Response(200, json={"code": -352, "data": {}})
        result = self.run_refresh()
        self.assertEqual(result["error_code"], "rate_limited")
        row = self.account_row()
        self.assertEqual(row.refresh_phase, "error")
        self.assertGreater(utc(row.cooldown_until), self.now)
        self.assertEqual(row.risk_failures, 1)
        self.assertEqual(row.secret_encrypted, OLD_COOKIE)
        self.assertEqual(len(self.requests), 3)

    def test_post_transport_failure_still_requires_reauthorization(self):
        def failed(request):
            raise httpx.ReadTimeout("Synthetic failure", request=request)
        self.handlers[REFRESH] = failed
        result = self.run_refresh()
        self.assertEqual(result["error_code"], "refresh_uncertain")
        row = self.account_row()
        self.assertEqual(row.refresh_phase, "relogin_required")
        self.assertEqual(row.secret_encrypted, OLD_COOKIE)
        self.assertIsNone(row.cooldown_until)

    def test_pending_confirmation_cooldown_preserves_saved_credentials(self):
        self.change_account(secret_encrypted=NEW_COOKIE, refresh_token_encrypted="new-token",
            refresh_pending_encrypted="old-token", refresh_phase="pending_confirm")
        def hook(url):
            if url == CONFIRM:
                raise IngestDeferred("等待源站冷却", retry_after_seconds=300)
        with self.assertRaises(IngestDeferred):
            self.run_refresh(hook=hook)
        self.assertEqual(self.requests, [])
        row = self.account_row()
        self.assertEqual(row.refresh_phase, "pending_confirm")
        self.assertEqual(row.secret_encrypted, NEW_COOKIE)
        self.assertEqual(row.refresh_pending_encrypted, "old-token")
        self.assertEqual(row.refresh_failures, 0)

    def test_pending_confirmation_rejection_sets_cooldown_without_rotating_again(self):
        self.change_account(secret_encrypted=NEW_COOKIE, refresh_token_encrypted="new-token",
            refresh_pending_encrypted="old-token", refresh_phase="pending_confirm")
        self.handlers[CONFIRM] = lambda _: httpx.Response(429)
        result = self.run_refresh()
        self.assertEqual(result["error_code"], "confirm_failed")
        self.assertEqual([url for url, _ in self.requests], [CONFIRM])
        row = self.account_row()
        self.assertEqual(row.refresh_phase, "pending_confirm")
        self.assertEqual(row.refresh_pending_encrypted, "old-token")
        self.assertEqual(row.risk_failures, 1)
        self.assertGreater(utc(row.cooldown_until), self.now)

    def test_qr_protocol_has_no_account_hook_by_default(self):
        with BiliPassport(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as passport:
            self.assertIsNone(passport.before_request)


if __name__ == "__main__":
    unittest.main()
