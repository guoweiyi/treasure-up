from datetime import timedelta

from sqlalchemy import func, select, update

from app import jobs
from app.models import Job, JobAttempt, utcnow
from test_postgres_integration import postgres_workspace, pytestmark
from test_job_recovery import (
    test_publication_never_consumes_a_new_continuation_created_during_send as check_fast_continuation,
    test_failed_publication_confirmation_keeps_events_recoverable as check_failed_confirmation,
    test_attempt_record_failure_rolls_back_claim_atomically as check_atomic_claim,
    test_recovered_attempt_history_cannot_be_overwritten_by_old_worker_error as check_recovered_history,
)
from test_jobs import new_job


def test_postgres_fast_continuation_keeps_its_own_delivery_event(postgres_workspace):
    check_fast_continuation(postgres_workspace.sessions)


def test_postgres_confirmation_failure_repeats_hints_but_not_claims(postgres_workspace):
    check_failed_confirmation(postgres_workspace.sessions)


def test_postgres_attempt_and_claim_commit_together(postgres_workspace):
    check_atomic_claim(postgres_workspace.sessions)


def test_postgres_stale_worker_preserves_recovered_attempt_history(postgres_workspace, monkeypatch):
    check_recovered_history(postgres_workspace.sessions, monkeypatch)


def test_recovery_skips_locked_jobs_and_closes_old_attempts(postgres_workspace):
    sessions = postgres_workspace.sessions
    ids = [new_job(sessions) for _ in range(2)]
    with sessions() as db:
        db.execute(update(Job).where(Job.id.in_(ids)).values(status='running', attempts=1, lease_owner='gone',
            lease_expires_at=utcnow() - timedelta(seconds=1), checkpoint={'preserved': True}))
        db.add_all(JobAttempt(job_id=identity) for identity in ids)
        db.commit()
    with sessions() as locked:
        locked.execute(select(Job.id).where(Job.id == ids[0]).with_for_update())
        with sessions() as db:
            jobs.recover_and_dispatch(db, lambda *_: None)
            assert db.get(Job, ids[0]).status == 'running'
            assert db.get(Job, ids[1]).status == 'queued'
        locked.rollback()
    with sessions() as db:
        jobs.recover_and_dispatch(db, lambda *_: None)
        assert all(row.status == 'queued' and row.checkpoint == {'preserved': True} for row in db.scalars(select(Job)))
        assert all(row.status == 'lease_lost' and row.finished_at for row in db.scalars(select(JobAttempt)))


def test_renew_and_claim_use_the_same_database_clock_as_the_lease_fence(postgres_workspace, monkeypatch):
    sessions = postgres_workspace.sessions
    job_id = new_job(sessions)
    # Deterministic equivalent of a worker clock lagging behind the DB or time
    # passing while UPDATE waits for a checkpoint transaction's row lock.
    monkeypatch.setattr(jobs, 'utcnow', lambda: utcnow() - timedelta(minutes=10))
    with sessions() as db:
        job = jobs.claim(db, job_id, 'owner')
        assert job is not None
        assert job.lease_expires_at > db.scalar(select(func.clock_timestamp()))
        assert jobs.renew_lease(db, job_id, 'owner')
        db.refresh(job)
        assert job.lease_expires_at > db.scalar(select(func.clock_timestamp()))
        assert jobs.ensure_active(db, job_id, 'owner') == {}
