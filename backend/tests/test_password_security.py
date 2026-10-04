import pytest
from sqlalchemy import func, select, update

from app import main
from app.models import LoginAttempt, User, UserSession
from app.security import hash_password
from test_api import context  # noqa: F401


@pytest.mark.parametrize('change', ['password', 'disabled'])
def test_login_rechecks_account_after_expensive_password_verification(context, monkeypatch, change):
    client, db, _ = context
    user = db.scalar(select(User).where(User.username == 'admin'))
    verify = main.verify_password

    def race(password, encoded):
        valid = verify(password, encoded)
        values = {'disabled': True} if change == 'disabled' else {'password_hash': hash_password('changed-password-123')}
        # Simulate a committed competing request without refreshing the login's
        # existing ORM object; this reproduced stale session issuance before.
        db.execute(update(User).where(User.id == user.id).values(**values), execution_options={'synchronize_session': False})
        db.commit()
        return valid

    monkeypatch.setattr(main, 'verify_password', race)
    response = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'a-test-password-123'})
    assert response.status_code == 401
    assert db.scalar(select(func.count()).select_from(UserSession)) == 0
    assert db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(False))) == 1


def test_password_quota_is_reserved_before_scrypt_and_success_releases_it(context, monkeypatch):
    client, db, _ = context
    verify = main.verify_password

    def reserved(password, encoded):
        assert db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(False))) == 1
        return verify(password, encoded)

    monkeypatch.setattr(main, 'verify_password', reserved)
    response = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'a-test-password-123'})
    assert response.status_code == 200
    assert db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(False))) == 0
