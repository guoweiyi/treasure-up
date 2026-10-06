"""Issue cloud read URLs from verified replica records, without per-fragment HEADs."""
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from app.models import AssetLocation, StorageProfile, utcnow
from .base import StorageError
from .service import get_adapter


def direct_profile(db, profile_id, *, bulk=False):
    profile = db.get(StorageProfile, profile_id)
    if profile is None or not profile.enabled:
        raise StorageError("Playback storage node is unavailable")
    if profile.kind == "local" or (profile.config or {}).get("delivery_mode") == "redirect":
        return None
    # Graph must fetch a download URL per object. Resolve requested fragments
    # lazily through redirects instead of hundreds of API calls before playback.
    if bulk and profile.kind in {"onedrive", "onedrive_cn", "sharepoint", "sharepoint_cn"}:
        return None
    return profile


def validate_delivery_url(value):
    """Provider URLs are capabilities; never log them or accept executable URIs."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username or parsed.password or parsed.fragment or
                (port is not None and not 1 <= port <= 65535) or
                any(ord(c) < 32 or c in '\\"' for c in value)):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise StorageError("Storage did not return a valid playback URL") from None
    return value


def direct_urls(db, profile, asset_ids, *, expires_in=3600):
    """One replica query and one adapter per playlist, irrespective of its length.

    These immutable objects were hash-verified during publication. HEADing every
    fragment before signing duplicates object requests and delays playback. A
    missing remote object is handled by the player's bounded retry/node switch.
    """
    ids = frozenset(asset_ids)
    if not ids:
        raise StorageError("Playback contains no assets")
    rows = db.scalars(select(AssetLocation).where(
        AssetLocation.storage_profile_id == profile.id,
        AssetLocation.asset_id.in_(ids), AssetLocation.state == "ready",
        AssetLocation.verified_at.is_not(None)).order_by(AssetLocation.id))
    locations = {}
    for row in rows:
        locations.setdefault(row.asset_id, row)
    if locations.keys() != ids:
        raise StorageError("Storage node does not contain the complete verified playback")
    maximum = 900 if profile.kind in {"onedrive", "onedrive_cn", "sharepoint", "sharepoint_cn"} else 3600
    ttl = max(1, min(maximum, int(expires_in)))
    adapter = get_adapter(profile)
    urls = {}
    try:
        for asset_id, location in locations.items():
            urls[asset_id] = validate_delivery_url(adapter.presign(
                location.object_key, ttl, version_id=location.version_id))
    except Exception:
        # Provider exceptions can include credential-bearing URLs and headers.
        raise StorageError("Storage could not issue direct playback addresses") from None
    return urls, utcnow() + timedelta(seconds=ttl)
