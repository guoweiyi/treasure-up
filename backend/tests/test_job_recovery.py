from datetime import timedelta

import pytest
from sqlalchemy import event, func, select, update

from app import jobs
from app.models import Job, JobAttempt, OutboxEvent, utcnow
from test_jobs import job_sessions, new_job, reclaim


def test_publication_never_consumes_a_new_continuation_created_during_send(job_sessions):
    job_id = new_job(job_sessions)
    created_during_send = []
    def send(*_):
        # A fast worker can complete and enqueue a new slice before the broker
        # publisher finishes confirming its previous delivery.
        with job_sessions() as worker:
            pending = OutboxEvent(job_id=job_id)
            worker.add(pending); worker.commit()
            created_during_send.append(pending.id)
    with job_sessions() as db:
        jobs.recover_and_dispatch(db, send)
        assert db.get(OutboxEvent, created_during_send[0]).published_at is None
        sent = []
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        assert sent == [(job_id, 'archive_video')]


def test_partly_failed_broker_batch_confirms_only_successful_messages(job_sessions):
    identifiers = [new_job(job_sessions) for _ in range(3)]
    sent = []
    def send(job_id, kind):
        if len(sent) == 1:
            raise ConnectionError('fixture broker failure')
        sent.append(job_id)
    with job_sessions() as db:
        with pytest.raises(ConnectionError): jobs.recover_and_dispatch(db, send)
    with job_sessions() as db:
        rows = db.execute(select(OutboxEvent.job_id, OutboxEvent.published_at)).all()
        assert [job_id for job_id, published_at in rows if published_at] == sent
        remaining = []
        jobs.recover_and_dispatch(db, lambda job_id, kind: remaining.append(job_id))
        assert set(remaining) == set(identifiers) - set(sent)


def test_dispatch_projects_small_fields_batches_writes_and_releases_connection(job_sessions):
    with job_sessions() as db:
        for index in range(20):
            item = jobs.enqueue(db, 'refresh_stats', str(index))
            item.checkpoint = {'large': 'x' * 20000}
        db.commit()
        statements, commits = [], []
        engine = db.get_bind()
        def query(_conn, _cursor, statement, _parameters, _ctx, _many): statements.append(statement)
        def committed(_db): commits.append(True)
        event.listen(engine, 'before_cursor_execute', query)
        event.listen(db, 'after_commit', committed)
        try:
            def send(*_):
                assert not db.in_transaction(), 'broker calls must release the DB transaction/connection'
            jobs.recover_and_dispatch(db, send)
        finally:
            event.remove(engine, 'before_cursor_execute', query)
            event.remove(db, 'after_commit', committed)
        assert not any('jobs.checkpoint' in statement for statement in statements)
        assert len(statements) <= 8 and len(commits) <= 3


@pytest.mark.parametrize('prior_status,attempts,expected', [
    ('running', 1, 'queued'), ('running', 3, 'failed'), ('paused', 1, 'paused'), ('cancelled', 1, 'cancelled')])
def test_restart_closes_lost_attempt_without_losing_checkpoint(job_sessions, prior_status, attempts, expected):
    job_id = new_job(job_sessions)
    with job_sessions() as db:
        job = jobs.claim(db, job_id, 'gone-worker')
        job.status, job.attempts = prior_status, attempts
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        job.checkpoint = {'part': 9}
        record = JobAttempt(job_id=job_id)
        db.add(record); db.commit()
        jobs.recover_and_dispatch(db, lambda *_: None)
        db.expire_all()
        assert job.status == expected and job.lease_owner is None and job.lease_expires_at is None
        assert job.checkpoint == {'part': 9}
        assert (job.finished_at is not None) == (expected in {'failed', 'cancelled'})
        assert record.status == (prior_status if prior_status != 'running' else 'lease_lost')
        assert record.finished_at is not None


