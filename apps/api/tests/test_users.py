from app.models import User
from conftest import TestSessionLocal, client


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_telegram_user_is_created_then_profile_is_updated() -> None:
    payload = {
        "telegram_id": 123456,
        "first_name": "Анна",
        "last_name": "Иванова",
        "username": "anna",
        "language_code": "ru",
    }

    created = client.post("/users/telegram", json=payload)
    existing = client.post(
        "/users/telegram",
        json={
            **payload,
            "first_name": "Анна-Мария",
            "last_name": "Петрова",
            "username": "anna_new",
            "language_code": "en",
        },
    )

    assert created.status_code == 200
    assert created.json()["created"] is True
    assert existing.status_code == 200
    assert existing.json() == {**created.json(), "created": False}

    with TestSessionLocal() as session:
        users = session.query(User).all()
        assert len(users) == 1
        assert users[0].username == "anna_new"
        assert users[0].first_name == "Анна-Мария"
        assert users[0].last_name == "Петрова"
        assert users[0].language_code == "en"
