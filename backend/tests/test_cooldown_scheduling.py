"""Shared upstream cooldown is scheduling state, not another failed attempt."""
import unittest
from datetime import timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import jobs
from app.models import Base, Job, JobAttempt, OutboxEvent, SourceAccount, SourceSubscription, utcnow


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class CooldownSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.now = utcnow()
        self.account = SourceAccount(name="Account", secret_encrypted="unused",
            status="valid", cooldown_until=self.now + timedelta(hours=2))
        self.db.add(self.account)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def job(self, kind="archive_video", **changes):
        values = dict(kind=kind, target_id="video", account_id=self.account.id,
            status="queued", available_at=self.now - timedelta(seconds=1), attempts=2,
            checkpoint={"metadata_done": True, "comments": {"cursor": 17}},
            dedupe_key=str(uuid4()))
        values.update(changes)
        row = Job(**values)
        self.db.add(row)
        self.db.flush()
        self.db.add(OutboxEvent(job_id=row.id))
        self.db.commit()
        return row

    def dispatch(self, now=None):
        delivered = []
        with patch("app.jobs.utcnow", return_value=now or self.now):
            jobs.recover_and_dispatch(self.db, lambda identity, kind: delivered.append((identity, kind)))
        self.db.expire_all()
        return delivered

    def test_all_source_jobs_wait_without_claims_and_local_jobs_run(self):
        source_jobs = [self.job(kind) for kind in jobs.SOURCE_JOB_KINDS]
        local_jobs = [self.job(kind) for kind in ("create_playback", "prepare_media", "sync_video", "backup")]
        self.assertEqual({identity for identity, _ in self.dispatch()}, {row.id for row in local_jobs})
        for row in source_jobs:
            self.assertEqual(aware(row.available_at), aware(self.account.cooldown_until))
            self.assertEqual(row.error, jobs.SOURCE_COOLDOWN_MESSAGE)
            self.assertEqual(row.attempts, 2)
            self.assertEqual(row.checkpoint, {"metadata_done": True, "comments": {"cursor": 17}})
        self.assertEqual(self.db.scalars(select(JobAttempt)).all(), [])

    def test_cooldown_does_not_block_a_different_account(self):
        other = SourceAccount(name="Other", secret_encrypted="unused", status="valid")
        self.db.add(other)
        self.db.commit()
        waiting = self.job()
        ready = self.job(account_id=other.id)
        self.assertEqual(self.dispatch(), [(ready.id, ready.kind)])
        self.assertIsNotNone(jobs.claim(self.db, ready.id, "worker", attempt=JobAttempt(job_id=ready.id)))
        self.assertEqual(ready.attempts, 3)
        self.assertEqual(waiting.attempts, 2)

    def test_extended_cooldown_updates_existing_wait_and_is_idempotent(self):
        row = self.job()
        self.assertEqual(self.dispatch(), [])
        self.account.cooldown_until = self.now + timedelta(hours=5)
        self.db.commit()
        self.assertEqual(self.dispatch(), [])
        self.assertEqual(self.dispatch(), [])
        self.assertEqual(aware(row.available_at), aware(self.account.cooldown_until))
        self.assertEqual(row.attempts, 2)
        self.assertEqual(row.checkpoint["comments"]["cursor"], 17)
        events = self.db.scalars(select(OutboxEvent).where(OutboxEvent.job_id == row.id)).all()
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].published_at)

    def test_claim_rejects_stale_redis_hint_without_an_attempt(self):
        row = self.job()
        attempt = JobAttempt(job_id=row.id)
        self.assertIsNone(jobs.claim(self.db, row.id, "worker", attempt=attempt))
        self.assertEqual(row.status, "queued")
        self.assertEqual(row.attempts, 2)
        self.assertIsNone(row.lease_owner)
        self.assertEqual(self.db.scalars(select(JobAttempt)).all(), [])

    def test_claim_rechecks_cooldown_set_after_dispatch(self):
        self.account.cooldown_until = None
        self.db.commit()
        row = self.job()
        self.assertEqual(self.dispatch(), [(row.id, row.kind)])
        self.account.cooldown_until = self.now + timedelta(hours=1)
        self.db.commit()
        self.assertIsNone(jobs.claim(self.db, row.id, "worker", attempt=JobAttempt(job_id=row.id)))
        self.assertEqual(row.attempts, 2)
        self.assertEqual(self.db.scalars(select(JobAttempt)).all(), [])

    def test_expired_cooldown_dispatches_and_claims_normally(self):
        row = self.job()
        self.assertEqual(self.dispatch(), [])
        resumed = self.now + timedelta(hours=2, seconds=1)
        self.assertEqual(self.dispatch(resumed), [(row.id, row.kind)])
        with patch("app.jobs.utcnow", return_value=resumed):
            claimed = jobs.claim(self.db, row.id, "worker", attempt=JobAttempt(job_id=row.id))
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, "running")
        self.assertEqual(claimed.attempts, 3)
        self.assertIsNone(claimed.error)
        self.assertEqual(len(self.db.scalars(select(JobAttempt)).all()), 1)

    def test_paused_running_and_longer_delays_are_preserved(self):
        paused = self.job(status="paused", error="manual pause")
        running = self.job(status="running", lease_owner="worker",
            lease_expires_at=self.now + timedelta(hours=1), error=None)
        delayed = self.job(available_at=self.now + timedelta(hours=4), error="other retry")
        self.assertEqual(self.dispatch(), [])
        self.assertEqual(paused.status, "paused")
        self.assertEqual(paused.error, "manual pause")
        self.assertEqual(running.status, "running")
        self.assertEqual(running.lease_owner, "worker")
        self.assertEqual(aware(delayed.available_at), self.now + timedelta(hours=4))
        self.assertEqual(delayed.error, "other retry")
        self.assertEqual([paused.attempts, running.attempts, delayed.attempts], [2, 2, 2])

    def test_legacy_credential_job_uses_target_account(self):
        row = self.job("refresh_credentials", account_id=None, target_id=self.account.id)
        self.assertIsNone(jobs.claim(self.db, row.id, "worker", attempt=JobAttempt(job_id=row.id)))
        self.assertEqual(self.dispatch(), [])
        self.assertEqual(aware(row.available_at), aware(self.account.cooldown_until))
        self.assertEqual(row.attempts, 2)

    def test_scheduled_scans_wait_until_cooldown_without_creating_jobs(self):
        source = SourceSubscription(collection_id="collection", account_id=self.account.id,
            enabled=True, interval_minutes=10, next_run_at=self.now - timedelta(minutes=1))
        self.db.add(source)
        self.db.commit()
        with patch("app.jobs.utcnow", return_value=self.now):
            jobs.schedule_due(self.db)
        self.assertEqual(aware(source.next_run_at), aware(self.account.cooldown_until))
        self.assertEqual(self.db.scalars(select(Job)).all(), [])
        with patch("app.jobs.utcnow", return_value=self.now + timedelta(hours=2, seconds=1)):
            jobs.schedule_due(self.db)
        queued = self.db.scalars(select(Job)).all()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].kind, "scan_collection")


if __name__ == "__main__":
    unittest.main()
