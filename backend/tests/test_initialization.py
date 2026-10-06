import pytest
from sqlalchemy import delete, func, select
from app.cli import ensure_initial_admin
from app.config import settings
from app.models import User
from test_api import context


def test_upgrade_without_bootstrap_password_preserves_existing_admin(context, monkeypatch):
    client, db, _ = context
    monkeypatch.setattr(settings, "admin_password", "")
    monkeypatch.setattr(settings, "admin_username", "changed-config")
    assert ensure_initial_admin(db) is False
    assert db.scalar(select(func.count()).select_from(User)) == 2
    assert client.get("/api/v1/auth/status").json()["initialized"] is True


def test_first_setup_requires_password_and_is_idempotent(context, monkeypatch):
    client, db, _ = context
    db.execute(delete(User)); db.commit()
    assert client.get("/api/v1/auth/status").json()["initialized"] is False
    monkeypatch.setattr(settings, "admin_password", "")
    with pytest.raises(RuntimeError, match="首次初始化"):
        ensure_initial_admin(db)
    monkeypatch.setattr(settings, "admin_password", "fresh-setup-password-123")
    monkeypatch.setattr(settings, "admin_username", "owner")
    assert ensure_initial_admin(db) is True
    db.commit()
    assert ensure_initial_admin(db) is False
    assert client.post("/api/v1/auth/login", json={"username": "owner", "password": "fresh-setup-password-123"}).status_code == 200
