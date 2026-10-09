from __future__ import annotations

import json
import os
import hashlib
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError as DatabaseIntegrityError

from app.config import settings
from app.models import Asset, AssetLocation, StorageProfile
from app.security import decrypt_secret

from .base import IntegrityError, ObjectMissing, StorageError, content_key, file_digest, throttled_check
from .local import LocalStorage, _is_link
from .locking import content_lock
from .naming import MediaName, is_managed_key, media_name_for_asset


def utcnow():
    return datetime.now(timezone.utc)


def storage_placement(kind, config):
    """Snapshot physical placement; tuning and credential rotation do not move bytes."""
    return {"kind": kind, "config": deepcopy({key: value for key, value in (config or {}).items()
            if key not in {"read_priority", "part_size", "public_endpoint", "public_base_url",
                           "delivery_mode", "private_bucket"}})}


_CONFIGURATION_LOCK = int.from_bytes(hashlib.sha256(b"treasure-storage-configuration-v1").digest()[:8], "big", signed=True)


def lock_storage_configuration(db, *, wait=True):
    """Serialize configuration writers until commit; SQLite is test-only."""
    if db.get_bind().dialect.name == "postgresql":
        if not wait:
            return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _CONFIGURATION_LOCK}))
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CONFIGURATION_LOCK})
    return True


def get_adapter(profile: StorageProfile, *, purpose="default"):
    if not profile.enabled:
        raise StorageError("Storage profile is disabled")
    config = dict(profile.config or {})
    if profile.kind == "local":
        return LocalStorage(config.get("root") or settings.media_root)
    try:
        credentials = json.loads(decrypt_secret(profile.secret_encrypted)) if profile.secret_encrypted else {}
    except Exception as exc:
        raise StorageError("Storage credentials cannot be decrypted") from exc
    from .providers import create_cloud_adapter
    from .configuration import GRAPH_KINDS
    from .credentials import rotation_callback
    callback = rotation_callback(profile) if profile.kind in GRAPH_KINDS else None
    return create_cloud_adapter(profile.kind, config, credentials, purpose=purpose, on_credentials=callback)


def default_profile(db, profile_id=None):
    if profile_id:
        profile = db.get(StorageProfile, profile_id)
        if not profile or not profile.enabled:
            raise StorageError("Storage profile is unavailable")
        return profile
    profile = db.scalars(select(StorageProfile).where(StorageProfile.enabled.is_(True))
                         .order_by(StorageProfile.is_default.desc(), StorageProfile.created_at, StorageProfile.id)).first()
    if profile:
        return profile
    # Existing defaults and fallbacks do not lock configuration during uploads.
    # Empty installations recheck after acquiring the writer lock.
    lock_storage_configuration(db)
    profile = db.scalars(select(StorageProfile).where(StorageProfile.enabled.is_(True))
                         .order_by(StorageProfile.is_default.desc(), StorageProfile.created_at, StorageProfile.id)
                         .execution_options(populate_existing=True)).first()
    if profile:
        return profile
    if db.scalars(select(StorageProfile)).first():
        raise StorageError("No enabled storage profile")
    profile = StorageProfile(name="Local media", kind="local", config={"root": str(settings.media_root)}, enabled=True, is_default=True)
    db.add(profile)
    db.flush()
    return profile


def _register_location(db, asset, profile, key, info, *, expected_placement):
    # The adapter may have uploaded before an administrator changed an empty
    # profile. KEY SHARE permits independent credential rotation while still
    # excluding configuration writers (which explicitly acquire FOR UPDATE).
    current = db.scalar(select(StorageProfile).where(StorageProfile.id == profile.id)
                        .with_for_update(read=True, key_share=True).execution_options(populate_existing=True))
    if current is None or not current.enabled or storage_placement(current.kind, current.config) != expected_placement:
        raise StorageError("Storage placement changed during upload; retry using the current configuration")
    # Keep publication serialized until the caller commits, even after the
    # content lock exits. Purge/retire take this same row lock before reading state.
    db.scalar(select(Asset).where(Asset.id == asset.id).with_for_update())
    location = db.scalars(select(AssetLocation).where(AssetLocation.asset_id == asset.id, AssetLocation.storage_profile_id == profile.id, AssetLocation.object_key == key)).first()
    if location is None:
        try:
            with db.begin_nested():
                location = AssetLocation(asset_id=asset.id, storage_profile_id=profile.id, object_key=key, state="ready")
                db.add(location)
                db.flush()
        except DatabaseIntegrityError:
            location = db.scalars(select(AssetLocation).where(AssetLocation.asset_id == asset.id, AssetLocation.storage_profile_id == profile.id, AssetLocation.object_key == key)).one()
    location.state = "ready"
    location.checksum = asset.sha256
    location.version_id = info.version_id
    location.verified_at = utcnow()
    db.flush()
    return location


