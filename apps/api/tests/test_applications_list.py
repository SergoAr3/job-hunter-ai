from datetime import datetime, timedelta, timezone

from app.models import Application, Job
from conftest import TestSessionLocal, client


def _user(telegram_id: int) -> int:
    return client.post("/users/telegram", json={"telegram_id": telegram_id, "first_name": "Anna"}).json()["id"]


def _application(user_id: int, title: str, created_at: datetime) -> int:
    with TestSessionLocal() as session:
        job = Job(
            source="company_site",
            source_url=f"https://example.com/{user_id}/{title}",
            title=title,
            workplace_type="remote",
        )
        session.add(job)
        session.flush()
        application = Application(user_id=user_id, job_id=job.id, status="saved", created_at=created_at)
        session.add(application)
        session.commit()
        return application.id


def test_applications_page_is_newest_first_and_compact() -> None:
    user_id = _user(100)
    now = datetime.now(timezone.utc)
    older = _application(user_id, "Older", now - timedelta(days=1))
    newer = _application(user_id, "Newer", now)
    response = client.get(f"/users/{user_id}/applications?limit=1&offset=0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_next"] is True
    assert payload["items"][0]["app_id"] == newer
    assert set(payload["items"][0]) == {"app_id", "job_id", "created_at", "title", "company", "location", "workplace_type", "parsing_status", "ai_enrichment_status"}
    assert client.get(f"/users/{user_id}/applications?limit=1&offset=1").json()["items"][0]["app_id"] == older


def test_application_detail_is_user_scoped() -> None:
    owner, foreign = _user(101), _user(102)
    app_id = _application(owner, "Backend", datetime.now(timezone.utc))
    assert client.get(f"/users/{owner}/applications/{app_id}").status_code == 200
    missing = client.get(f"/users/{owner}/applications/99999")
    foreign_response = client.get(f"/users/{foreign}/applications/{app_id}")
    assert missing.status_code == foreign_response.status_code == 404
    assert missing.json() == foreign_response.json() == {"detail": {"code": "APPLICATION_NOT_FOUND"}}


def test_applications_page_validates_bounds() -> None:
    user_id = _user(103)
    assert client.get(f"/users/{user_id}/applications?limit=0").status_code == 422
    assert client.get(f"/users/{user_id}/applications?limit=6").status_code == 422
    assert client.get(f"/users/{user_id}/applications?offset=-1").status_code == 422
