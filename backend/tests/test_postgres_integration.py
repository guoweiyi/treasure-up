"""Opt-in real PostgreSQL tests, isolated from the configured application database.

Run inside the backend image with TREASURE_RUN_POSTGRES_TESTS=1. Connection
parameters come from settings; only random treasure_test_* database names are used.
The role needs CREATE DATABASE. Tests never print connection strings or keys.
"""
import base64
import hashlib
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app import backup, jobs
from app.config import settings
from app.models import (Asset, AssetLocation, Job, JobAttempt, Setting, StorageProfile, User, Video, VideoPart,
                        MediaVariant, SourceAccount, PlaybackSession, utcnow)
from app.storage.service import ingest_file, migrate_asset, read_asset_bytes, resolve_asset

pytestmark = pytest.mark.skipif(os.environ.get("TREASURE_RUN_POSTGRES_TESTS") != "1",
                                reason="Explicit TREASURE_RUN_POSTGRES_TESTS=1 required")


def _test_database_name(name):
    if not re.fullmatch(r"treasure_test_[0-9a-f]{12}_(source|restore)", name):
        raise RuntimeError("Refusing a non-test database name")
    return name


@pytest.fixture
def postgres_workspace(monkeypatch):
    configured = make_url(settings.database_url)
    if configured.get_backend_name() != "postgresql":
        pytest.fail("Integration tests require a PostgreSQL container configuration")
    suffix = uuid4().hex[:12]
    source_name = _test_database_name(f"treasure_test_{suffix}_source")
    restore_name = _test_database_name(f"treasure_test_{suffix}_restore")
    assert configured.database not in {source_name, restore_name}
    scratch = Path(settings.scratch_dir).resolve()
    root = scratch / f"postgres-test-{suffix}"
    if root.parent != scratch or root.exists():
        raise RuntimeError("Unsafe integration-test directory")
    root.mkdir(parents=True)
    admin = create_engine(configured.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created, engines = [], []

    def drop_test_database(name):
        _test_database_name(name)
        if name not in created:
            raise RuntimeError("Database was not created by this test")
        with admin.connect() as connection:
            connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"), {"name": name})
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))

    try:
        with admin.connect() as connection:
            for name in (source_name, restore_name):
                connection.execute(text(f'CREATE DATABASE "{name}"'))
                created.append(name)
        source_url = configured.set(database=source_name)
        restore_url = configured.set(database=restore_name)
        monkeypatch.setattr(settings, "database_url", source_url.render_as_string(hide_password=False))
        monkeypatch.setattr(settings, "media_root", root / "primary" / "media")
        monkeypatch.setattr(settings, "scratch_dir", root / "primary" / "scratch")
        monkeypatch.setattr(settings, "data_dir", root / "primary")
        alembic = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(alembic, "head")
        source_engine = create_engine(source_url)
        engines.append(source_engine)
        factory = sessionmaker(source_engine, expire_on_commit=False)
        monkeypatch.setattr(jobs, "SessionLocal", factory)
        monkeypatch.setattr(jobs, "heartbeat", lambda job_id, owner, stop: stop.wait())
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        yield SimpleNamespace(root=root, sessions=factory, engine=source_engine,
                              source_name=source_name, restore_name=restore_name,
                              restore_url=restore_url, key=key, drop=drop_test_database,
                              engines=engines)
    finally:
        for engine in engines:
            engine.dispose()
        for name in reversed(created):
            drop_test_database(name)
        admin.dispose()
        if root.parent != scratch or not re.fullmatch(r"postgres-test-[0-9a-f]{12}", root.name):
            raise RuntimeError("Refusing cleanup outside the test directory")
        shutil.rmtree(root)


def _command(arguments):
    result = subprocess.run(arguments, capture_output=True, timeout=60, check=False)
    if result.returncode:
        pytest.fail(f"Synthetic media validation command failed: {Path(arguments[0]).name}")


