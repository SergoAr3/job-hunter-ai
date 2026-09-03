from decimal import Decimal

from app.models import Application, Job
from conftest import TestSessionLocal, client


def create_user(telegram_id: int) -> int:
    response = client.post("/users/telegram", json={"telegram_id": telegram_id, "first_name": "Anna"})
    return response.json()["id"]


def put_profile(user_id: int, **changes: object) -> None:
    data: dict[str, object] = {"target_roles": ["Backend Developer"], "skills": ["Python"], "experience": "middle"}
    data.update(changes)
    assert client.put(f"/users/{user_id}/profile", json=data).status_code == 200


def create_application(user_id: int, **job_changes: object) -> tuple[int, int]:
    with TestSessionLocal() as session:
        values: dict[str, object] = {
            "source": "company_site",
            "source_url": f"https://example.com/{user_id}/{job_changes.get('title', 'job')}",
            "title": "Backend Developer",
            "required_skills": ["Python"],
            "seniority": "middle",
            "parsing_status": "partial",
            "ai_enrichment_status": "success",
        }
        values.update(job_changes)
        job = Job(**values)
        session.add(job)
        session.flush()
        application = Application(user_id=user_id, job_id=job.id, status="saved")
        session.add(application)
        session.commit()
        return application.id, job.id


def test_owner_gets_on_demand_match_and_algorithm_version() -> None:
    user_id = create_user(1)
    put_profile(user_id)
    application_id, _ = create_application(user_id)
    response = client.get(f"/users/{user_id}/applications/{application_id}/match")
    assert response.status_code == 200
    assert response.json()["algorithm_version"] == "job-match-v1"


def test_foreign_and_missing_application_are_indistinguishable() -> None:
    owner_id, foreign_id = create_user(2), create_user(3)
    put_profile(owner_id)
    application_id, _ = create_application(owner_id)
    foreign = client.get(f"/users/{foreign_id}/applications/{application_id}/match")
    missing = client.get(f"/users/{owner_id}/applications/999999/match")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": {"code": "APPLICATION_NOT_FOUND"}}


def test_profile_required_and_partial_job_result() -> None:
    user_id = create_user(4)
    application_id, _ = create_application(user_id, ai_enrichment_status="failed", required_skills=[])
    missing = client.get(f"/users/{user_id}/applications/{application_id}/match")
    assert missing.status_code == 409
    assert missing.json() == {"detail": {"code": "PROFILE_REQUIRED"}}
    put_profile(user_id)
    partial = client.get(f"/users/{user_id}/applications/{application_id}/match")
    assert partial.status_code == 200
    assert partial.json()["components"]["required_skills"]["score"] is None


def test_profile_and_job_updates_are_immediately_reflected() -> None:
    user_id = create_user(5)
    put_profile(user_id, skills=["Python"])
    application_id, job_id = create_application(user_id)
    first = client.get(f"/users/{user_id}/applications/{application_id}/match").json()
    put_profile(user_id, skills=[])
    second = client.get(f"/users/{user_id}/applications/{application_id}/match").json()
    put_profile(user_id, skills=["Python"])
    with TestSessionLocal() as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.required_skills = ["Python", "SQL"]
        session.commit()
    third = client.get(f"/users/{user_id}/applications/{application_id}/match").json()
    assert first["components"]["required_skills"]["score"] == 100
    assert second["components"]["required_skills"]["score"] is None
    assert third["components"]["required_skills"]["score"] == 50


def test_api_match_applies_role_and_skill_aliases_without_changing_match_rules() -> None:
    user_id = create_user(6)
    put_profile(
        user_id,
        target_roles=["Backend-разработчик"],
        skills=["REST"],
    )
    application_id, _ = create_application(
        user_id,
        title="Backend Developer",
        required_skills=["REST API"],
    )

    response = client.get(f"/users/{user_id}/applications/{application_id}/match")

    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["role"] == {
        "weight": 20,
        "score": 100,
        "status": "matched",
        "matched": ["Backend-разработчик"],
        "missing": [],
    }
    assert payload["components"]["required_skills"] == {
        "weight": 30,
        "score": 100,
        "status": "matched",
        "matched": ["REST API"],
        "missing": [],
    }
    assert payload["coverage"] == 65
    assert payload["verdict"] == "high"
