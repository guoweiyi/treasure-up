"""An actual upgrade retires only the mistakenly introduced personal credentials."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select

from app.models import utcnow
from test_postgres_integration import postgres_workspace, pytestmark


def test_retirement_preserves_password_passkey_sessions_and_does_not_resurrect_tokens(postgres_workspace):
    space = postgres_workspace
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with space.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "f1476d8eb953")
        metadata = MetaData()
        tables = {name: Table(name, metadata, autoload_with=connection, resolve_fks=False)
                  for name in ("app_users", "sessions", "passkey_credentials", "identity_tokens")}
        now = utcnow()
        user_id, key_id, token_id = (str(uuid4()) for _ in range(3))
        password_session, passkey_session, token_session = (str(uuid4()) for _ in range(3))
        common = {"created_at": now, "updated_at": now}
        connection.execute(tables["app_users"].insert().values(**common, id=user_id, username="test", password_hash="test", role="admin", disabled=False))
        connection.execute(tables["passkey_credentials"].insert().values(**common, id=key_id, user_id=user_id,
            credential_id="public-fixture", public_key="public-fixture", name="passkey", transports=[], device_type="single_device"))
        connection.execute(tables["identity_tokens"].insert().values(**common, id=token_id, user_id=user_id,
            name="retired", token_hash="a" * 64, expires_at=now + timedelta(days=1)))
        for index, (session_id, passkey_id, identity_id) in enumerate(((password_session, None, None), (passkey_session, key_id, None), (token_session, None, token_id))):
            connection.execute(tables["sessions"].insert().values(**common, id=session_id, user_id=user_id,
                token_hash=str(index) * 64, csrf_token="test", expires_at=now + timedelta(hours=1),
                passkey_credential_id=passkey_id, identity_token_id=identity_id))
        command.upgrade(config, "head")
        inspector = inspect(connection)
        assert "identity_tokens" not in inspector.get_table_names()
        assert "identity_token_id" not in {column["name"] for column in inspector.get_columns("sessions")}
        assert set(connection.scalars(select(tables["sessions"].c.id))) == {password_session, passkey_session}
        assert connection.scalar(select(tables["passkey_credentials"].c.id)) == key_id
        command.downgrade(config, "f1476d8eb953")
        assert list(connection.scalars(select(tables["identity_tokens"].c.id))) == []
        command.upgrade(config, "head")