class IngestWorkspace:
    """An owned generation directory, placed beside local immutable storage.

    Files are generated once on the destination filesystem, verified by the
    caller, then consumed by ingest_file. Only this active capability permits
    publication without a copy; general ingest inputs retain copy isolation.
    """
    def __init__(self, db, *, profile_id=None, prefix="media-"):
        self.db, self.requested_profile_id, self.prefix = db, profile_id, prefix
        self.path = self._temporary = None

    def __enter__(self):
        if self.path is not None:
            raise StorageError("Ingest workspace cannot be reused")
        profile = default_profile(self.db, self.requested_profile_id)
        self.profile_id = profile.id
        self.placement = storage_placement(profile.kind, profile.config)
        self.adapter = get_adapter(profile)
        if isinstance(self.adapter, LocalStorage):
            parent = self.adapter.path_for("temporary")
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.adapter.path_for("temporary")
        else:
            parent = LocalStorage(settings.scratch_dir).root
        self._temporary = tempfile.TemporaryDirectory(prefix=self.prefix, dir=parent)
        self.path = Path(self._temporary.name)
        return self

    def __exit__(self, *exc):
        temporary, self._temporary = self._temporary, None
        return temporary.__exit__(*exc)

    def validate(self, db, path, profile_id):
        if self._temporary is None or self.db is not db or profile_id not in (None, self.profile_id):
            raise StorageError("Invalid ingest workspace")
        path = Path(path).absolute()
        # Generated outputs are direct children. Do not accept an archive, a
        # nested traversal, a symlink/junction or a hard-link alias as owned work.
        if path.parent != self.path or _is_link(self.path) or _is_link(path):
            raise StorageError("Ingest source is outside its private workspace")
        if isinstance(self.adapter, LocalStorage):
            self.adapter.path_for(path.relative_to(self.adapter.root).as_posix())
        if not path.is_file() or path.stat().st_nlink != 1:
            raise StorageError("Ingest source must be an exclusive regular file")


def ingest_workspace(db, *, profile_id=None, prefix="media-"):
    return IngestWorkspace(db, profile_id=profile_id, prefix=prefix)


def ingest_file(db, path: Path, *, kind: str, mime_type: str, profile_id=None, media_name: MediaName | None = None, workspace=None, check_active=None) -> Asset:
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise StorageError("Ingest source must be a regular file")
    if workspace is not None:
        if not isinstance(workspace, IngestWorkspace):
            raise StorageError("Invalid ingest workspace")
        workspace.validate(db, path, profile_id)
        profile_id = workspace.profile_id
    sha, size = file_digest(path, check_active=check_active)
    with content_lock(db, sha):
        return _ingest_file_locked(db, path, sha, size, kind=kind, mime_type=mime_type, profile_id=profile_id,
            media_name=media_name, workspace=workspace, check_active=check_active)


