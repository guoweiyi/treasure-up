"""Opt-in real PostgreSQL publication/configuration races, in isolated databases."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app import main, schemas
from app.models import AssetLocation, StorageProfile
from app.storage import service
from app.storage.base import StorageError
from test_postgres_integration import postgres_workspace

pytestmark = pytest.mark.skipif(os.environ.get('TREASURE_RUN_POSTGRES_TESTS') != '1',
                                reason='Explicit TREASURE_RUN_POSTGRES_TESTS=1 required')


@pytest.mark.parametrize('operation', ['ingest', 'migrate'])
@pytest.mark.parametrize('change', ['placement', 'disabled'])
def test_configuration_change_during_upload_cannot_publish_ready(postgres_workspace, monkeypatch, operation, change):
    space = postgres_workspace
    source = space.root / 'configuration-race.bin'
    source.write_bytes(b'isolated configuration race payload' * 256)
    with space.sessions() as db:
        asset = service.ingest_file(db, source, kind='media', mime_type='video/mp4')
        db.commit()
        target = StorageProfile(name='empty target', kind='local', config={'root': str(space.root / 'old')})
        db.add(target); db.commit()
        asset_id, target_id = asset.id, target.id

    verified, resume = threading.Event(), threading.Event()
    original = service._register_location
    def paused(db, asset, profile, key, info, **kwargs):
        if profile.id == target_id:
            # A checkpoint can commit while the adapter still refers to the old
            # root. The captured placement must survive that commit.
            db.commit()
            verified.set()
            assert resume.wait(10)
        return original(db, asset, profile, key, info, **kwargs)
    monkeypatch.setattr(service, '_register_location', paused)

    def upload():
        with space.sessions() as db:
            try:
                if operation == 'ingest':
                    service.ingest_file(db, source, kind='media', mime_type='video/mp4', profile_id=target_id)
                else:
                    service.migrate_asset(db, asset_id, target_id)
                db.commit()
                return 'incorrectly_published'
            except StorageError:
                db.rollback()
                return 'refused'

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(upload)
        try:
            assert verified.wait(10)
            with space.sessions() as admin:
                service.lock_storage_configuration(admin)
                target = admin.scalar(select(StorageProfile).where(StorageProfile.id == target_id)
                                      .with_for_update().execution_options(populate_existing=True))
                assert admin.scalar(select(AssetLocation.id).where(AssetLocation.storage_profile_id == target_id)) is None
                if change == 'placement':
                    target.config = {'root': str(space.root / 'new')}
                else:
                    target.enabled = False
                admin.commit()
        finally:
            resume.set()
        assert task.result(timeout=10) == 'refused'
    with space.sessions() as db:
        assert db.scalar(select(AssetLocation.id).where(AssetLocation.storage_profile_id == target_id)) is None
        assert service.read_asset_bytes(db, asset_id) == source.read_bytes()


def test_registered_location_prevents_late_configuration_change(postgres_workspace):
    space = postgres_workspace
    source = space.root / 'publication-race.bin'
    source.write_bytes(b'publication wins')
    with space.sessions() as db:
        profile = service.default_profile(db)
        db.commit()
        profile_id = profile.id
    registered, release, admin_started = threading.Event(), threading.Event(), threading.Event()

    def publish():
        with space.sessions() as db:
            service.ingest_file(db, source, kind='media', mime_type='video/mp4', profile_id=profile_id)
            registered.set()
            assert release.wait(10)
            db.commit()

    def edit():
        with space.sessions() as db:
            service.lock_storage_configuration(db)
            admin_started.set()
            db.scalar(select(StorageProfile).where(StorageProfile.id == profile_id)
                      .with_for_update().execution_options(populate_existing=True))
            # This is the guard used by the API after locking the profile.
            return db.scalar(select(AssetLocation.id).where(AssetLocation.storage_profile_id == profile_id)) is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        publisher = pool.submit(publish)
        editor = None
        try:
            assert registered.wait(10)
            editor = pool.submit(edit)
            assert admin_started.wait(10)
            assert not threading.Event().wait(0.2) and not editor.done()
        finally:
            release.set()
        publisher.result(timeout=10)
        assert editor is not None and editor.result(timeout=10)


def test_parallel_first_default_creation_rechecks_after_configuration_lock(postgres_workspace, monkeypatch):
    space = postgres_workspace
    barrier = threading.Barrier(2)
    original = service.lock_storage_configuration
    def both_saw_empty(db):
        barrier.wait(timeout=10)
        original(db)
    monkeypatch.setattr(service, 'lock_storage_configuration', both_saw_empty)
    def initialize():
        with space.sessions() as db:
            profile = service.default_profile(db)
            db.commit()
            return profile.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: initialize(), range(2)))
    assert ids[0] == ids[1]
    with space.sessions() as db:
        assert len(list(db.scalars(select(StorageProfile)))) == 1


def test_unrelated_assets_register_while_another_publication_is_uncommitted(postgres_workspace):
    space = postgres_workspace
    first_source, second_source = space.root / 'first.bin', space.root / 'second.bin'
    first_source.write_bytes(b'first publication remains open')
    second_source.write_bytes(b'an unrelated publication can commit')
    with space.sessions() as db:
        profile_id = service.default_profile(db).id
        db.commit()
    registered, release = threading.Event(), threading.Event()

    def hold_first():
        with space.sessions() as db:
            service.ingest_file(db, first_source, kind='media', mime_type='video/mp4', profile_id=profile_id)
            registered.set()
            assert release.wait(10)
            db.commit()

    def publish_second():
        with space.sessions() as db:
            # An exclusive profile lock would hit this bounded server timeout.
            db.execute(text("SET LOCAL statement_timeout = '1500ms'"))
            asset = service.ingest_file(db, second_source, kind='media', mime_type='video/mp4', profile_id=profile_id)
            db.commit()
            return asset.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hold_first)
        try:
            assert registered.wait(10)
            second = pool.submit(publish_second)
            second_id = second.result(timeout=5)
            assert not first.done()  # No caller commit released the first profile lock.
            with space.sessions() as db:
                assert db.scalar(select(AssetLocation.id).where(AssetLocation.asset_id == second_id))
        finally:
            release.set()
        first.result(timeout=10)
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AssetLocation)) == 2


def test_administrator_rejects_busy_configuration_writer_without_waiting(postgres_workspace):
    space = postgres_workspace
    body = schemas.StorageInput(name='new', kind='local', config={'root': str(space.root / 'primary' / 'media')})
    with space.sessions() as holder, space.sessions() as admin:
        service.lock_storage_configuration(holder)
        admin.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        with pytest.raises(HTTPException) as failed:
            main.write_storage(admin, body)
        assert failed.value.status_code == 409
        assert admin.scalar(select(func.count()).select_from(StorageProfile)) == 0
        holder.rollback()
        created = main.write_storage(admin, body)
        admin.commit()
        assert created.name == 'new'


@pytest.mark.parametrize('busy_row', ['target', 'previous_default'])
def test_administrator_rejects_busy_profile_and_releases_global_writer_lock(postgres_workspace, busy_row):
    space = postgres_workspace
    with space.sessions() as db:
        first = service.default_profile(db)
        second = StorageProfile(name='other', kind='local', config={'root': str(space.root / 'primary' / 'media' / 'other')})
        db.add(second)
        db.commit()
        first_id, second_id = first.id, second.id
    source = space.root / 'held-publication.bin'
    source.write_bytes(b'uncommitted publication protects its profile')
    target_id = first_id if busy_row == 'target' else second_id
    with space.sessions() as holder, space.sessions() as admin:
        service.ingest_file(holder, source, kind='media', mime_type='video/mp4', profile_id=first_id)
        target = admin.get(StorageProfile, target_id)
        body = schemas.StorageInput(name='edited', kind=target.kind, config=dict(target.config), is_default=True)
        admin.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        with pytest.raises(HTTPException) as failed:
            main.write_storage(admin, body, target)
        assert failed.value.status_code == 409
        # NOWAIT errors abort a PostgreSQL transaction. The API must roll it
        # back so this Session is reusable and the global writer lock is free.
        assert admin.scalar(select(StorageProfile.is_default).where(StorageProfile.id == first_id)) is True
        with space.sessions() as independent:
            assert service.lock_storage_configuration(independent, wait=False)
            independent.rollback()
        holder.commit()
        updated = main.write_storage(admin, body, admin.get(StorageProfile, target_id))
        admin.commit()
        assert updated.name == 'edited' and updated.is_default
    with space.sessions() as db:
        assert db.scalar(select(func.count()).select_from(StorageProfile).where(StorageProfile.is_default.is_(True))) == 1