@pytest.mark.parametrize("source_revision", ["head", "39052d216250"], ids=["current", "v01"])
def test_postgres_snapshot_encrypted_backup_and_restore_without_primary(postgres_workspace, monkeypatch, source_revision):
    space = postgres_workspace
    video = space.root / "synthetic.mp4"
    _command([settings.ffmpeg_path, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10",
              "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-c:v", "libx264",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(video)])
    expected = hashlib.sha256(video.read_bytes()).hexdigest()
    with space.sessions() as db:
        # Exercise the real Alembic migration rather than metadata.create_all.
        assert db.scalar(text("SELECT count(*) FROM alembic_version")) == 1
        db.add(Setting(key="backup", value={"destination": str(space.root / "independent"), "key_id": "integration-key"}))
        db.add_all([Setting(key="statistics", value={"enabled":True}),
                    Setting(key="statistics_runtime", value={"active":True,"manual":True}),
                    Setting(key="media_maintenance_runtime", value={"active":True})])
        asset = ingest_file(db, video, kind="video", mime_type="video/mp4")
        db.commit()
        asset_id = asset.id
        viewer = User(username="backup-viewer", password_hash="test-only")
        video_row = Video(bvid="BVtestBackup", title="preserved archive")
        account = SourceAccount(name="preserved source", secret_encrypted="test-only")
        db.add_all([viewer, video_row, account]); db.flush()
        part_row = VideoPart(video_id=video_row.id, cid="test-backup-part")
        db.add(part_row); db.flush()
        variant = MediaVariant(part_id=part_row.id, asset_id=asset_id, format_key="test", kind="archive")
        db.add(variant); db.flush()
        db.add(PlaybackSession(user_id=viewer.id, variant_id=variant.id, protocol="file", state={},
                               expires_at=utcnow() + timedelta(hours=4)))
        db.commit()
        original = resolve_asset(db, asset_id)["path"]
        replica = StorageProfile(name="test-replica", kind="local", enabled=True, is_default=False,
                                 config={"root": str(space.root / "primary" / "replica")})
        db.add(replica)
        db.commit()
        migrate_asset(db, asset_id, replica.id)
        db.commit()
        original.unlink()
        assert read_asset_bytes(db, asset_id) == video.read_bytes()
        video_id, variant_id, account_id = video_row.id, variant.id, account.id
        db.commit()
        migration = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        if source_revision != "head":
            # Downgrade the populated disposable source, then create a genuine
            # v0.1 dump. The recovery target remains a separate empty database.
            with space.engine.begin() as connection:
                migration.attributes["connection"] = connection
                command.downgrade(migration, source_revision)
            migration.attributes.pop("connection")
            assert "playback_sessions" not in inspect(space.engine).get_table_names()

        original_runner = backup._run_postgres
        injected = []

        def concurrent_write_after_export(arguments, url, **kwargs):
            if arguments[0] == "pg_dump" and not injected:
                assert any(arg.startswith("--snapshot=") for arg in arguments)
                late_file = space.root / "late.txt"
                late_file.write_bytes(b"committed after exported snapshot")
                with space.sessions() as concurrent:
                    late = ingest_file(concurrent, late_file, kind="metadata", mime_type="text/plain")
                    concurrent.commit()
                    injected.append(late.id)
            return original_runner(arguments, url, **kwargs)

        monkeypatch.setattr(backup, "_run_postgres", concurrent_write_after_export)
        archived = backup.create_backup(db, key=space.key)
        backup_id = archived.id
        assert archived.status == "complete" and archived.manifest["database_format"] == "postgres_custom"
        assert [item["id"] for item in archived.manifest["assets"]] == [asset_id]
        assert db.scalar(select(func.count()).select_from(Asset)) == 2
        assert backup.verify_backup(space.root / "independent", backup_id, key=space.key)["asset_count"] == 1
        assert asset_id in backup.protected_asset_ids(db)
    space.engine.dispose()
    space.drop(space.source_name)
    (space.root / "primary").rename(space.root / "isolated-primary")
    restored_media = space.root / "restored-media"
    result = backup.restore_backup(space.root / "independent", backup_id,
                                   space.restore_url.render_as_string(hide_password=False), restored_media, key=space.key)
    assert result["restored"] and result["asset_count"] == 1
    restored_engine = create_engine(space.restore_url)
    space.engines.append(restored_engine)
    with Session(restored_engine) as restored:
        assert restored.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(migration).get_current_head()
        assert {"playback_sessions", "storage_observations", "video_stat_snapshots"} <= set(inspect(restored_engine).get_table_names())
        assert restored.get(Video, video_id).title == "preserved archive"
        assert restored.get(MediaVariant, variant_id).asset_id == asset_id
        assert restored.get(MediaVariant, variant_id).metadata_json == {}
        assert restored.get(SourceAccount, account_id).name == "preserved source"
        assert restored.get(SourceAccount, account_id).risk_failures == 0
        assert restored.get(SourceAccount, account_id).cooldown_until is None
        assert restored.scalar(select(func.count()).select_from(Asset)) == 1
        assert restored.scalar(select(func.count()).select_from(PlaybackSession)) == 0
        assert restored.get(Setting, "statistics").value["enabled"] is False
        assert restored.get(Setting, "playback").value["package_long_videos"] is False
        assert restored.get(Setting, "playback").value["analyze_loudness"] is False
        assert restored.get(Setting, "statistics_runtime") is None
        assert restored.get(Setting, "media_maintenance_runtime") is None
        assert restored.get(Asset, injected[0]) is None
        assert hashlib.sha256(read_asset_bytes(restored, asset_id)).hexdigest() == expected
        delivery = resolve_asset(restored, asset_id)
        assert delivery["path"].is_relative_to(restored_media)
        assert delivery["kind"] == "local"
        _command([settings.ffmpeg_path, "-v", "error", "-i", str(delivery["path"]), "-f", "null", "-"])


