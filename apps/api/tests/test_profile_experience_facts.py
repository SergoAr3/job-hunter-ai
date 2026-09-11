from concurrent.futures import ThreadPoolExecutor

from app.models import ProfileExperienceFact, UserProfile
from conftest import TestSessionLocal, client


def create_user(telegram_id: int) -> int:
    return client.post(
        "/users/telegram", json={"telegram_id": telegram_id, "first_name": "Test"}
    ).json()["id"]


def create_profile(user_id: int) -> None:
    response = client.put(f"/users/{user_id}/profile", json={"target_roles": ["Backend Developer"]})
    assert response.status_code == 200


def create_fact(user_id: int, text: str = "Разрабатывал REST API на FastAPI") -> dict[str, object]:
    response = client.post(f"/users/{user_id}/profile/experience-facts", json={"text": text})
    assert response.status_code == 201
    return response.json()


def test_crud_normalizes_text_and_keeps_stable_id_after_edit():
    user_id = create_user(1001)
    create_profile(user_id)
    created = create_fact(user_id, "  Делал\n REST API на FastAPI  ")
    assert created["text"] == "Делал REST API на FastAPI"
    listed = client.get(f"/users/{user_id}/profile/experience-facts")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == created["id"]

    edited = client.put(
        f"/users/{user_id}/profile/experience-facts/{created['id']}",
        json={"text": "Интегрировал сторонние API"},
    )
    assert edited.status_code == 200
    assert edited.json()["id"] == created["id"]
    assert edited.json()["text"] == "Интегрировал сторонние API"

    assert client.delete(f"/users/{user_id}/profile/experience-facts/{created['id']}").status_code == 204
    assert client.get(f"/users/{user_id}/profile/experience-facts").json() == {"items": []}


def test_foreign_and_missing_fact_are_indistinguishable():
    owner, other = create_user(1002), create_user(1003)
    create_profile(owner)
    create_profile(other)
    fact = create_fact(owner)
    foreign = client.put(
        f"/users/{other}/profile/experience-facts/{fact['id']}", json={"text": "Другой текст"}
    )
    missing = client.put(
        f"/users/{owner}/profile/experience-facts/999999", json={"text": "Другой текст"}
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": {"code": "EXPERIENCE_FACT_NOT_FOUND"}}


def test_validation_duplicate_limit_and_ordinary_profile_put_does_not_touch_facts():
    user_id = create_user(1004)
    create_profile(user_id)
    fact = create_fact(user_id, "Немного работал с Docker")
    duplicate = client.post(
        f"/users/{user_id}/profile/experience-facts", json={"text": "  немного   работал с docker "}
    )
    assert duplicate.status_code == 422
    assert duplicate.json() == {"detail": {"code": "DUPLICATE_EXPERIENCE_FACT"}}
    for text in ("", "\x00", "text\x01", "x" * 501):
        assert client.post(f"/users/{user_id}/profile/experience-facts", json={"text": text}).status_code == 422
    for index in range(19):
        create_fact(user_id, f"Факт {index}")
    limited = client.post(f"/users/{user_id}/profile/experience-facts", json={"text": "Ещё один факт"})
    assert limited.status_code == 422
    assert limited.json() == {"detail": {"code": "EXPERIENCE_FACT_LIMIT_REACHED"}}

    replaced = client.put(f"/users/{user_id}/profile", json={"target_roles": ["Data Engineer"]})
    assert replaced.status_code == 200
    assert any(item["id"] == fact["id"] for item in client.get(
        f"/users/{user_id}/profile/experience-facts"
    ).json()["items"])


def test_concurrent_independent_facts_are_added_without_profile_replacement():
    user_id = create_user(1005)
    create_profile(user_id)

    def add(text: str) -> int:
        return client.post(f"/users/{user_id}/profile/experience-facts", json={"text": text}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(add, ["Разрабатывал backend на Python", "Работал с PostgreSQL"]))
    assert statuses == [201, 201]
    assert len(client.get(f"/users/{user_id}/profile/experience-facts").json()["items"]) == 2
    with TestSessionLocal() as session:
        assert session.query(UserProfile).count() == 1
        assert session.query(ProfileExperienceFact).count() == 2


def test_facts_cascade_when_profile_is_deleted():
    user_id = create_user(1006)
    create_profile(user_id)
    create_fact(user_id)
    with TestSessionLocal() as session:
        profile = session.query(UserProfile).filter_by(user_id=user_id).one()
        session.delete(profile)
        session.commit()
        assert session.query(ProfileExperienceFact).count() == 0
