"""Replica mutations share a content lock across upload checkpoint commits.

Lock order: backup GC guard (destructive operations only), content lock, asset
row lock. The row lock in registration keeps publication serialized until the
caller's outer commit, after the short-lived content-lock context exits.
"""
import hashlib
import threading
import time
from contextlib import contextmanager

from sqlalchemy import text

from .base import StorageError, content_key


class StorageBusy(StorageError):
    retryable = True


_registry_guard = threading.Lock()
_locks = {}


def _lock_key(sha256):
    content_key(sha256)  # Validate before deriving a shared lock identity.
    return int.from_bytes(hashlib.sha256(("treasure-replica-v1:" + sha256).encode("ascii")).digest()[:8], "big", signed=True)


@contextmanager
def content_lock(db, sha256, *, timeout=5.0):
    """Use a dedicated PG connection: db.commit() cannot release this lock."""
    key = _lock_key(sha256)
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        engine = getattr(bind, "engine", bind)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            acquired = False
            try:
                deadline = time.monotonic() + timeout
                while not acquired:
                    acquired = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}))
                    if acquired:
                        break
                    if time.monotonic() >= deadline:
                        raise StorageBusy("Another operation is changing this asset; retry shortly")
                    time.sleep(0.05)
                yield
            finally:
                if acquired:
                    try:
                        released = connection.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                        if not released:
                            connection.invalidate()
                    except Exception:
                        # Never return a connection holding a session lock to the pool.
                        connection.invalidate()
                        raise
        return
    # SQLite is for local tests; all threads touching the content share one mutex.
    with _registry_guard:
        entry = _locks.setdefault(key, [threading.RLock(), 0])
        entry[1] += 1
    acquired = False
    try:
        acquired = entry[0].acquire(timeout=timeout)
        if not acquired:
            raise StorageBusy("Another operation is changing this asset; retry shortly")
        yield
    finally:
        if acquired:
            entry[0].release()
        with _registry_guard:
            entry[1] -= 1
            if entry[1] == 0:
                _locks.pop(key, None)