def test_upgrade_preserves_populated_v01_archive(postgres_workspace):
    space = postgres_workspace
    with space.sessions() as db:
        video = Video(bvid="BVmigration", title="existing archive")
        asset = Asset(sha256="a" * 64, size=123, kind="video", mime_type="video/mp4")
        account = SourceAccount(name="existing source", secret_encrypted="test-only")
        db.add_all([video, asset, account]); db.flush()
        part = VideoPart(video_id=video.id, cid="migration-part")
        db.add(part); db.flush()
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, format_key="old-format", kind="archive")
        db.add(variant); db.commit()
        video_id, variant_id, account_id, asset_id = video.id, variant.id, account.id, asset.id
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    # A populated v0.1 schema exercises NOT NULL defaults during the upgrade.
    command.downgrade(config, "39052d216250")
    command.upgrade(config, "head")
    with space.sessions() as db:
        assert db.get(Video, video_id).title == "existing archive"
        assert db.get(MediaVariant, variant_id).asset_id == asset_id
        assert db.get(MediaVariant, variant_id).metadata_json == {}
        assert db.get(SourceAccount, account_id).risk_failures == 0
        assert db.get(SourceAccount, account_id).cooldown_until is None


def test_postgres_claim_contention_and_old_worker_fence(postgres_workspace, monkeypatch):
    space = postgres_workspace
    with space.sessions() as db:
        pending = jobs.enqueue(db, "archive_video", "test-target")
        db.commit()
        job_id = pending.id
    barrier = threading.Barrier(2)

    def compete(owner):
        with space.sessions() as db:
            barrier.wait()
            item = jobs.claim(db, job_id, owner)
            return item.lease_owner if item else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = list(pool.map(compete, ["test-a", "test-b"]))
    assert len([winner for winner in winners if winner]) == 1
    with space.sessions() as db:
        db.execute(update(Job).where(Job.id == job_id).values(status="queued", lease_owner=None, lease_expires_at=None))
        db.commit()

    def stale_worker(db, job):
        with space.sessions() as replacement:
            replacement.execute(update(Job).where(Job.id == job.id).values(lease_expires_at=utcnow() - timedelta(seconds=1)))
            replacement.commit()
            jobs.recover_and_dispatch(replacement, lambda *_: None)
            assert jobs.claim(replacement, job.id, "new-owner")
            jobs.save_checkpoint(replacement, job.id, "new-owner", {"new_owner": True})
        db.add(Asset(sha256="f" * 64, size=1, kind="metadata", mime_type="text/plain"))
        db.commit()
        pytest.fail("Old worker committed after lease replacement")

    monkeypatch.setattr("app.ingest.runner.run_job", stale_worker)
    assert jobs.run_job_id(job_id)["status"] == "lease_lost"
    with space.sessions() as db:
        current = db.get(Job, job_id)
        assert current.lease_owner == "new-owner" and current.checkpoint == {"new_owner": True}
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(JobAttempt.status)) == "lease_lost"