def _ingest_file_locked(db, path, sha, size, *, kind, mime_type, profile_id, media_name=None, workspace=None, check_active=None):
    profile = default_profile(db, profile_id)
    expected_placement = storage_placement(profile.kind, profile.config)
    adapter = get_adapter(profile)
    if workspace is not None and workspace.placement != expected_placement:
        raise StorageError("Storage placement changed during media generation")
    guard = check_active or (lambda: None)
    guard()
    asset = db.scalars(select(Asset).where(Asset.sha256 == sha)).first()
    key = media_name.key(sha, mime_type) if media_name and kind in {"media", "video"} else content_key(sha)
    if asset:
        # Repeated acquisition and title changes reuse the same verified object.
        # Existing hash-only archives are not silently renamed or duplicated.
        existing = db.scalar(select(AssetLocation).where(AssetLocation.asset_id == asset.id,
            AssetLocation.storage_profile_id == profile.id, AssetLocation.state == "ready")
            .order_by(AssetLocation.created_at, AssetLocation.id).limit(1))
        if existing and is_managed_key(existing.object_key, sha):
            key = existing.object_key
    try:
        info = adapter.verify(key, sha, size, check_active=guard)
    except ObjectMissing:
        if workspace is not None and isinstance(adapter, LocalStorage):
            workspace.validate(db, path, profile_id)
            info = adapter.consume_file(path, key, mime_type, sha256=sha, size=size, check_active=guard)
        else:
            kwargs = {"check_active": guard} if isinstance(adapter, LocalStorage) else {}
            info = adapter.put_file(path, key, mime_type, **kwargs)
            # Stored metadata is not evidence: re-read the uploaded bytes.
            info = adapter.verify(key, sha, size, version_id=info.version_id, check_active=guard)
    guard()
    if asset is None:
        try:
            with db.begin_nested():
                asset = Asset(sha256=sha, size=size, mime_type=mime_type, kind=kind)
                db.add(asset)
                db.flush()
        except DatabaseIntegrityError:
            asset = db.scalars(select(Asset).where(Asset.sha256 == sha)).one()
    if asset.size != size:
        raise IntegrityError("Asset digest/size conflict")
    _register_location(db, asset, profile, key, info, expected_placement=expected_placement)
    return asset


def asset_locations(db, asset_id):
    rows = list(db.execute(select(AssetLocation, StorageProfile).join(StorageProfile, StorageProfile.id == AssetLocation.storage_profile_id).where(AssetLocation.asset_id == asset_id, AssetLocation.state == "ready", StorageProfile.enabled.is_(True))))
    return sorted(rows, key=lambda row: (int((row[1].config or {}).get("read_priority", 100)), not row[1].is_default, row[0].id))


def resolve_asset(db, asset_id, *, expires_in=1800) -> dict:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ObjectMissing("Asset does not exist")
    for location, profile in asset_locations(db, asset_id):
        try:
            adapter = get_adapter(profile)
            info = adapter.head(location.object_key, version_id=location.version_id)
            if info.size != asset.size:
                continue
            result = {"asset_id": asset.id, "size": asset.size, "mime_type": asset.mime_type, "sha256": asset.sha256, "location_id": location.id, "kind": profile.kind}
            if isinstance(adapter, LocalStorage):
                result.update(path=adapter.path_for(location.object_key), expires_at=None)
            else:
                result.update(url=adapter.presign(location.object_key, expires_in, version_id=location.version_id), expires_at=utcnow() + timedelta(seconds=expires_in))
            return result
        except Exception:
            # Never include provider exceptions or signed URLs in public failures.
            continue
    raise StorageError("No readable verified replica is available")


def read_asset_bytes(db, asset_id, *, max_bytes=64 * 1024**2):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ObjectMissing("Asset does not exist")
    if asset.size > max_bytes:
        raise StorageError("Asset exceeds inline read limit")
    for location, profile in asset_locations(db, asset_id):
        try:
            with get_adapter(profile).reader(location.object_key, version_id=location.version_id) as stream:
                data = stream.read(max_bytes + 1)
            import hashlib
            if len(data) != asset.size or hashlib.sha256(data).hexdigest() != asset.sha256:
                continue
            return data
        except Exception:
            continue
    raise IntegrityError("No valid readable asset replica")


def copy_asset_to(db, asset_id, destination: Path, *, check_active=None):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ObjectMissing("Asset does not exist")
    guard = throttled_check(check_active)
    cancelled = False
    def check():
        nonlocal cancelled
        try:
            guard()
        except BaseException:
            cancelled = True
            raise
    for location, profile in asset_locations(db, asset_id):
        check()
        try:
            if _is_link(Path(destination)):
                raise StorageError("Copy destination cannot be a link")
            with get_adapter(profile).reader(location.object_key, version_id=location.version_id) as source, Path(destination).open("wb") as target:
                count = 0
                while block := source.read(min(1024 * 1024, asset.size - count + 1)):
                    check()
                    count += len(block)
                    if count > asset.size:
                        raise IntegrityError("Object exceeds registered size")
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if file_digest(destination, check_active=check) == (asset.sha256, asset.size):
                check()
                return
        except Exception:
            if cancelled:
                raise
    raise IntegrityError("No valid readable asset replica")


