"""Downloads share one durable global lane and do not retry source requests inline."""
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest import media, runner
from app.ingest.errors import IngestDeferred, IngestError
from app.ingest.diagnostics import canonical_source_endpoint, source_rejection
from app.models import Base, Job, SourceAccount


class SerialDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.tmp.name) / "test.sqlite"))
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.now = datetime.now(timezone.utc)
        self.account = SourceAccount(name="one", secret_encrypted="unused", status="valid")
        self.other = SourceAccount(name="two", secret_encrypted="unused", status="valid")
        self.db.add_all([self.account, self.other])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def lane(self):
        lane = runner.DownloadLane(self.db)
        lane.pacer = Mock(clock=lambda: self.now)
        return lane

    def test_other_account_finish_cursor_defers_before_reserving(self):
        self.other.next_video_at = self.now + timedelta(seconds=181)
        self.db.commit()
        lane = self.lane()
        with self.assertRaises(IngestDeferred) as raised:
            lane.before_video()
        self.assertEqual(raised.exception.code, "video_interval")
        self.assertEqual(raised.exception.retry_after_seconds, 181)
        lane.pacer.before_video.assert_not_called()
        lane.after_video()
        lane.pacer.after_video.assert_not_called()

    def test_first_part_reuses_pre_nav_reservation_then_next_part_reserves(self):
        lane = self.lane()
        lane.before_video()  # Worker startup before nav.
        lane.before_video()  # archive_media, first part.
        lane.pacer.before_video.assert_called_once_with(defer=True)
        lane.after_video()
        lane.pacer.after_video.assert_called_once_with()
        lane.after_video()  # The outer finally must not extend it again.
        lane.pacer.after_video.assert_called_once_with()
        lane.before_video()  # A later part, after the durable cursor is due.
        self.assertEqual(lane.pacer.before_video.call_count, 2)

    def test_attempt_finish_is_persisted_before_global_unlock_even_on_error(self):
        events = []
        @contextmanager
        def lock(db, name):
            self.assertEqual(name, "download:global")
            events.append("locked")
            try:
                yield
            finally:
                events.append("unlocked")
        with patch.object(runner, "_lock", side_effect=lock):
            with self.assertRaisesRegex(IngestError, "interrupted"):
                with runner.download_lane(self.db) as lane:
                    lane.pacer = Mock(clock=lambda: self.now,
                                      after_video=lambda: events.append("finished"))
                    lane.before_video()
                    raise IngestError("interrupted", code="job_stopped")
        self.assertEqual(events, ["locked", "finished", "unlocked"])

    def test_future_video_slot_makes_zero_source_requests(self):
        self.other.next_video_at = self.now + timedelta(minutes=10)
        job = Job(kind="download_media", target_id="unused", account_id=self.account.id,
                  status="running", dedupe_key="serial-test", checkpoint={})
        self.db.add(job)
        self.db.commit()
        client = Mock()
        with patch.object(runner, "BiliClient", return_value=client), \
             patch.object(runner, "decrypt_secret", return_value="SESSDATA=unused"):
            with self.assertRaises(IngestDeferred) as raised:
                runner.run_job(self.db, job)
        self.assertEqual(raised.exception.code, "video_interval")
        client.nav.assert_not_called()
        client.view.assert_not_called()
        client.close.assert_called_once_with()


    def test_rejection_checkpoint_records_only_safe_fields_and_cooldown_keeps_it(self):
        secret = "sensitive-token-123"
        job = Job(kind="verify_account", target_id=self.account.id, account_id=self.account.id,
                  status="running", dedupe_key="rejection-test", checkpoint={})
        self.db.add(job)
        self.db.commit()
        rejection = IngestError("response body: " + secret, code="rate_limited", http_status=429,
            source_endpoint="https://" + secret + "@api.bilibili.com/x/web-interface/nav?cookie=" + secret)
        client = Mock()
        client.nav.side_effect = rejection
        with patch.object(runner, "BiliClient", return_value=client), \
             patch.object(runner, "decrypt_secret", return_value="SESSDATA=" + secret):
            with self.assertRaises(IngestError) as raised:
                runner.run_job(self.db, job)
            self.assertIs(raised.exception, rejection)
            saved = dict(job.checkpoint["source_rejection"])
            self.assertEqual(saved["endpoint"], "/x/web-interface/nav")
            self.assertEqual(saved["http_status"], 429)
            self.assertEqual(set(saved), {"occurred_at", "endpoint", "http_status"})
            self.assertNotIn(secret, json.dumps(job.checkpoint))
            with self.assertRaises(IngestDeferred) as waiting:
                runner.run_job(self.db, job)
            self.assertEqual(waiting.exception.code, "account_cooldown")
            self.assertEqual(job.checkpoint["source_rejection"], saved)
            client.nav.assert_called_once_with()

    def test_rejection_endpoint_whitelist_strips_dynamic_paths_and_ignores_waits(self):
        secret = "private-id-and-token"
        for endpoint, expected in (
            ("https://cdn.bilivideo.com/" + secret + ".m4s?sign=" + secret, "media_cdn"),
            ("https://i0.hdslb.com/bfs/" + secret + ".jpg", "image_cdn"),
            ("https://www.bilibili.com/video/" + secret, "bilibili_page"),
            ("https://api.bilibili.com/x/unrecognized/" + secret, "bilibili_api"),
            ("https://other.invalid/" + secret, "source_resource")):
            with self.subTest(endpoint=expected):
                details = source_rejection(IngestError(secret, code="rate_limited",
                    source_endpoint=endpoint, source_code=-352), self.now)
                self.assertEqual(details["endpoint"], expected)
                self.assertEqual(details["source_code"], -352)
                self.assertNotIn(secret, json.dumps(details))
        self.assertIsNone(source_rejection(IngestDeferred("waiting"), self.now))
        self.assertEqual(canonical_source_endpoint(None), "unknown")


