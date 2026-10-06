"""Replica lifecycle, including explicit guarded deletion of retired objects."""
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models import Asset, AssetLocation, AssetRef, BackupSet, StorageProfile
from .base import ObjectMissing, StorageError, safe_key
from .naming import is_managed_key
from .service import get_adapter, utcnow
from .locking import content_lock


def dependency_asset_ids(db, asset_ids):
    visited, pending = set(), set(asset_ids)
    while pending:
        found = set(db.scalars(select(Asset.id).where(Asset.id.in_(pending))))
        if found != pending:
            raise ObjectMissing("Selected asset does not exist")
        visited.update(pending)
        children = set(db.scalars(select(AssetRef.asset_id).where(AssetRef.entity_type == "asset", AssetRef.entity_id.in_(pending))))
        pending = children - visited
    return sorted(visited)


def _lock_asset(db, location_id):
    location = db.get(AssetLocation, location_id)
    if not location:
        raise ObjectMissing("Storage location does not exist")
    asset = db.scalar(select(Asset).where(Asset.id == location.asset_id).with_for_update().execution_options(populate_existing=True))
    db.refresh(location)
    return asset, location


def _content_sha(db, location_id):
    # Scalar SQL avoids a stale Session identity-map state after waiting for locks.
    sha = db.scalar(select(Asset.sha256).join(AssetLocation, AssetLocation.asset_id == Asset.id)
                    .where(AssetLocation.id == location_id))
    if not sha:
        raise ObjectMissing("Storage location does not exist")
    return sha


def retire_location(db, location_id):
    with content_lock(db, _content_sha(db, location_id)):
        return _retire_locked(db, location_id)


def _retire_locked(db, location_id):
    asset, location = _lock_asset(db, location_id)
    if location.state == "retired":
        return location
    if location.state == "deleted":
        raise StorageError("A physically deleted location cannot be retired or restored")
    candidates = db.execute(select(AssetLocation, StorageProfile).join(StorageProfile,
        StorageProfile.id == AssetLocation.storage_profile_id).where(AssetLocation.asset_id == asset.id,
        AssetLocation.id != location.id, AssetLocation.state == "ready", AssetLocation.verified_at.is_not(None),
        StorageProfile.enabled.is_(True)))
    for replica, profile in candidates:
        try:
            get_adapter(profile).verify(replica.object_key, asset.sha256, asset.size, version_id=replica.version_id)
        except Exception:
            continue
        replica.checksum, replica.verified_at = asset.sha256, utcnow()
        location.state = "retired"
        db.flush()
        return location
    raise StorageError("Cannot retire the last verified readable replica")


def restore_location(db, location_id):
    with content_lock(db, _content_sha(db, location_id)):
        return _restore_locked(db, location_id)


def _restore_locked(db, location_id):
    asset, location = _lock_asset(db, location_id)
    if location.state == "deleted":
        raise StorageError("A physically deleted location must be synchronized again")
    profile = db.get(StorageProfile, location.storage_profile_id)
    if not profile or not profile.enabled:
        raise StorageError("Enable the storage node before restoring its location")
    get_adapter(profile).verify(location.object_key, asset.sha256, asset.size, version_id=location.version_id)
    location.state, location.checksum, location.verified_at = "ready", asset.sha256, utcnow()
    db.flush()
    return location


def _same_physical_object(left, left_profile, right, right_profile):
    if left_profile.kind == "local" and right_profile.kind == "local":
        left_path = (Path((left_profile.config or {}).get("root") or settings.media_root) / safe_key(left.object_key)).resolve()
        right_path = (Path((right_profile.config or {}).get("root") or settings.media_root) / safe_key(right.object_key)).resolve()
        return left_path == right_path
    if left_profile.kind == "local" or right_profile.kind == "local":
        return False
    # Native OSS and its S3 endpoint can name the same object. Be conservative:
    # different endpoints do not prove independence when bucket/key coincide.
    def identity(location, profile):
        config = profile.config or {}
        prefix = str(config.get("prefix") or "").strip("/")
        return config.get("bucket"), f"{prefix}/{location.object_key}" if prefix else location.object_key
    same_key = identity(left, left_profile) == identity(right, right_profile)
    return same_key and (not left.version_id or not right.version_id or left.version_id == right.version_id)


def purge_location(db, location_id, *, check_active=None):
    """Explicit worker-only destruction of one retired replica, under backup/asset locks."""
    from app.backup import asset_gc_guard
    guard = check_active or (lambda: None)
    with asset_gc_guard(db):
        guard()
        with content_lock(db, _content_sha(db, location_id)):
            return _purge_locked(db, location_id, guard)


def _purge_locked(db, location_id, guard):
    asset, location = _lock_asset(db, location_id)
    if location.state not in {"retired", "deleted"}:
        raise StorageError("Retire this storage location before permanent deletion")
    if not is_managed_key(location.object_key, asset.sha256):
        raise StorageError("Permanent deletion is restricted to registered immutable objects")
    if location.state == "deleted":
        return {"location_id": location.id, "state": "deleted", "bytes": asset.size,
                "physical_reclamation": "previously_deleted"}
    for backup in db.scalars(select(BackupSet).where(BackupSet.status.in_(["running", "incomplete"]))):
        manifest = backup.manifest or {}
        if manifest.get("protect_all") or asset.id in {item["id"] for item in manifest.get("assets", [])}:
            raise StorageError("An incomplete backup currently protects this asset")
    profile = db.get(StorageProfile, location.storage_profile_id)
    if not profile or not profile.enabled:
        raise StorageError("Storage node must be enabled to verify permanent deletion")
    other_rows = list(db.execute(select(AssetLocation, StorageProfile).join(StorageProfile,
        StorageProfile.id == AssetLocation.storage_profile_id).where(AssetLocation.id != location.id,
        AssetLocation.state != "deleted")))
    # Check all registered aliases, even disabled profiles or retired copies.
    if any(_same_physical_object(location, profile, other, node) for other, node in other_rows):
        raise StorageError("Another registered storage location aliases this physical object")
    verified = False
    for replica, node in other_rows:
        if replica.asset_id != asset.id or replica.state != "ready" or not replica.verified_at or not node.enabled:
            continue
        guard()
        try:
            get_adapter(node).verify(replica.object_key, asset.sha256, asset.size, version_id=replica.version_id)
        except Exception:
            continue
        replica.checksum, replica.verified_at = asset.sha256, utcnow()
        verified = True
        break
    if not verified:
        raise StorageError("Cannot permanently delete the last verified readable replica")
    guard()
    adapter = get_adapter(profile)
    try:
        adapter.head(location.object_key, version_id=location.version_id)
    except ObjectMissing:
        pass  # Retry after deletion succeeded but the database commit was interrupted.
    else:
        adapter.delete(location.object_key, version_id=location.version_id)
        try:
            adapter.head(location.object_key, version_id=location.version_id)
        except ObjectMissing:
            pass
        else:
            raise StorageError("Storage provider did not confirm object deletion")
    guard()
    location.state = "deleted"
    db.flush()
    reclamation = "local_unlinked" if profile.kind == "local" else "specific_object_version_deleted" if location.version_id else "provider_delete_confirmed_reclamation_unknown"
    return {"location_id": location.id, "state": "deleted", "bytes": asset.size, "physical_reclamation": reclamation}
