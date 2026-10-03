from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models import Asset, AssetLocation, PlaybackSession, StorageObservation, StorageProfile
from .base import IntegrityError, ObjectMissing, StorageError
from .local import LocalStorage
from .service import asset_locations, get_adapter

MEASUREMENT_TTL_SECONDS = 600
MAX_PROBE_BYTES = 128 * 1024
MAX_SESSION_SWITCHES = 2


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _now():
    return datetime.now(timezone.utc)


def complete_profiles(db, asset_ids):
    ids = list(dict.fromkeys(asset_ids))
    if not ids:
        return []
    complete = select(AssetLocation.storage_profile_id).where(AssetLocation.asset_id.in_(ids),
        AssetLocation.state == "ready", AssetLocation.verified_at.is_not(None)).group_by(
        AssetLocation.storage_profile_id).having(func.count(func.distinct(AssetLocation.asset_id)) == len(ids))
    return list(db.scalars(select(StorageProfile).where(StorageProfile.id.in_(complete), StorageProfile.enabled.is_(True))))


def playback_routes(db, asset_ids, *, user_id=None):
    """One DB completeness query, no remote HEAD or per-segment network probing."""
    profiles = complete_profiles(db, asset_ids)
    if not profiles:
        return []
    observations = list(db.scalars(select(StorageObservation).where(
        StorageObservation.profile_id.in_([p.id for p in profiles]),
        StorageObservation.measured_at >= _now() - timedelta(seconds=MEASUREMENT_TTL_SECONDS),
        ((StorageObservation.scope == "server_storage") & StorageObservation.user_id.is_(None)) |
        ((StorageObservation.scope == "browser_delivery") & (StorageObservation.user_id == user_id) & StorageObservation.user_id.is_not(None))
    ).order_by(StorageObservation.measured_at.desc()).limit(max(1000, len(profiles) * 10))))
    latest = {}
    for observation in observations:
        latest.setdefault((observation.profile_id, observation.scope), observation)
    result = []
    for profile in profiles:
        sample = latest.get((profile.id, "browser_delivery")) or latest.get((profile.id, "server_storage"))
        speed = None
        if sample and sample.succeeded and sample.bytes_read > 0 and sample.elapsed_ms > 0:
            speed = sample.bytes_read * 1000.0 / sample.elapsed_ms
        result.append({"id": profile.id, "name": profile.name, "provider": profile.kind,
            "status": "unmeasured" if sample is None else ("available" if sample.succeeded else "unavailable"),
            "latency_ms": sample.latency_ms if sample and sample.succeeded else None,
            "throughput_bps": speed,
            "measurement_scope": sample.scope if sample else None,
            "measured_at": _utc(sample.measured_at).isoformat() if sample else None,
            "priority": int((profile.config or {}).get("read_priority", 100)),
            "measurement_ttl_seconds": MEASUREMENT_TTL_SECONDS})
    return sorted(result, key=_route_score)


def _route_score(route):
    # Browser observations only compare with this user's browser observations.
    # Server-side measurements are an explicitly labelled fallback, not client QoE.
    status = route["status"]
    category = 0 if route["measurement_scope"] == "browser_delivery" and status == "available" else 1 if status == "available" else 2 if status == "unmeasured" else 3
    speed = route["throughput_bps"]
    cost_ms = route["latency_ms"] + (1024**2 / speed * 1000) if speed and route["latency_ms"] is not None else math.inf
    return category, cost_ms, route["priority"], route["id"]


def select_route(db, asset_ids, *, route_id=None, user_id=None, excluded=None):
    routes = [r for r in playback_routes(db, asset_ids, user_id=user_id) if r["id"] not in set(excluded or [])]
    if route_id:
        if any(r["id"] == route_id for r in routes):
            return route_id
        raise StorageError("Selected storage node does not have a complete verified copy")
    if not routes:
        raise StorageError("No complete storage node is available")
    return routes[0]["id"]


def record_observation(db, profile_id, *, asset_id=None, user_id=None, scope="browser_delivery",
                       latency_ms, elapsed_ms, bytes_read, succeeded):
    if scope not in {"server_storage", "browser_delivery"} or (scope == "browser_delivery") != (user_id is not None):
        raise StorageError("Invalid observation scope")
    if not all(isinstance(x, (float, int)) and math.isfinite(x) and 0 <= x <= 120000 for x in (latency_ms, elapsed_ms)):
        raise StorageError("Invalid storage timing")
    if not isinstance(bytes_read, int) or not 0 <= bytes_read <= MAX_PROBE_BYTES or (succeeded and (elapsed_ms <= 0 or bytes_read <= 0)):
        raise StorageError("Invalid probe byte count")
    if not db.get(StorageProfile, profile_id):
        raise ObjectMissing("Storage node does not exist")
    sample = StorageObservation(profile_id=profile_id, asset_id=asset_id, user_id=user_id, scope=scope,
        latency_ms=latency_ms, elapsed_ms=elapsed_ms, bytes_read=bytes_read, succeeded=bool(succeeded))
    db.add(sample)
    db.flush()
    return sample


