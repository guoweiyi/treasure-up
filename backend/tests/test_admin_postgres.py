"""Opt-in administrator row-lock/FK regression against isolated PostgreSQL."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import main, schemas
from app.models import AuditLog, User, UserSession
from app.security import create_session, hash_password, verify_password
from test_postgres_integration import postgres_workspace  # noqa: F401; random isolated databases


pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit isolated PostgreSQL test opt-in required")


def test_admins_can_update_each_other_without_audit_fk_deadlock(postgres_workspace, monkeypatch):
    space = postgres_workspace
    with space.sessions() as db:
        original_hash = hash_password("synthetic-original-password")
        first = User(username="admin-first", password_hash=original_hash, role="admin")
        second = User(username="admin-second", password_hash=original_hash, role="admin")
        unrelated = User(username="unrelated-reader", password_hash=original_hash, role="reader")
        db.add_all([first, second, unrelated]); db.flush()
        first_id, second_id, unrelated_id = first.id, second.id, unrelated.id
        for user in (first, second, unrelated):
            create_session(db, user)
            create_session(db, user)
        db.commit()

    reached_audit, release_audit = threading.Barrier(3), threading.Event()
    arrivals = Queue()
    original_audit = main.audit

    def synchronized_audit(db, actor, action, kind, identifier=None, details=None):
        assert action == "update" and kind == "user"
        # update_user has already locked and updated its target and deleted that
        # target's sessions. Pause before either actor FK check can commit.
        arrivals.put((db.scalar(text("SELECT pg_backend_pid()")), actor.id, identifier))
        reached_audit.wait(timeout=10)
        assert release_audit.wait(timeout=10)
        original_audit(db, actor, action, kind, identifier, details)

    monkeypatch.setattr(main, "audit", synchronized_audit)
    passwords = {first_id: "first-admin-replacement-password", second_id: "second-admin-replacement-password"}

    def update(actor_id, target_id):
        with space.sessions() as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            db.execute(text("SET LOCAL statement_timeout = '15s'"))
            actor = db.get(User, actor_id)
            return main.update_user(target_id, schemas.UserUpdate(password=passwords[target_id]), user=actor, db=db)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(update, first_id, second_id), executor.submit(update, second_id, first_id)]
        try:
            reached_audit.wait(timeout=10)
            participants = [arrivals.get(timeout=1) for _ in range(2)]
            assert len({pid for pid, _, _ in participants}) == 2
            assert {(actor, target) for _, actor, target in participants} == {
                (first_id, second_id), (second_id, first_id)}
            # Prove both target locks are genuinely held in independent DB
            # transactions; a thread barrier alone is not lock evidence.
            for target_id in (first_id, second_id):
                with space.sessions() as observer:
                    with pytest.raises(DBAPIError) as blocked:
                        observer.scalar(select(User.id).where(User.id == target_id).with_for_update(nowait=True))
                    assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
                    observer.rollback()
        finally:
            release_audit.set()  # Release before joining, including failed assertions.
        results = [future.result(timeout=15) for future in futures]

    assert {result["id"] for result in results} == {first_id, second_id}
    with space.sessions() as db:
        for user_id, password in passwords.items():
            user = db.get(User, user_id)
            assert verify_password(password, user.password_hash)
            assert user.role == "admin" and not user.disabled
            assert db.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id)) == 0
        assert db.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == unrelated_id)) == 2
        audits = list(db.scalars(select(AuditLog).where(AuditLog.action == "update", AuditLog.entity_type == "user")))
        assert {(row.actor_id, row.entity_id) for row in audits} == {(first_id, second_id), (second_id, first_id)}
        assert len(audits) == 2 and all(row.details == {"fields": ["password"]} for row in audits)
