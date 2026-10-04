"""Consistent catalog snapshots with immutable, authenticated encrypted media backups.

No destination or key is defaulted. A backup is complete only after decrypt-and-hash
verification of every object and the database dump. Restore refuses nonempty targets.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import subprocess
import tempfile
import uuid
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Asset, AssetLocation, BackupSet, IntegrationToken, Job, OutboxEvent, Setting, SourceSubscription, StorageProfile, UserSession, PlaybackSession, PasskeyChallenge, PasskeyAttempt
from app.storage.base import IntegrityError, StorageError, content_key, file_digest, safe_key
from app.storage.local import LocalStorage
from app.storage.service import copy_asset_to, utcnow

MAGIC = b"TUPBK1\n"
CHUNK = 4 * 1024**2
BACKUP_LOCK = 843092614
_GC_LOCK = threading.RLock()


class BackupError(RuntimeError):
    pass


def _master_key(key=None):
    value = key or os.environ.get("TREASURE_BACKUP_KEY", "")
    key_file = os.environ.get("TREASURE_BACKUP_KEY_FILE")
    if not value and key_file:
        value = Path(key_file).read_text(encoding="utf-8").strip()
    try:
        raw = base64.b64decode(value, altchars=b"-_", validate=True)
    except Exception as exc:
        raise BackupError("A valid independent backup key is required") from exc
    if len(raw) != 32:
        raise BackupError("Backup key must contain 32 random bytes in URL-safe base64")
    return raw


def _cipher(master, salt):
    return AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"treasure-up-backup-v1").derive(master))


def encrypt_file(source: Path, destination: Path, master: bytes, purpose: str):
    """Chunked AES-GCM; final authenticated empty record detects truncation."""
    salt = os.urandom(16)
    cipher, header = _cipher(master, salt), MAGIC + salt
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".writing-{uuid.uuid4().hex}"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as outgoing, Path(source).open("rb") as incoming:
            outgoing.write(header)
            counter = 0
            while True:
                block = incoming.read(CHUNK)
                nonce = counter.to_bytes(12, "big")
                encrypted = cipher.encrypt(nonce, block, header + purpose.encode() + nonce)
                outgoing.write(struct.pack(">I", len(encrypted)))
                outgoing.write(encrypted)
                counter += 1
                if not block:
                    break
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def decrypt_to(source: Path, destination, master: bytes, purpose: str, *, max_bytes: int | None = None) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        raise BackupError("Invalid backup size limit")
    try:
        with Path(source).open("rb") as incoming:
            header = incoming.read(len(MAGIC) + 16)
            if len(header) != len(MAGIC) + 16 or not header.startswith(MAGIC):
                raise BackupError("Invalid encrypted backup format")
            cipher, counter = _cipher(master, header[len(MAGIC):]), 0
            while True:
                length_raw = incoming.read(4)
                if len(length_raw) != 4:
                    raise BackupError("Truncated encrypted backup")
                length = struct.unpack(">I", length_raw)[0]
                if not 16 <= length <= CHUNK + 16:
                    raise BackupError("Invalid encrypted record length")
                encrypted = incoming.read(length)
                if len(encrypted) != length:
                    raise BackupError("Truncated encrypted backup")
                nonce = counter.to_bytes(12, "big")
                block = cipher.decrypt(nonce, encrypted, header + purpose.encode() + nonce)
                counter += 1
                if not block:
                    if incoming.read(1):
                        raise BackupError("Trailing encrypted backup data")
                    break
                size += len(block)
                if max_bytes is not None and size > max_bytes:
                    raise BackupError("Decrypted backup exceeds size limit")
                digest.update(block)
                if destination is not None:
                    destination.write(block)
    except BackupError:
        raise
    except Exception:
        raise BackupError("Backup authentication or read failed") from None
    return digest.hexdigest(), size


def _validate_destination(db, path: Path):
    if not path.is_absolute():
        raise BackupError("Backup destination must be an absolute independent mount path")
    forbidden = [Path(settings.media_root), Path(settings.scratch_dir)]
    for profile in db.scalars(select(StorageProfile).where(StorageProfile.kind == "local")):
        forbidden.append(Path((profile.config or {}).get("root") or settings.media_root))
    database_url = make_url(str(db.get_bind().url))
    if database_url.drivername.startswith("sqlite") and database_url.database not in (None, "", ":memory:"):
        forbidden.append(Path(database_url.database).absolute().parent)
    resolved = path.resolve()
    if path.is_symlink() or any(resolved.is_relative_to(item.resolve()) or item.resolve().is_relative_to(resolved) for item in forbidden):
        raise BackupError("Backup destination overlaps primary media, scratch or database storage")
    return LocalStorage(path)


def _postgres_environment(url):
    parsed = make_url(url) if isinstance(url, str) else url
    environment = os.environ.copy()
    # Credentials stay in the child environment, never command arguments or errors.
    for field, value in {"PGHOST": parsed.host, "PGPORT": parsed.port, "PGUSER": parsed.username, "PGPASSWORD": parsed.password, "PGDATABASE": parsed.database}.items():
        if value is not None:
            environment[field] = str(value)
    for option, value in parsed.query.items():
        if option in {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "host", "port"}:
            environment[f"PG{option.upper()}"] = str(value)
    environment.setdefault("PGCONNECT_TIMEOUT", "15")
    return environment


def _run_postgres(arguments, url, *, timeout=3600):
    try:
        completed = subprocess.run(arguments, env=_postgres_environment(url), capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError("PostgreSQL backup/restore command unavailable or timed out") from None
    if completed.returncode:
        raise BackupError(f"PostgreSQL backup/restore command failed (exit {completed.returncode})")


def _snapshot(db, dump_path):
    engine = db.get_bind()
    dialect = engine.dialect.name
    with engine.connect() as connection:
        if dialect == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            if dialect == "postgresql":
                snapshot_token = connection.scalar(text("SELECT pg_export_snapshot()"))
                database_format = "postgres_custom"
            elif dialect == "sqlite":
                snapshot_token, database_format = None, "sqlite_sql_test"
            else:
                raise BackupError("Unsupported database backup engine")
            snapshot_at = utcnow().isoformat()
            with Session(bind=connection) as snapshot_db:
                # Backing up every registered immutable asset is a safe superset of
                # explicit AssetRef rows and also covers model foreign-key references.
                assets = [{"id": item.id, "sha256": item.sha256, "size": item.size, "mime_type": item.mime_type, "kind": item.kind} for item in snapshot_db.scalars(select(Asset).order_by(Asset.id))]
                locations = [{"asset_id": item.asset_id, "storage_profile_id": item.storage_profile_id, "object_key": item.object_key, "version_id": item.version_id} for item in snapshot_db.scalars(select(AssetLocation).where(AssetLocation.state == "ready"))]
            if dialect == "postgresql":
                _run_postgres(["pg_dump", "--format=custom", "--no-password", f"--snapshot={snapshot_token}", f"--file={dump_path}"], engine.url)
            else:
                raw = connection.connection.driver_connection
                dump_path.write_text("\n".join(raw.iterdump()), encoding="utf-8")
            if not dump_path.is_file() or not dump_path.stat().st_size:
                raise BackupError("Database export is empty")
    return assets, locations, snapshot_at, database_format


def protected_asset_ids(db) -> set[str]:
    """GC must honor this even for interrupted/incomplete backup sets."""
    protected = set()
    for backup in db.scalars(select(BackupSet).where(BackupSet.status.in_(["incomplete", "running", "complete"]))):
        manifest = backup.manifest or {}
        if manifest.get("protect_all"):
            return set(db.scalars(select(Asset.id)))
        protected.update(item["id"] for item in manifest.get("assets", []))
    return protected


@contextmanager
def asset_gc_guard(db):
    """GC callers hold this across protection lookup and physical deletion.

    Postgres advisory locks coordinate independent processes. SQLite is a test-only
    deployment and uses an in-process mutex.
    """
    if db.get_bind().dialect.name == "postgresql":
        with db.get_bind().connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": BACKUP_LOCK})
            try:
                yield
            finally:
                try:
                    if not connection.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": BACKUP_LOCK}):
                        connection.invalidate()
                except Exception:
                    # A pooled connection must never retain a session-level GC lock.
                    connection.invalidate()
                    raise
    else:
        with _GC_LOCK:
            yield


def rpo_status(db):
    backup = db.scalars(select(BackupSet).where(BackupSet.status == "complete").order_by(BackupSet.snapshot_at.desc())).first()
    if not backup:
        return {"snapshot_at": None, "age_seconds": None, "within_24_hours": False}
    moment = backup.snapshot_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    age = max(0, (utcnow() - moment).total_seconds())
    return {"snapshot_at": moment.isoformat(), "age_seconds": age, "within_24_hours": age <= 86400, "backup_id": backup.id}


def create_backup(db, *, destination: Path | None = None, key=None, check_active=None) -> BackupSet:
    with asset_gc_guard(db):
        return _create_backup(db, destination=destination, key=key, check_active=check_active)


def _create_backup(db, *, destination: Path | None = None, key=None, check_active=None) -> BackupSet:
    """Runs a durable backup transaction; commits state independently of caller work."""
    setting = db.get(Setting, "backup")
    config = dict(setting.value or {}) if setting else {}
    if not destination and not config.get("destination"):
        raise BackupError("Configure an independent backup destination first")
    key_id = safe_key(config.get("key_id", ""))
    if "/" in key_id:
        raise BackupError("Backup key_id must be a simple identifier")
    master = _master_key(key)
    store = _validate_destination(db, Path(destination or config["destination"]))
    backup = BackupSet(status="incomplete", manifest={"protect_all": True}, snapshot_at=utcnow())
    db.add(backup)
    db.commit()
    backup_id = backup.id
    set_key = f"sets/{backup_id}"
    backup.object_key = set_key
    db.commit()
    Path(settings.scratch_dir).mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="backup-", dir=settings.scratch_dir) as directory:
            if check_active:
                check_active()
            temporary = Path(directory)
            dump = temporary / "database.dump"
            assets, locations, moment, database_format = _snapshot(db, dump)
            if check_active:
                check_active()
            manifest = {"schema_version": 1, "backup_id": backup_id, "key_id": key_id, "snapshot_at": moment, "database_format": database_format, "assets": assets, "source_locations": locations, "status": "incomplete"}
            backup.snapshot_at = datetime.fromisoformat(moment)
            backup.manifest = manifest
            db.commit()
            database_key = f"{set_key}/database.enc"
            encrypt_file(dump, store.path_for(database_key), master, f"database:{backup_id}")
            expected = file_digest(dump)
            if decrypt_to(store.path_for(database_key), None, master, f"database:{backup_id}", max_bytes=expected[1]) != expected:
                raise IntegrityError("Database backup validation failed")
            manifest["database"] = {"key": database_key, "sha256": expected[0], "size": expected[1]}
            for asset in assets:
                if check_active:
                    check_active()
                object_key = f"objects/{key_id}/{asset['sha256']}.enc"
                target = store.path_for(object_key)
                purpose = f"asset:{asset['sha256']}"
                if not target.exists():
                    source = temporary / "asset"
                    copy_asset_to(db, asset["id"], source)
                    try:
                        encrypt_file(source, target, master, purpose)
                    except FileExistsError:
                        pass
                    source.unlink(missing_ok=True)
                if decrypt_to(target, None, master, purpose, max_bytes=asset["size"]) != (asset["sha256"], asset["size"]):
                    raise IntegrityError("Media backup validation failed")
                asset["backup_key"] = object_key
            if check_active:
                check_active()
            manifest["status"] = "complete"
            manifest_file = temporary / "manifest.json"
            manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            manifest_key = f"{set_key}/manifest.enc"
            encrypt_file(manifest_file, store.path_for(manifest_key), master, f"manifest:{backup_id}")
            if decrypt_to(store.path_for(manifest_key), None, master, f"manifest:{backup_id}", max_bytes=64 * 1024**2) != file_digest(manifest_file):
                raise IntegrityError("Backup manifest validation failed")
            # Persist all ciphertext before declaring completion, including directory entries.
            if os.name != "nt":
                durable_directories = {store.root}
                for leaf in (store.path_for(manifest_key).parent, store.root / "objects" / key_id):
                    durable_directories.update(path for path in [leaf, *leaf.parents] if path.is_relative_to(store.root))
                for path in durable_directories:
                    if path.exists():
                        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
            backup.manifest = dict(manifest)
            backup.status = "complete"
            backup.completed_at = utcnow()
            backup.error = None
            db.commit()
        return backup
    except Exception as exc:
        db.rollback()
        backup = db.get(BackupSet, backup_id)
        backup.status = "incomplete"
        backup.completed_at = None
        reason = str(exc) if isinstance(exc, (BackupError, StorageError)) else type(exc).__name__
        backup.error = f"Backup failed: {reason}"
        db.commit()
        raise BackupError(backup.error) from None


def load_backup_manifest(destination: Path, backup_id: str, *, key=None):
    safe_key(backup_id)
    if "/" in backup_id:
        raise BackupError("Invalid backup identifier")
    master, store = _master_key(key), LocalStorage(destination)
    stream = io.BytesIO()
    decrypt_to(store.path_for(f"sets/{backup_id}/manifest.enc"), stream, master, f"manifest:{backup_id}", max_bytes=64 * 1024**2)
    manifest = json.loads(stream.getvalue())
    if manifest.get("schema_version") != 1 or manifest.get("backup_id") != backup_id or manifest.get("status") != "complete":
        raise BackupError("Backup manifest is not a complete supported backup")
    return manifest


def verify_backup(destination: Path, backup_id: str, *, key=None):
    master, store = _master_key(key), LocalStorage(destination)
    manifest = load_backup_manifest(destination, backup_id, key=key)
    item = manifest["database"]
    if decrypt_to(store.path_for(item["key"]), None, master, f"database:{backup_id}", max_bytes=item["size"]) != (item["sha256"], item["size"]):
        raise IntegrityError("Database backup mismatch")
    for asset in manifest["assets"]:
        if decrypt_to(store.path_for(asset["backup_key"]), None, master, f"asset:{asset['sha256']}", max_bytes=asset["size"]) != (asset["sha256"], asset["size"]):
            raise IntegrityError("Media backup mismatch")
    return {"backup_id": backup_id, "snapshot_at": manifest["snapshot_at"], "asset_count": len(manifest["assets"]), "verified": True}


def restore_backup(destination: Path, backup_id: str, database_url: str, media_root: Path, *, key=None):
    """CLI only: refuses existing schema/media; no source/cloud access is used."""
    root = Path(media_root).absolute()
    if root.is_symlink() or (root.exists() and any(root.iterdir())):
        raise BackupError("Restore requires an empty media directory")
    if root.resolve().is_relative_to(Path(destination).resolve()) or Path(destination).resolve().is_relative_to(root.resolve()):
        raise BackupError("Restore target overlaps backup source")
    engine = create_engine(database_url)
    try:
        if inspect(engine).get_table_names():
            raise BackupError("Restore requires an empty database")
        manifest = load_backup_manifest(destination, backup_id, key=key)
        if (engine.dialect.name == "postgresql") != (manifest["database_format"] == "postgres_custom"):
            raise BackupError("Backup database format does not match restore target")
        verify_backup(destination, backup_id, key=key)
        master, source, target = _master_key(key), LocalStorage(destination), LocalStorage(root)
        with tempfile.TemporaryDirectory(prefix="treasure-restore-") as directory:
            temporary = Path(directory)
            dump = temporary / "database.dump"
            with dump.open("wb") as stream:
                actual = decrypt_to(source.path_for(manifest["database"]["key"]), stream, master, f"database:{backup_id}", max_bytes=manifest["database"]["size"])
            if actual != (manifest["database"]["sha256"], manifest["database"]["size"]):
                raise IntegrityError("Database backup changed during restore")
            for asset in manifest["assets"]:
                path = temporary / "asset"
                with path.open("wb") as stream:
                    actual = decrypt_to(source.path_for(asset["backup_key"]), stream, master, f"asset:{asset['sha256']}", max_bytes=asset["size"])
                if actual != (asset["sha256"], asset["size"]):
                    raise IntegrityError("Media backup changed during restore")
                target.put_file(path, content_key(asset["sha256"]), asset["mime_type"])
                path.unlink()
            if engine.dialect.name == "postgresql":
                _run_postgres(["pg_restore", "--no-password", "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction", "--dbname", make_url(database_url).database, str(dump)], database_url)
            elif engine.dialect.name == "sqlite":
                with engine.connect() as connection:
                    connection.connection.driver_connection.executescript(dump.read_text(encoding="utf-8"))
                    connection.commit()
            else:
                raise BackupError("Unsupported restore database")
        # A previous release's dump lacks tables used by current ORM cleanup.
        # Upgrade only this verified recovery target, keeping global settings and
        # its application engine untouched. create_all test dumps have no version
        # table and already contain the current schema, so they need no migration.
        with engine.begin() as connection:
            if inspect(connection).has_table("alembic_version"):
                migration = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                migration.attributes["connection"] = connection
                command.upgrade(migration, "head")
        with Session(engine) as restored:
            # Old sessions, schedulers, and storage endpoints must not revive on recovery.
            restored.execute(delete(PasskeyChallenge))
            restored.execute(delete(PasskeyAttempt))
            restored.execute(delete(UserSession))
            restored.execute(delete(PlaybackSession))
            for token in restored.scalars(select(IntegrationToken).where(IntegrationToken.revoked_at.is_(None))):
                token.revoked_at = utcnow()
            restored.execute(delete(OutboxEvent))
            for job in restored.scalars(select(Job).where(Job.status.in_(["pending", "queued", "running", "retry", "paused"]))):
                job.status = "paused"
                job.lease_owner = None
                job.lease_expires_at = None
            for subscription in restored.scalars(select(SourceSubscription)):
                subscription.enabled = False
            for profile in restored.scalars(select(StorageProfile)):
                profile.enabled, profile.is_default = False, False
            for location in restored.scalars(select(AssetLocation)):
                location.state = "unavailable"
            profile = StorageProfile(name="Restored local media", kind="local", config={"root": str(root)}, enabled=True, is_default=True)
            restored.add(profile)
            restored.flush()
            for asset in manifest["assets"]:
                restored.add(AssetLocation(asset_id=asset["id"], storage_profile_id=profile.id, object_key=content_key(asset["sha256"]), state="ready", checksum=asset["sha256"], verified_at=utcnow()))
            backup_setting = restored.get(Setting, "backup")
            if backup_setting:
                backup_setting.value = {**backup_setting.value, "enabled": False, "destination": ""}
            for key, paused in {"statistics": {"enabled": False},
                                "playback": {"package_long_videos": False, "analyze_loudness": False}}.items():
                row = restored.get(Setting, key)
                if row:
                    row.value = {**row.value, **paused}
                else:
                    restored.add(Setting(key=key, value=paused))
            # A manually started batch resumes even with recurring scheduling off.
            # Restore must clear those continuations as well as pause old jobs.
            restored.execute(delete(Setting).where(Setting.key.in_(["statistics_runtime", "media_maintenance_runtime"])))
            recovered_set = restored.get(BackupSet, backup_id)
            if recovered_set:
                recovered_set.manifest = manifest
                recovered_set.status = "complete"
                recovered_set.completed_at = utcnow()
            restored.commit()
        return {"backup_id": backup_id, "asset_count": len(manifest["assets"]), "restored": True, "collection_paused": True}
    finally:
        engine.dispose()
