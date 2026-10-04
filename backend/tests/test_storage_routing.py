from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AssetLocation, AssetRef, Base, BackupSet, PlaybackSession, StorageObservation, StorageProfile, User, utcnow
from app.storage import routing
from app.storage.base import StorageError
from app.storage.lifecycle import dependency_asset_ids, purge_location, restore_location, retire_location
from app.storage.service import get_adapter, ingest_file, migrate_asset


@pytest.fixture
def replicas(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = tmp_path / "source"
        source.write_bytes(b"sample payload" * 20000)
        asset = ingest_file(db, source, kind="media", mime_type="video/mp4")
        first = db.scalar(select(StorageProfile))
        second = StorageProfile(name="replica", kind="local", config={"root": str(tmp_path / "replica")})
        db.add(second)
        db.flush()
        migrate_asset(db, asset.id, second.id)
        db.commit()
        yield db, asset, first, second
    engine.dispose()


def test_routes_are_metadata_only_and_user_measurements_are_isolated(replicas, monkeypatch):
    db, asset, first, second = replicas
    monkeypatch.setattr(routing, "get_adapter", lambda *_: pytest.fail("route listing must not perform network I/O"))
    alice, bob = User(username="alice", password_hash="x"), User(username="bob", password_hash="x")
    db.add_all([alice, bob]); db.flush()
    routing.record_observation(db, first.id, user_id=alice.id, latency_ms=20, elapsed_ms=100, bytes_read=65536, succeeded=True)
    routing.record_observation(db, second.id, user_id=bob.id, latency_ms=1, elapsed_ms=2, bytes_read=65536, succeeded=True)
    routes = routing.playback_routes(db, [asset.id], user_id=alice.id)
    assert routes[0]["id"] == first.id
    assert routes[1]["latency_ms"] is None and routes[1]["status"] == "unmeasured"
    sample = db.scalar(select(StorageObservation).where(StorageObservation.user_id == alice.id))
    sample.measured_at = utcnow() - timedelta(seconds=routing.MEASUREMENT_TTL_SECONDS + 1)
    db.flush()
    assert all(r["status"] == "unmeasured" for r in routing.playback_routes(db, [asset.id], user_id=alice.id))


def test_probe_measures_bounded_real_bytes_and_labels_server_scope(replicas):
    db, asset, _, _ = replicas
    routes = routing.probe_routes(db, asset.id, max_candidates=99)
    assert len(routes) == 2
    assert all(r["status"] == "available" and r["measurement_scope"] == "server_storage" and r["throughput_bps"] > 0 for r in routes)
    samples = list(db.scalars(select(StorageObservation)))
    assert all(s.bytes_read == 128 * 1024 and s.user_id is None for s in samples)
    with pytest.raises(StorageError):
        routing.record_observation(db, routes[0]["id"], user_id="not-real", latency_ms=1, elapsed_ms=1, bytes_read=128 * 1024 + 1, succeeded=True)


def test_session_pins_route_and_fails_over_only_after_failure(replicas):
    db, asset, first, second = replicas
    session = PlaybackSession(user_id="test", variant_id="test", protocol="file", profile_id=first.id,
        expires_at=utcnow() + timedelta(hours=1), state={})
    db.add(session); db.flush()
    routing.record_observation(db, second.id, scope="server_storage", latency_ms=0.1, elapsed_ms=1, bytes_read=65536, succeeded=True)
    assert routing.resolve_session_asset(db, session, asset.id, [asset.id])["profile_id"] == first.id
    location = db.scalar(select(AssetLocation).where(AssetLocation.storage_profile_id == first.id))
    get_adapter(first).delete(location.object_key)
    assert routing.resolve_session_asset(db, session, asset.id, [asset.id])["profile_id"] == second.id
    assert session.state["switch_count"] == 1
    with pytest.raises(StorageError):
        routing.resolve_session_asset(db, session, "unauthorized-asset", [asset.id])


def test_retirement_checks_real_surviving_bytes_and_is_reversible(replicas):
    db, asset, first, second = replicas
    a = db.scalar(select(AssetLocation).where(AssetLocation.storage_profile_id == first.id))
    b = db.scalar(select(AssetLocation).where(AssetLocation.storage_profile_id == second.id))
    original = get_adapter(second).path_for(b.object_key).read_bytes()
    get_adapter(second).path_for(b.object_key).write_bytes(b"corrupt")
    with pytest.raises(StorageError):
        retire_location(db, a.id)
    assert a.state == "ready"
    get_adapter(second).path_for(b.object_key).write_bytes(original)
    retire_location(db, a.id)
    assert a.state == "retired" and get_adapter(first).path_for(a.object_key).exists()
    with pytest.raises(StorageError):
        retire_location(db, b.id)
    restore_location(db, a.id)
    assert a.state == "ready"


def test_purge_requires_retired_independent_survivor_and_active_backup_guard(replicas):
    db, asset, first, _ = replicas
    location = db.scalar(select(AssetLocation).where(AssetLocation.storage_profile_id == first.id))
    with pytest.raises(StorageError):
        purge_location(db, location.id)
    retire_location(db, location.id)
    backup = BackupSet(status="incomplete", manifest={"assets": [{"id": asset.id}]})
    db.add(backup); db.flush()
    with pytest.raises(StorageError, match="backup"):
        purge_location(db, location.id)
    backup.status = "complete"; db.flush()
    result = purge_location(db, location.id)
    assert result["state"] == "deleted" and result["physical_reclamation"] == "local_unlinked"
    assert not get_adapter(first).path_for(location.object_key).exists()
    with pytest.raises(StorageError):
        restore_location(db, location.id)
    assert purge_location(db, location.id)["physical_reclamation"] == "previously_deleted"


def test_purge_rejects_cross_profile_alias_even_when_other_replica_exists(replicas):
    db, asset, first, _ = replicas
    location = db.scalar(select(AssetLocation).where(AssetLocation.storage_profile_id == first.id))
    alias = StorageProfile(name="alias", kind="local", config=dict(first.config))
    db.add(alias); db.flush()
    migrate_asset(db, asset.id, alias.id)
    retire_location(db, location.id)
    with pytest.raises(StorageError, match="aliases"):
        purge_location(db, location.id)
    assert get_adapter(first).path_for(location.object_key).exists()


def test_purge_does_not_claim_success_when_provider_does_not_remove_object(replicas, monkeypatch):
    from app.storage.local import LocalStorage
    db, _, first, _ = replicas
    location = db.scalar(select(AssetLocation).where(AssetLocation.storage_profile_id == first.id))
    retire_location(db, location.id)
    monkeypatch.setattr(LocalStorage, "delete", lambda *args, **kwargs: None)
    with pytest.raises(StorageError, match="did not confirm"):
        purge_location(db, location.id)
    assert location.state == "retired" and get_adapter(first).path_for(location.object_key).exists()


def test_dependency_closure_includes_package_and_original_and_handles_cycles(replicas, tmp_path):
    db, asset, _, _ = replicas
    path = tmp_path / "index"; path.write_bytes(b"index")
    index = ingest_file(db, path, kind="hls_index", mime_type="application/json")
    db.add_all([AssetRef(asset_id=asset.id, entity_type="asset", entity_id=index.id, purpose="hls_source"),
        AssetRef(asset_id=index.id, entity_type="asset", entity_id=asset.id, purpose="test_cycle")]); db.flush()
    assert set(dependency_asset_ids(db, [index.id])) == {index.id, asset.id}


def test_content_mutex_survives_commits_and_serializes_threads(replicas):
    from app.storage.locking import StorageBusy, content_lock
    db, asset, _, _ = replicas
    entered, release = threading.Event(), threading.Event()
    engine = create_engine("sqlite://")
    def holder():
        with Session(engine) as separate:
            with content_lock(separate, asset.sha256):
                separate.commit()
                entered.set()
                assert release.wait(5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(holder)
        try:
            assert entered.wait(5)
            with pytest.raises(StorageBusy):
                with content_lock(db, asset.sha256, timeout=0.05):
                    pytest.fail("Checkpoint commit released the content mutex")
        finally:
            release.set()
        future.result(timeout=5)
    with content_lock(db, asset.sha256, timeout=0.05):
        pass
    engine.dispose()


def test_disabled_during_verification_does_not_reactivate_retired_location(replicas, monkeypatch):
    from sqlalchemy import update
    from app.storage.local import LocalStorage
    db, asset, _, target = replicas
    location = db.scalar(select(AssetLocation).where(AssetLocation.storage_profile_id == target.id))
    retire_location(db, location.id); db.commit()
    original = LocalStorage.verify
    def disable_after_readback(store, *args, **kwargs):
        info = original(store, *args, **kwargs)
        if str(store.root) == target.config['root']:
            db.execute(update(StorageProfile).where(StorageProfile.id == target.id).values(enabled=False)
                       .execution_options(synchronize_session=False))
        return info
    monkeypatch.setattr(LocalStorage, 'verify', disable_after_readback)
    with pytest.raises(StorageError, match='placement changed'):
        migrate_asset(db, asset.id, target.id)
    db.refresh(location)
    assert location.state == 'retired'
