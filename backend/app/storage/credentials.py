"""Persist provider refresh-token rotation without committing a caller's work."""
import json

from sqlalchemy import inspect, select, update, text
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import set_committed_value

from app.models import StorageProfile
from app.security import encrypt_secret


def rotation_callback(profile):
    expected = profile.secret_encrypted

    def save(credentials):
        nonlocal expected
        encrypted = encrypt_secret(json.dumps(credentials))
        db = object_session(profile)
        if db is None:
            return
        state = inspect(profile)
        if profile.secret_encrypted != expected:
            return  # Do not replace a credential explicitly changed by this caller.
        if not state.persistent or state.attrs.secret_encrypted.history.has_changes():
            # A freshly imported profile is not visible to another connection.
            profile.secret_encrypted = encrypted
            expected = encrypted
            return
        statement = update(StorageProfile).where(
            StorageProfile.id == profile.id, StorageProfile.secret_encrypted == expected
        ).values(secret_encrypted=encrypted).execution_options(synchronize_session=False)
        bind = db.get_bind()
        if bind.dialect.name == 'postgresql':
            # The public media GET may never commit its read transaction. Save
            # only the rotated credential, independently, with a bounded lock.
            engine = getattr(bind, 'engine', bind)
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                exists = connection.scalar(select(StorageProfile.id).where(StorageProfile.id == profile.id))
                if exists is None:
                    # A flushed INSERT is persistent to SQLAlchemy but remains
                    # invisible here until its owner's transaction commits.
                    profile.secret_encrypted = encrypted
                    expected = encrypted
                    return
                changed = connection.execute(statement).rowcount
        else:
            # SQLite is confined to tests/development and may share one in-memory
            # connection. Never commit the caller's unrelated writes implicitly.
            with db.no_autoflush:
                changed = db.execute(statement).rowcount
        if changed:
            expected = encrypted
            set_committed_value(profile, 'secret_encrypted', encrypted)
        # A newer administrator/worker credential wins a concurrent rotation.

    return save