def test_recovery_batch_is_bounded_and_eventually_drains(job_sessions):
    with job_sessions() as db:
        for index in range(105):
            item = jobs.enqueue(db, 'refresh_stats', str(index))
            item.status, item.attempts = 'running', 1
            item.lease_expires_at = utcnow() - timedelta(seconds=1)
            item.lease_owner = 'gone'
        db.commit()
        jobs.recover_and_dispatch(db, lambda *_: None)
        assert db.scalar(select(func.count()).select_from(Job).where(Job.status == 'running')) == 5
        jobs.recover_and_dispatch(db, lambda *_: None)
        assert db.scalar(select(func.count()).select_from(Job).where(Job.status == 'running')) == 0


def test_attempt_record_failure_rolls_back_claim_atomically(job_sessions):
    job_id = new_job(job_sessions)
    def fail(*_): raise RuntimeError('fixture insert failure')
    event.listen(JobAttempt, 'before_insert', fail)
    try:
        with pytest.raises(RuntimeError, match='fixture insert failure'): jobs.run_job_id(job_id)
    finally: event.remove(JobAttempt, 'before_insert', fail)
    with job_sessions() as db:
        job = db.get(Job, job_id)
        assert job.status == 'queued' and job.attempts == 0 and job.lease_owner is None


def test_failed_publication_confirmation_keeps_events_recoverable(job_sessions):
    job_id = new_job(job_sessions)
    sent = []
    engine = job_sessions.kw['bind']
    def fail_confirmation(_conn, _cursor, statement, _params, _ctx, _many):
        if statement.startswith('UPDATE outbox_events'):
            raise RuntimeError('fixture confirmation failure')
    event.listen(engine, 'before_cursor_execute', fail_confirmation)
    try:
        with job_sessions() as db:
            with pytest.raises(RuntimeError, match='confirmation failure'):
                jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
    finally: event.remove(engine, 'before_cursor_execute', fail_confirmation)
    with job_sessions() as db:
        assert db.scalar(select(OutboxEvent.published_at)) is None
        jobs.recover_and_dispatch(db, lambda *message: sent.append(message))
        assert sent == [(job_id, 'archive_video')] * 2
        assert jobs.claim(db, job_id, 'winner') is not None
    with job_sessions() as db:
        assert jobs.claim(db, job_id, 'duplicate') is None


@pytest.mark.parametrize('manual', [True, False])
def test_credential_refresh_dispatch_keeps_manual_intent_and_lease_guard(job_sessions, monkeypatch, manual):
    job_id = new_job(job_sessions, kind='refresh_credentials', policy={'manual': manual})
    calls = []
    def refresh(db, account_id, *, manual, check_active):
        check_active()
        calls.append((account_id, manual))
        return {'checked': True}
    monkeypatch.setattr('app.source_credentials.refresh_account', refresh)
    monkeypatch.setattr('app.ingest.runner.run_job', lambda *args: pytest.fail('renewal entered ordinary ingest lane'))
    assert jobs.run_job_id(job_id)['status'] == 'succeeded'
    assert calls == [('target', manual)]


def test_credential_refresh_guard_stops_cancelled_work(job_sessions, monkeypatch):
    job_id = new_job(job_sessions, kind='refresh_credentials')
    def refresh(db, account_id, *, manual, check_active):
        check_active()
        with job_sessions() as controller:
            controller.execute(update(Job).where(Job.id == job_id).values(status='cancelled'))
            controller.commit()
        check_active()
        pytest.fail('cancelled credential rotation continued')
    monkeypatch.setattr('app.source_credentials.refresh_account', refresh)
    assert jobs.run_job_id(job_id)['status'] == 'cancelled'


def test_recovered_attempt_history_cannot_be_overwritten_by_old_worker_error(job_sessions, monkeypatch):
    job_id = new_job(job_sessions)
    def stale(db, job):
        reclaim(job_sessions, job_id)
        raise RuntimeError('old worker failed after recovery')
    monkeypatch.setattr('app.ingest.runner.run_job', stale)
    assert jobs.run_job_id(job_id)['status'] == 'lease_lost'
    with job_sessions() as db:
        record = db.scalar(select(JobAttempt))
        assert record.status == 'lease_lost' and record.error == '执行进程租约到期，已保存检查点'
