import json
import os

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Asset, StorageProfile
from app.security import decrypt_secret, encrypt_secret
from app.storage.credentials import rotation_callback
from app.storage.service import ingest_file
from test_api import context
from test_postgres_integration import postgres_workspace


def secret(token):
    return encrypt_secret(json.dumps({'client_id': 'fixture', 'refresh_token': token}))


def test_refresh_callback_does_not_overwrite_explicit_credential_change(context):
    _, db, _ = context
    profile = StorageProfile(name='Drive', kind='onedrive', config={'drive_id': 'fixture'}, secret_encrypted=secret('old'))
    db.add(profile); db.commit()
    rotate = rotation_callback(profile)
    explicit = secret('administrator-replacement')
    profile.secret_encrypted = explicit
    rotate({'client_id': 'fixture', 'refresh_token': 'provider-rotation'})
    assert profile.secret_encrypted == explicit


@pytest.mark.skipif(os.environ.get('TREASURE_RUN_POSTGRES_TESTS') != '1', reason='Explicit isolated PostgreSQL opt-in required')
def test_refresh_is_independent_of_uncommitted_asset_publication(postgres_workspace, monkeypatch):
    space = postgres_workspace
    monkeypatch.setattr(settings, 'secret_key', space.key)
    source = space.root / 'rotation.bin'; source.write_bytes(b'rotation lock fixture')
    with space.sessions() as db:
        profile = StorageProfile(name='Fixture', kind='local', config={'root': str(space.root / 'media')}, secret_encrypted=secret('old'))
        db.add(profile); db.commit()
        profile_id = profile.id
        asset = ingest_file(db, source, kind='media', mime_type='video/mp4', profile_id=profile_id)
        asset_id = asset.id
        # Asset registration retains the profile lock until this transaction
        # ends. Token refresh must still complete on its independent connection.
        rotation_callback(profile)({'client_id': 'fixture', 'refresh_token': 'new'})
        with space.sessions() as other:
            assert other.get(Asset, asset_id) is None
            assert json.loads(decrypt_secret(other.get(StorageProfile, profile_id).secret_encrypted))['refresh_token'] == 'new'
        db.rollback()
    with space.sessions() as db:
        assert db.get(Asset, asset_id) is None
        assert json.loads(decrypt_secret(db.get(StorageProfile, profile_id).secret_encrypted))['refresh_token'] == 'new'


@pytest.mark.skipif(os.environ.get('TREASURE_RUN_POSTGRES_TESTS') != '1', reason='Explicit isolated PostgreSQL opt-in required')
def test_refresh_handles_flushed_profile_and_concurrent_rotation(postgres_workspace, monkeypatch):
    space = postgres_workspace
    monkeypatch.setattr(settings, 'secret_key', space.key)
    with space.sessions() as db:
        profile = StorageProfile(name='Fresh', kind='onedrive', config={'drive_id': 'fixture'}, secret_encrypted=secret('old'))
        db.add(profile); db.flush()
        rotation_callback(profile)({'client_id': 'fixture', 'refresh_token': 'first'})
        db.commit()
        profile_id = profile.id
    with space.sessions() as stale, space.sessions() as fresh:
        old = stale.get(StorageProfile, profile_id)
        rotate_old = rotation_callback(old)
        latest = fresh.get(StorageProfile, profile_id)
        rotation_callback(latest)({'client_id': 'fixture', 'refresh_token': 'winner'})
        rotate_old({'client_id': 'fixture', 'refresh_token': 'stale-loser'})
        stale.rollback(); fresh.rollback()
    with space.sessions() as db:
        assert json.loads(decrypt_secret(db.get(StorageProfile, profile_id).secret_encrypted))['refresh_token'] == 'winner'
