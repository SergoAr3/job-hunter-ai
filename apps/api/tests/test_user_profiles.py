import pytest

from app.models import UserProfile
from app.schemas import UserProfileOut, UserProfilePutIn
from app.services.profile_normalization import normalize_profile_skills
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


def test_profile_skills_are_canonicalized_at_the_domain_boundary() -> None:
    assert normalize_profile_skills([" python ", "PYTHON"]) == ["Python"]
    assert normalize_profile_skills(["postgres", "PostgreSQL", "postgresql"]) == [
        "PostgreSQL"
    ]


def test_profile_skill_normalization_endpoint_is_stateless() -> None:
    response = client.post(
        "/profile/skills/normalize",
        json={"skills": [" python ", "PYTHON", "postgres", "my   internal tool"]},
    )

    assert response.status_code == 200
    assert response.json() == {"skills": ["Python", "PostgreSQL", "my internal tool"]}
    assert normalize_profile_skills(["sql", "HTML", "api", "GIT", "redis"]) == [
        "SQL",
        "HTML",
        "API",
        "Git",
        "Redis",
    ]
    assert normalize_profile_skills(["my   internal tool", " ", "MY   INTERNAL TOOL"]) == [
        "my internal tool"
    ]


def test_manual_profile_payload_uses_normalized_skills() -> None:
    profile = UserProfilePutIn.model_validate(
        {"target_roles": ["Engineer"], "skills": [" redis ", "PYTHON", "python"]}
    )

    assert profile.skills == ["Redis", "Python"]


def test_profile_language_normalization_endpoint_is_stateless() -> None:
    response = client.post(
        "/profile/languages/normalize",
        json={
            "languages": [
                {"language": " English ", "level": "b1"},
                {"language": "Russian", "level": "NATIVE"},
                {"language": "Eastern   Armenian", "level": "c1"},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "languages": [
            {"language": "English", "level": "B1"},
            {"language": "Russian", "level": "native"},
            {"language": "Eastern Armenian", "level": "C1"},
        ]
    }


@pytest.mark.parametrize("level", ["conversational", "advanced", "unknown", "Английский", "qwerty", " "])
def test_profile_languages_reject_unsupported_levels(level: str) -> None:
    with pytest.raises(ValueError, match="language level must be one of"):
        UserProfilePutIn.model_validate(
            {"target_roles": ["Engineer"], "languages": [{"language": "English", "level": level}]}
        )


@pytest.mark.parametrize(
    "languages",
    [
        [{"language": "English", "level": "B1"}, {"language": "English", "level": "C1"}],
        [{"language": "English", "level": "B1"}, {"language": " english ", "level": "B1"}],
    ],
)
def test_profile_languages_reject_duplicate_normalized_names(languages: list[dict[str, str]]) -> None:
    with pytest.raises(ValueError, match="languages must not contain duplicate language names"):
        UserProfilePutIn.model_validate({"target_roles": ["Engineer"], "languages": languages})


def test_profile_languages_reject_malformed_repeated_words() -> None:
    with pytest.raises(ValueError, match="language level must be one of"):
        UserProfilePutIn.model_validate(
            {
                "target_roles": ["Engineer"],
                "languages": [{"language": "Английский Английский", "level": "Английский"}],
            }
        )


def test_profile_output_keeps_legacy_language_shape_readable() -> None:
    output = UserProfileOut.model_validate(
        {
            "user_id": 1,
            **complete_profile(),
            "languages": [{"language": "English English", "level": "English"}],
            "created_at": "2026-09-02T00:00:00Z",
            "updated_at": "2026-09-02T00:00:00Z",
        }
    )

    assert output.languages[0].model_dump() == {"language": "English English", "level": "English"}


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


@pytest.mark.parametrize(
    "location",
    [
        "remote", " REMOTE ", "hybrid", "onsite", "any", "Удалённо", "удаленно",
        "  Гибрид", "Любой", "На   месте работодателя",
    ],
)
def test_location_rejects_workplace_preferences(location: str) -> None:
    user_id = create_user()
    response = client.put(
        f"/users/{user_id}/profile",
        json={"target_roles": ["Engineer"], "location": [location]},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("location", ["Москва", "Berlin", "Yerevan"])
def test_location_keeps_free_text_geographic_values(location: str) -> None:
    profile = UserProfilePutIn.model_validate({"target_roles": ["Engineer"], "location": [location]})

    assert profile.location == [location]


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
