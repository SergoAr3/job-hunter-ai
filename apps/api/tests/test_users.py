from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_session
from app.main import app
from app.models import User

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def override_get_session():
    with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_session] = override_get_session
client = TestClient(app)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


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
    }

    created = client.post("/users/telegram", json=payload)
    existing = client.post(
        "/users/telegram",
        json={
            **payload,
            "first_name": "Анна-Мария",
            "last_name": "Петрова",
            "username": "anna_new",
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