def test_postgres_savepoint_does_not_lock_out_heartbeat(postgres_workspace, monkeypatch):
    space = postgres_workspace
    with space.sessions() as db:
        item = jobs.enqueue(db, "archive_video", "savepoint-test")
        db.commit()
        job_id = item.id

    def work_with_savepoint(db, job):
        with db.begin_nested():
            db.add(Setting(key="test_savepoint", value={"committed": True}))
        with space.sessions() as heartbeat:
            heartbeat.execute(text("SET LOCAL lock_timeout = '500ms'"))
            assert jobs.renew_lease(heartbeat, job.id, job.lease_owner)
        return {"saved": True}

    monkeypatch.setattr("app.ingest.runner.run_job", work_with_savepoint)
    assert jobs.run_job_id(job_id)["status"] == "succeeded"
    with space.sessions() as db:
        assert db.get(Setting, "test_savepoint").value == {"committed": True}


@pytest.mark.parametrize("operation", ["migrate", "ingest"])
def test_postgres_sync_and_purge_are_serialized_across_checkpoint_and_publication(postgres_workspace, monkeypatch, operation):
    from app.storage import service
    from app.storage.base import StorageError
    from app.storage.lifecycle import purge_location, retire_location
    from app.storage.local import LocalStorage
    space = postgres_workspace
    source = space.root / "replica-race.bin"
    source.write_bytes(b"isolated concurrent replica test" * 100)
    with space.sessions() as db:
        asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
        db.commit()
        target = StorageProfile(name="race-target", kind="local", config={"root": str(space.root / "race-target")})
        db.add(target); db.commit()
        location = migrate_asset(db, asset.id, target.id)
        db.commit()
        retire_location(db, location.id); db.commit()
        asset_id, target_id, location_id = asset.id, target.id, location.id
        target_path = service.get_adapter(target).path_for(location.object_key)

    verified, release_verify = threading.Event(), threading.Event()
    registered, release_commit = threading.Event(), threading.Event()
    purge_started, deleted = threading.Event(), threading.Event()
    original_register, original_delete = service._register_location, LocalStorage.delete

    def paused_register(db, asset, profile, key, info):
        if profile.id == target_id:
            # A multipart checkpoint commits the worker Session while its physical
            # operation remains in progress. The content advisory lock must survive.
            db.commit()
            verified.set()
            assert release_verify.wait(10)
        return original_register(db, asset, profile, key, info)

    def tracked_delete(store, key, **kwargs):
        if store.path_for(key) == target_path:
            deleted.set()
        return original_delete(store, key, **kwargs)

    monkeypatch.setattr(service, "_register_location", paused_register)
    monkeypatch.setattr(LocalStorage, "delete", tracked_delete)

    def synchronize():
        with space.sessions() as db:
            if operation == "migrate":
                service.migrate_asset(db, asset_id, target_id)
            else:
                service.ingest_file(db, source, kind="media", mime_type="video/mp4", profile_id=target_id)
            registered.set()
            # The content context has exited, but ready is not committed yet.
            # The Asset FOR UPDATE publication lock must still keep purge out.
            assert release_commit.wait(10)
            db.commit()

    def purge():
        with space.sessions() as db:
            purge_started.set()
            try:
                purge_location(db, location_id)
                db.commit()
                return "incorrectly_deleted"
            except StorageError:
                db.rollback()
                return "refused_ready_location"

    with ThreadPoolExecutor(max_workers=2) as pool:
        synchronize_future = pool.submit(synchronize)
        purge_future = None
        try:
            assert verified.wait(10)
            purge_future = pool.submit(purge)
            assert purge_started.wait(10)
            assert not deleted.wait(0.2)
            assert not purge_future.done(), "Purge entered while synchronization held its content lock"
            release_verify.set()
            assert registered.wait(10)
            assert not deleted.wait(0.2)
            assert not purge_future.done(), "Purge ignored the uncommitted registration publication lock"
        finally:
            release_verify.set()
            release_commit.set()
        synchronize_future.result(timeout=10)
        assert purge_future is not None and purge_future.result(timeout=10) == "refused_ready_location"
    with space.sessions() as db:
        assert db.get(AssetLocation, location_id).state == "ready"
        assert target_path.read_bytes() == source.read_bytes()
    assert not deleted.is_set()