class AdvisoryLaneTests(unittest.TestCase):
    def test_workers_contend_for_one_lock_and_release_it_after_interruption(self):
        # Model the database's session advisory-lock primitive, not job state.
        # Independent engine connections must contend even for different jobs.
        held, mutex = set(), threading.Lock()
        class Connection:
            closed, invalidated = False, False
            def execution_options(self, **kwargs):
                if kwargs != {"isolation_level": "AUTOCOMMIT"}:
                    raise AssertionError("download lane opened a transaction")
                return self
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def scalar(self, query, params=None):
                if params is None:
                    return id(self)
                with mutex:
                    if params["key"] in held:
                        return False
                    held.add(params["key"])
                    return True
            def execute(self, query, params):
                with mutex:
                    held.remove(params["key"])
        engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), connect=Connection)
        db = SimpleNamespace(get_bind=lambda: engine)
        entered, release = threading.Event(), threading.Event()
        failure = []
        def first_worker():
            try:
                with runner.download_lane(db):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("test release timed out")
                    raise IngestError("cancelled", code="job_stopped")
            except IngestError:
                pass
            except Exception as error:
                failure.append(error)
        thread = threading.Thread(target=first_worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(IngestDeferred) as raised:
                with runner.download_lane(db):
                    self.fail("a second worker entered the download lane")
            self.assertEqual(raised.exception.code, "download_busy")
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(failure)
        self.assertFalse(thread.is_alive())
        with runner.download_lane(db):
            self.assertEqual(len(held), 1)
        self.assertEqual(held, set())


    def test_disconnected_or_reconnected_lock_connection_stops_download_guard(self):
        connection = SimpleNamespace(closed=False, invalidated=False,
                                     scalar=Mock(return_value=10))
        lane = runner.DownloadLane(None, connection)
        for mutation in ("invalidated", "closed", "backend_pid"):
            with self.subTest(mutation=mutation):
                connection.closed = mutation == "closed"
                connection.invalidated = mutation == "invalidated"
                connection.scalar.return_value = 11 if mutation == "backend_pid" else 10
                lane.last_guard = 0
                with self.assertRaises(IngestError) as raised:
                    lane.check_active()
                self.assertEqual(raised.exception.code, "download_lock_lost")


class DownloaderPolicyTests(unittest.TestCase):
    def test_legacy_parallel_policy_is_serial_and_no_inline_retry_or_partial_cleanup(self):
        captured = {}
        class Downloader:
            def __init__(self, options):
                captured.update(options)
            def __enter__(self):
                # Exercise the real PacedDownloader hook for a CDN request too.
                self.urlopen("https://test.bilivideo.com/media.m4s")
                return self
            def __exit__(self, *args): return False
            def urlopen(self, request): return None
            def build_format_selector(self, expression): return lambda ctx: []
        stopped = IngestError("source paused", code="rate_limited")
        client = SimpleNamespace(cookies=[], before_request=Mock(),
                                 before_video=Mock(side_effect=stopped))
        with tempfile.TemporaryDirectory() as temp, patch.object(settings, "scratch_dir", Path(temp)), \
             patch("yt_dlp.YoutubeDL", Downloader):
            partial = Path(temp) / "downloads" / "existing" / "media.mp4.part"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"checkpoint")
            with self.assertRaises(IngestError) as raised:
                media.archive_media(None, client, SimpleNamespace(), SimpleNamespace(),
                                    {"fragment_concurrency": 3})
            self.assertIs(raised.exception, stopped)
            self.assertEqual(partial.read_bytes(), b"checkpoint")
        self.assertEqual(captured["concurrent_fragment_downloads"], 1)
        for key in ("retries", "fragment_retries", "extractor_retries", "file_access_retries"):
            self.assertEqual(captured[key], 0)
        self.assertTrue(captured["continuedl"])
        client.before_request.assert_called_once_with("https://test.bilivideo.com/media.m4s")


if __name__ == "__main__":
    unittest.main()
