"""Cross-worker bounds on playback rows and their three possible probe reports."""
import hashlib
import ipaddress
import math
import threading
from contextlib import contextmanager
from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, true

from app.models import PlaybackSession, StorageObservation, utcnow


WINDOW_SECONDS = 60
IDENTITY_ACTIVE_LIMIT = 256
IDENTITY_WINDOW_LIMIT = 30
CLIENT_ACTIVE_LIMIT = 512
CLIENT_WINDOW_LIMIT = 120
GLOBAL_ACTIVE_LIMIT = 10_000
GLOBAL_WINDOW_LIMIT = 300
CREATION_LOCK = 87213004
_sqlite_mutex = threading.RLock()


def client_hash(request):
    # The ASGI server, with a restricted trusted-proxy configuration, owns any
    # forwarding-header interpretation. Never trust a header directly here.
    host = request.client.host if request.client else "unknown"
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address):
            address = address.ipv4_mapped or ipaddress.ip_network(f"{address}/64", strict=False)
        host = str(address)
    except ValueError:
        pass
    return hashlib.sha256(host.encode()).hexdigest()


@contextmanager
def creation_lock(db):
    if db.get_bind().dialect.name == "postgresql":
        # The quota check and the insert must share this transaction. A single
        # global lock also serializes the global quota across different clients.
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CREATION_LOCK})
        yield
    else:
        # SQLite is the explicitly enabled, single-process development backend.
        with _sqlite_mutex:
            yield


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _check_quota(db, user, digest):
    now = utcnow()
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    own = (PlaybackSession.user_id == user.id) if user.id else (
        PlaybackSession.user_id.is_(None) & (PlaybackSession.state["guest_hash"].as_string() == user.guest_hash))
    scopes = (
        (own, IDENTITY_ACTIVE_LIMIT, IDENTITY_WINDOW_LIMIT),
        (PlaybackSession.state["client_hash"].as_string() == digest, CLIENT_ACTIVE_LIMIT, CLIENT_WINDOW_LIMIT),
        (true(), GLOBAL_ACTIVE_LIMIT, GLOBAL_WINDOW_LIMIT),
    )
    fields = []
    for scope, _, _ in scopes:
        active = scope & (PlaybackSession.expires_at > now)
        recent = scope & (PlaybackSession.created_at > cutoff)
        fields.extend((func.count().filter(active), func.min(PlaybackSession.expires_at).filter(active),
                       func.count().filter(recent), func.min(PlaybackSession.created_at).filter(recent)))
    # Every new session expires four hours after creation. Thus this indexed
    # expiry range contains every recent creation, including newly expired rows,
    # without scanning the historical rows awaiting batched housekeeping.
    values = db.execute(select(*fields).where(PlaybackSession.expires_at > cutoff)).one()
    retry = 0
    for index, (_, active_limit, window_limit) in enumerate(scopes):
        active_count, earliest_expiry, recent_count, earliest_creation = values[index * 4:index * 4 + 4]
        if active_count >= active_limit:
            retry = max(retry, math.ceil((_utc(earliest_expiry) - now).total_seconds()))
        if recent_count >= window_limit:
            retry = max(retry, math.ceil((_utc(earliest_creation) + timedelta(seconds=WINDOW_SECONDS) - now).total_seconds()))
    if retry:
        raise HTTPException(429, "播放会话创建过于频繁，请稍后再试", headers={"Retry-After": str(max(1, retry))})


def check_playback_creation(db, request, user):
    """Cheap early rejection before loading HLS indexes or resolving routes."""
    digest = client_hash(request)
    _check_quota(db, user, digest)
    return digest


def _prune_old_records(db):
    # Creation remains bounded even if the periodic scheduler is stopped. Each
    # successful creation removes up to eight stale sessions and 24 stale probe
    # reports, exceeding its one new session and at most three new reports.
    # Keep this in the insertion transaction; never commit/release its lock here.
    now = utcnow()
    for model, clock, cutoff, batch_size in (
        (PlaybackSession, PlaybackSession.expires_at, now - timedelta(hours=1), 8),
        (StorageObservation, StorageObservation.measured_at, now - timedelta(days=7), 24),
    ):
        ids = (select(model.id).where(clock < cutoff).order_by(clock, model.id)
               .limit(batch_size).with_for_update(skip_locked=True))
        db.execute(delete(model).where(model.id.in_(ids)), execution_options={"synchronize_session": False})


@contextmanager
def playback_creation_slot(db, user, digest):
    """Caller must persist client_hash and commit its new session before exit."""
    with creation_lock(db):
        try:
            _check_quota(db, user, digest)
            _prune_old_records(db)
            yield
        except BaseException:
            db.rollback()
            raise
