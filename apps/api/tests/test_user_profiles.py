from app.models import UserProfile
from conftest import TestSessionLocal, client


def create_user() -> int:
    response = client.post(
        "/users/telegram",
        json={"telegram_id": 987654, "first_name": "Анна", "username": "anna"},
    )
    assert response.status_code == 200
    return response.json()["id"]


def complete_profile() -> dict[str, object]:
    return {
        "target_roles": [" Python Backend Developer ", "ML Engineer"],
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        "experience": "middle",
        "location": ["Yerevan", "Tbilisi"],
        "workplace_preference": "remote",
        "salary_min": 2500,
        "salary_currency": "usd",
        "salary_period": "month",
        "languages": [
            {"language": "English", "level": "B2"},
            {"language": "Russian", "level": "native"},
        ],
    }


def test_put_creates_profile_and_get_returns_it() -> None:
    user_id = create_user()

    created = client.put(f"/users/{user_id}/profile", json=complete_profile())
    fetched = client.get(f"/users/{user_id}/profile")

    assert created.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    payload = created.json()
    assert payload["user_id"] == user_id
    assert payload["target_roles"] == ["Python Backend Developer", "ML Engineer"]
    assert payload["skills"] == ["Python", "FastAPI", "PostgreSQL"]
    assert payload["experience"] == "middle"
    assert payload["location"] == ["Yerevan", "Tbilisi"]
    assert payload["workplace_preference"] == "remote"
    assert payload["salary_min"] == "2500.00"
    assert payload["salary_currency"] == "USD"
    assert payload["salary_period"] == "month"
    assert payload["languages"] == [
        {"language": "English", "level": "B2"},
        {"language": "Russian", "level": "native"},
    ]


def test_put_is_idempotent_and_fully_replaces_profile() -> None:
    user_id = create_user()
    first = client.put(f"/users/{user_id}/profile", json=complete_profile())
    replacement = {"target_roles": ["Data Engineer"]}

    replaced = client.put(f"/users/{user_id}/profile", json=replacement)
    repeated = client.put(f"/users/{user_id}/profile", json=replacement)

    assert first.status_code == replaced.status_code == repeated.status_code == 200
    for response in (replaced, repeated):
        payload = response.json()
        assert payload["target_roles"] == ["Data Engineer"]
        assert payload["skills"] == []
        assert payload["experience"] == "unknown"
        assert payload["location"] == []
        assert payload["workplace_preference"] == "any"
        assert payload["salary_min"] is None
        assert payload["salary_currency"] is None
        assert payload["salary_period"] == "unknown"
        assert payload["languages"] == []

    with TestSessionLocal() as session:
        assert session.query(UserProfile).count() == 1


def test_profile_endpoints_return_not_found_for_missing_resources() -> None:
    user_id = create_user()

    assert client.get(f"/users/{user_id}/profile").status_code == 404
    assert client.get("/users/999999/profile").status_code == 404
    assert client.put("/users/999999/profile", json={"target_roles": ["Engineer"]}).status_code == 404


def test_target_roles_are_required_and_cannot_be_empty() -> None:
    user_id = create_user()

    assert client.put(f"/users/{user_id}/profile", json={}).status_code == 422
    assert client.put(f"/users/{user_id}/profile", json={"target_roles": []}).status_code == 422
    assert client.put(f"/users/{user_id}/profile", json={"target_roles": [" "]}).status_code == 422


def test_location_rejects_workplace_preferences() -> None:
    user_id = create_user()
    response = client.put(
        f"/users/{user_id}/profile",
        json={"target_roles": ["Engineer"], "location": ["Remote"]},
    )
    assert response.status_code == 422


def test_profile_rejects_invalid_enum_values() -> None:
    user_id = create_user()
    for field, value in (
        ("experience", "staff"),
        ("workplace_preference", "office"),
        ("salary_period", "week"),
    ):
        response = client.put(
            f"/users/{user_id}/profile",
            json={"target_roles": ["Engineer"], field: value},
        )
        assert response.status_code == 422


def test_salary_accepts_complete_block_or_fully_absent_block() -> None:
    user_id = create_user()
    absent = client.put(f"/users/{user_id}/profile", json={"target_roles": ["Engineer"]})
    present = client.put(
        f"/users/{user_id}/profile",
        json={
            "target_roles": ["Engineer"],
            "salary_min": "1000.50",
            "salary_currency": "eur",
            "salary_period": "year",
        },
    )
    assert absent.status_code == present.status_code == 200
    assert present.json()["salary_currency"] == "EUR"


def test_salary_rejects_nonpositive_invalid_currency_and_partial_blocks() -> None:
    user_id = create_user()
    invalid_blocks = (
        {"salary_min": 0, "salary_currency": "USD", "salary_period": "month"},
        {"salary_min": -1, "salary_currency": "USD", "salary_period": "month"},
        {"salary_min": 1000, "salary_currency": "ABC", "salary_period": "month"},
        {"salary_min": 1000},
        {"salary_min": 1000, "salary_currency": "USD"},
        {"salary_currency": "USD", "salary_period": "month"},
        {"salary_period": "month"},
    )
    for salary in invalid_blocks:
        response = client.put(
            f"/users/{user_id}/profile",
            json={"target_roles": ["Engineer"], **salary},
        )
        assert response.status_code == 422
