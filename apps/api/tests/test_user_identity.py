from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import User
from conftest import TestSessionLocal, client


@pytest.mark.parametrize("telegram_id,email", [(123, None), (None, "User@Example.com"), (123, "User@Example.com")])
def test_user_supports_both_identity_methods(telegram_id, email):
    with TestSessionLocal() as session:
        user = User(
            telegram_id=telegram_id, email=email,
            email_canonical="user@example.com" if email else None,
            password_hash="test-only-placeholder" if email else None,
        )
        session.add(user)
        session.commit()
        assert session.get(User, user.id).telegram_id == telegram_id


@pytest.mark.parametrize("fields", [
    {},
    {"telegram_id": 123, "email": "user@example.com"},
    {"telegram_id": 123, "password_hash": "placeholder"},
    {"telegram_id": 123, "email_canonical": "user@example.com"},
    {"telegram_id": 123, "email": "user@example.com", "password_hash": "placeholder"},
    {"telegram_id": 123, "email_canonical": "user@example.com", "password_hash": "placeholder"},
    {"telegram_id": 123, "email": "user@example.com", "email_canonical": "user@example.com"},
    {"email": "user@example.com", "email_canonical": "User@example.com", "password_hash": "placeholder"},
    {"email": "user@example.com", "email_canonical": "other@example.com", "password_hash": "placeholder"},
    {"email": "Üser@example.com", "email_canonical": "üser@example.com", "password_hash": "placeholder"},
    {"email": "user@example.com\n", "email_canonical": "user@example.com\n", "password_hash": "placeholder"},
    {"email": " ", "email_canonical": "", "password_hash": "placeholder"},
    {"email": "user@example.com", "email_canonical": "user@example.com", "password_hash": " "},
    {"telegram_id": 123, "email_verified_at": datetime.now(timezone.utc)},
])
def test_invalid_identity_is_rejected_and_session_recovers(fields):
    with TestSessionLocal() as session:
        session.add(User(**fields))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(User(telegram_id=456))
        session.commit()
        assert session.query(User).count() == 1


def test_canonical_email_is_unique_across_original_case():
    with TestSessionLocal() as session:
        session.add(User(email="User@Example.com", email_canonical="user@example.com", password_hash="placeholder"))
        session.commit()
        session.add(User(email="user@example.com", email_canonical="user@example.com", password_hash="placeholder"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.query(User).count() == 1


def test_telegram_refresh_preserves_account_credentials_and_response_projection():
    verified_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with TestSessionLocal() as session:
        user = User(
            telegram_id=123, first_name="Old", username="old", last_name="Old", language_code="ru",
            email="User@Example.com", email_canonical="user@example.com",
            password_hash="test-only-secret-hash", email_verified_at=verified_at, display_name="Web name",
        )
        session.add(user)
        session.commit()
        user_id = user.id
        created_at = user.created_at
        before_verified_at = session.get(User, user_id).email_verified_at

    response = client.post("/users/telegram", json={
        "telegram_id": 123, "first_name": "New", "username": "new", "language_code": "en",
    })
    assert response.status_code == 200
    assert response.json() == {"id": user_id, "telegram_id": 123, "created": False}
    assert "test-only-secret-hash" not in response.text
    with TestSessionLocal() as session:
        user = session.get(User, user_id)
        assert session.query(User).count() == 1
        assert (user.first_name, user.username, user.last_name, user.language_code) == ("New", "new", None, "en")
        assert (user.email, user.email_canonical, user.password_hash, user.display_name) == (
            "User@Example.com", "user@example.com", "test-only-secret-hash", "Web name",
        )
        # SQLite returns naive timestamps after a DB reload.
        assert user.email_verified_at.replace(tzinfo=timezone.utc) == before_verified_at.replace(tzinfo=timezone.utc)
        assert user.created_at == created_at