def materialize_asset(db, asset_id, path: Path, *, check_active=None) -> Path:
    """Download once to private scratch, verify, then publish without overwrite."""
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ObjectMissing("Asset does not exist")
    store = LocalStorage(settings.scratch_dir)
    try:
        key = Path(path).absolute().relative_to(store.root).as_posix()
    except ValueError:
        raise StorageError("Materialization target must be inside scratch directory") from None
    destination = store.path_for(key)
    if destination.exists():
        store.verify(key, asset.sha256, asset.size, check_active=check_active)
        return destination
    with tempfile.TemporaryDirectory(prefix="materialize-", dir=store.root) as directory:
        temporary = Path(directory) / "asset"
        copy_asset_to(db, asset_id, temporary, check_active=check_active)
        store.consume_file(temporary, key, asset.mime_type, sha256=asset.sha256, size=asset.size, check_active=check_active)
    return destination


def migrate_asset(db, asset_id, target_profile_id, *, checkpoint=None, on_checkpoint=None):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ObjectMissing("Asset does not exist")
    with content_lock(db, asset.sha256):
        return _migrate_asset_locked(db, asset, target_profile_id, checkpoint=checkpoint, on_checkpoint=on_checkpoint)


def _migrate_asset_locked(db, asset, target_profile_id, *, checkpoint, on_checkpoint):
    target = default_profile(db, target_profile_id)
    expected_placement = storage_placement(target.kind, target.config)
    adapter = get_adapter(target)
    existing = db.scalar(select(AssetLocation).where(AssetLocation.asset_id == asset.id,
        AssetLocation.storage_profile_id == target.id, AssetLocation.state == "ready")
        .order_by(AssetLocation.created_at, AssetLocation.id).limit(1))
    if existing and is_managed_key(existing.object_key, asset.sha256):
        key = existing.object_key
    else:
        # Explicit synchronization can give an older hash-only archive a readable
        # name on the destination without moving the original source object.
        name = media_name_for_asset(db, asset)
        key = name.key(asset.sha256, asset.mime_type) if name else content_key(asset.sha256)
        if not name:
            readable = db.scalar(select(AssetLocation.object_key).where(AssetLocation.asset_id == asset.id,
                AssetLocation.state != "deleted", AssetLocation.object_key.startswith("videos/"))
                .order_by(AssetLocation.created_at, AssetLocation.id).limit(1))
            if readable and is_managed_key(readable, asset.sha256):
                key = readable
        if checkpoint:
            # Metadata may be edited while a multipart copy is paused. Resume
            # its immutable original key, never start a second name/upload.
            if (checkpoint.get("sha256") != asset.sha256
                    or not is_managed_key(checkpoint.get("key"), asset.sha256)):
                raise StorageError("Multipart checkpoint does not match asset ownership")
            key = checkpoint["key"]
    try:
        info = adapter.verify(key, asset.sha256, asset.size)
    except ObjectMissing:
        Path(settings.scratch_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="migration-", dir=settings.scratch_dir) as directory:
            source = Path(directory) / "asset"
            copy_asset_to(db, asset.id, source)
            info = adapter.put_file(source, key, asset.mime_type, checkpoint=checkpoint, on_checkpoint=on_checkpoint)
            info = adapter.verify(key, asset.sha256, asset.size, version_id=info.version_id)
    return _register_location(db, asset, target, key, info, expected_placement=expected_placement)


def probe_profile(db, profile_id):
    profile = default_profile(db, profile_id)
    adapter = get_adapter(profile)
    key, payload = f"probes/{uuid.uuid4().hex}", uuid.uuid4().bytes * 512
    Path(settings.scratch_dir).mkdir(parents=True, exist_ok=True)
    created, info = False, None
    try:
        with tempfile.TemporaryDirectory(prefix="storage-probe-", dir=settings.scratch_dir) as directory:
            path = Path(directory) / "probe"
            path.write_bytes(payload)
            # Record cleanup responsibility before upload, including partial failures.
            created = True
            info = adapter.put_file(path, key, "application/octet-stream")
            sha, size = file_digest(path)
            adapter.verify(key, sha, size, version_id=info.version_id)
            if adapter.read_range(key, 17, 53) != payload[17:54]:
                raise IntegrityError("Range probe returned incorrect bytes")
        return {"status": "passed", "provider": profile.kind, "checks": {"put": True, "head": True, "sha256_readback": True, "single_range": True}, "verified_at": utcnow().isoformat(), "multipart": "not_tested", "browser_cors": "not_tested"}
    finally:
        if created:
            adapter.delete(key, version_id=info.version_id if info else None)
