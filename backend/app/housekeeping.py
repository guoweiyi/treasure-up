"""Bound the lifetime of transient playback, login and route-probe records."""
from datetime import timedelta

from sqlalchemy import delete, select

from app.models import PlaybackSession, SourceAuthorization, StorageObservation, UserSession, utcnow


def prune_transient_records(db, *, now=None, batch_size=1000):
    now = now or utcnow()
    batch_size = min(1000, max(1, batch_size))
    counts = {}
    for model, clock, cutoff in (
        (SourceAuthorization, SourceAuthorization.expires_at, now),
        (PlaybackSession, PlaybackSession.expires_at, now - timedelta(hours=1)),
        (UserSession, UserSession.expires_at, now - timedelta(days=1)),
        (StorageObservation, StorageObservation.measured_at, now - timedelta(days=7)),
    ):
        # Expiry/measurement columns are indexed. Keep each transaction small;
        # a busy row must never hold up the collection scheduler.
        ids = select(model.id).where(clock < cutoff).order_by(clock, model.id).limit(batch_size).with_for_update(skip_locked=True)
        result = db.execute(delete(model).where(model.id.in_(ids)), execution_options={"synchronize_session": False})
        counts[model.__tablename__] = result.rowcount
        db.commit()
    return counts