def probe_routes(db, asset_id, *, max_candidates=3):
    """Explicit background probe only. At most 128 KiB from each of three replicas."""
    asset = db.get(Asset, asset_id)
    if not asset or asset.size <= 0:
        raise ObjectMissing("A nonempty asset is required for measurement")
    for location, profile in asset_locations(db, asset_id)[:max(0, min(3, max_candidates))]:
        started, latency, count, succeeded = time.perf_counter(), 0.0, 0, False
        try:
            adapter = get_adapter(profile)
            info = adapter.head(location.object_key, version_id=location.version_id)
            latency = (time.perf_counter() - started) * 1000
            if info.size != asset.size:
                raise IntegrityError("Replica size mismatch")
            expected = min(MAX_PROBE_BYTES, asset.size)
            payload = adapter.read_range(location.object_key, 0, expected - 1, version_id=location.version_id)
            if len(payload) != expected:
                raise IntegrityError("Probe byte count mismatch")
            count, succeeded = len(payload), True
        except Exception:
            pass
        elapsed = max(0.001, (time.perf_counter() - started) * 1000)
        record_observation(db, profile.id, asset_id=asset_id, scope="server_storage", latency_ms=min(latency or elapsed, 120000),
            elapsed_ms=min(elapsed, 120000), bytes_read=count, succeeded=succeeded)
    return playback_routes(db, [asset_id])


def resolve_on_profile(db, asset_id, profile_id, *, expires_in=300):
    asset, profile = db.get(Asset, asset_id), db.get(StorageProfile, profile_id)
    if not asset or not profile or not profile.enabled:
        raise ObjectMissing("Asset or node is unavailable")
    locations = db.scalars(select(AssetLocation).where(AssetLocation.asset_id == asset_id,
        AssetLocation.storage_profile_id == profile_id, AssetLocation.state == "ready", AssetLocation.verified_at.is_not(None)))
    for location in locations:
        try:
            adapter = get_adapter(profile)
            if adapter.head(location.object_key, version_id=location.version_id).size != asset.size:
                continue
            result = {"asset_id": asset.id, "size": asset.size, "mime_type": asset.mime_type, "sha256": asset.sha256,
                      "location_id": location.id, "profile_id": profile.id, "kind": profile.kind}
            if isinstance(adapter, LocalStorage):
                result.update(path=adapter.path_for(location.object_key), expires_at=None)
            else:
                ttl = max(1, min(3600, int(expires_in)))
                result.update(url=adapter.presign(location.object_key, ttl, version_id=location.version_id), expires_at=_now() + timedelta(seconds=ttl))
            return result
        except Exception:
            continue
    raise StorageError("Selected storage node cannot serve this asset")


def resolve_session_asset(db, session, asset_id, allowed_asset_ids):
    allowed = set(allowed_asset_ids)
    if asset_id not in allowed or _utc(session.expires_at) <= _now():
        raise StorageError("Playback session does not authorize this asset")
    # Caller authenticates ownership; row lock serializes concurrent fragment failovers.
    db.scalar(select(PlaybackSession).where(PlaybackSession.id == session.id).with_for_update().execution_options(populate_existing=True))
    state = dict(session.state or {})
    failed = list(state.get("failed_profile_ids", []))
    switches = int(state.get("switch_count", 0))
    if not session.profile_id:
        session.profile_id = select_route(db, allowed, user_id=session.user_id, excluded=failed)
    while True:
        try:
            result = resolve_on_profile(db, asset_id, session.profile_id)
            db.flush()
            return result
        except StorageError:
            if session.profile_id not in failed:
                failed.append(session.profile_id)
            state.update(failed_profile_ids=failed, switch_count=switches)
            session.state = dict(state)
            if switches >= MAX_SESSION_SWITCHES:
                db.flush()
                raise StorageError("Playback storage failover limit reached") from None
            replacement = select_route(db, allowed, user_id=session.user_id, excluded=failed)
            switches += 1
            state["switch_count"] = switches
            session.profile_id, session.state = replacement, dict(state)
