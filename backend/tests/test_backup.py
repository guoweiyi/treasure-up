import base64
import io
import os
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.backup import BackupError, _postgres_environment, create_backup, decrypt_to, encrypt_file, load_backup_manifest, protected_asset_ids, restore_backup, rpo_status, verify_backup
from app.config import settings
from app.models import Asset, AssetLocation, BackupSet, Base, Setting, StorageProfile
from app.storage.service import ingest_file, read_asset_bytes, utcnow


@pytest.fixture
def backup_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", tmp_path / "primary" / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "primary" / "scratch")
    database = tmp_path / "primary" / "db"
    database.mkdir(parents=True)
    engine = create_engine(f"sqlite:///{database / 'catalog.sqlite'}")
    Base.metadata.create_all(engine)
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("TREASURE_BACKUP_KEY", key)
    with Session(engine, expire_on_commit=False) as db:
        db.add(Setting(key="backup", value={"destination": str(tmp_path / "independent"), "key_id": "test-key"}))
        source = tmp_path / "sample"
        source.write_bytes(b"unique private media payload" * 4096)
        asset = ingest_file(db, source, kind="video", mime_type="video/mp4")
        db.commit()
        yield db, asset, source.read_bytes(), key
    engine.dispose()


def test_encryption_detects_corruption_truncation_and_wrong_context(tmp_path, monkeypatch):
    monkeypatch.setattr("app.backup.CHUNK", 64)
    source, encrypted = tmp_path / "plain", tmp_path / "cipher"
    source.write_bytes(b"test" * 1024)
    master = os.urandom(32)
    encrypt_file(source, encrypted, master, "asset:test")
    output = io.BytesIO()
    decrypt_to(encrypted, output, master, "asset:test")
    assert output.getvalue() == source.read_bytes()
    with pytest.raises(BackupError):
        decrypt_to(encrypted, None, master, "asset:other")
    original = encrypted.read_bytes()
    encrypted.write_bytes(original[:-20])
    with pytest.raises(BackupError):
        decrypt_to(encrypted, None, master, "asset:test")
    changed = bytearray(original)
    changed[40] ^= 1
    encrypted.write_bytes(changed)
    with pytest.raises(BackupError):
        decrypt_to(encrypted, None, master, "asset:test")


def test_roundtrip_without_primary_media_or_database(backup_fixture, tmp_path):
    db, asset, content, key = backup_fixture
    backup = create_backup(db)
    assert backup.status == "complete"
    destination = tmp_path / "independent"
    assert verify_backup(destination, backup.id)["asset_count"] == 1
    assert asset.id in protected_asset_ids(db)
    for path in settings.media_root.rglob("*"):
        if path.is_file():
            path.unlink()
    source_engine = db.get_bind()
    source_database = source_engine.url.database
    db.close()
    source_engine.dispose()
    from pathlib import Path
    Path(source_database).unlink()
    for path in destination.rglob("*.enc"):
        assert b"unique private media payload" not in path.read_bytes()
    target_url = f"sqlite:///{tmp_path / 'restored.sqlite'}"
    result = restore_backup(destination, backup.id, target_url, tmp_path / "restored-media")
    assert result["restored"]
    engine = create_engine(target_url)
    with Session(engine) as restored:
        assert read_asset_bytes(restored, asset.id) == content
        assert all(not profile.enabled for profile in restored.scalars(select(StorageProfile).where(StorageProfile.name != "Restored local media")))
    engine.dispose()
    with pytest.raises(BackupError, match="empty"):
        restore_backup(destination, backup.id, target_url, tmp_path / "restored-media")


def test_missing_media_never_completes_and_keeps_gc_protection(backup_fixture):
    db, asset, _, _ = backup_fixture
    for path in settings.media_root.rglob("*"):
        if path.is_file():
            path.unlink()
    with pytest.raises(BackupError):
        create_backup(db)
    backup = db.scalars(select(BackupSet)).one()
    assert backup.status == "incomplete" and backup.completed_at is None
    assert asset.id in protected_asset_ids(db)


def test_corrupted_backup_refuses_restore_before_database_write(backup_fixture, tmp_path):
    db, _, _, _ = backup_fixture
    backup = create_backup(db)
    destination = tmp_path / "independent"
    manifest = load_backup_manifest(destination, backup.id)
    object_path = destination / manifest["assets"][0]["backup_key"]
    object_path.write_bytes(object_path.read_bytes()[:-1])
    url = f"sqlite:///{tmp_path / 'empty.sqlite'}"
    with pytest.raises(BackupError):
        restore_backup(destination, backup.id, url, tmp_path / "restore")
    from sqlalchemy import inspect
    engine = create_engine(url)
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_rpo_uses_snapshot_instead_of_completion(backup_fixture):
    db, _, _, _ = backup_fixture
    backup = create_backup(db)
    backup.snapshot_at = utcnow() - timedelta(hours=25)
    backup.completed_at = utcnow()
    db.commit()
    assert not rpo_status(db)["within_24_hours"]


def test_destination_cannot_overlap_primary(backup_fixture):
    db, _, _, _ = backup_fixture
    with pytest.raises(BackupError, match="overlaps"):
        create_backup(db, destination=settings.media_root / "backups")


def test_pg_environment_retains_url_password_without_cli_argument():
    environment = _postgres_environment(make_url("postgresql+psycopg://test:example-only@localhost/catalog"))
    assert environment["PGPASSWORD"] == "example-only"
    assert environment["PGDATABASE"] == "catalog"


def test_decrypt_size_limit_is_enforced_before_writing_excess_plaintext(tmp_path, monkeypatch):
    monkeypatch.setattr('app.backup.CHUNK', 64)
    source, encrypted = tmp_path / 'plain', tmp_path / 'encrypted'
    source.write_bytes(b'payload' * 100)
    master = os.urandom(32)
    encrypt_file(source, encrypted, master, 'size-limit')
    output = io.BytesIO()
    with pytest.raises(BackupError, match='size limit'):
        decrypt_to(encrypted, output, master, 'size-limit', max_bytes=100)
    assert output.tell() == 64
    with pytest.raises(BackupError, match='size limit'):
        decrypt_to(encrypted, None, master, 'size-limit', max_bytes=100)


@pytest.mark.parametrize('unlock_failure', [False, True])
def test_gc_guard_invalidates_connection_when_session_unlock_is_not_confirmed(unlock_failure):
    from types import SimpleNamespace
    from app.backup import asset_gc_guard

    class Connection:
        invalidated = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execution_options(self, **kwargs):
            assert kwargs == {'isolation_level': 'AUTOCOMMIT'}
            return self
        def execute(self, *args): pass
        def scalar(self, *args):
            if unlock_failure:
                raise RuntimeError('synthetic broken connection')
            return False
        def invalidate(self): self.invalidated = True

    connection = Connection()
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql'), connect=lambda: connection))
    if unlock_failure:
        with pytest.raises(RuntimeError):
            with asset_gc_guard(db): pass
    else:
        with asset_gc_guard(db): pass
    assert connection.invalidated
