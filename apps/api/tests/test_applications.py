import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Application, Job
from app.models import User
from app.services.applications import save_application_for_user
from app.services.safe_http_fetcher import BlockedUrlError
import app.main as main_module
from conftest import TestSessionLocal, client


def create_user(telegram_id: int) -> int:
    response = client.post(
        "/users/telegram",
        json={"telegram_id": telegram_id, "first_name": "Анна"},
    )
    assert response.status_code == 200
    return response.json()["id"]


def save_application(user_id: int, source_url: str):
    return client.post(f"/users/{user_id}/applications", json={"source_url": source_url})


def test_first_request_creates_job_and_saved_application() -> None:
    user_id = create_user(1)

    response = save_application(user_id, "https://example.com/jobs/123")

    assert response.status_code == 200
    body = response.json()
    assert body["job_created"] is True
    assert body["application_created"] is True
    assert body["job"]["source"] == "company_site"
    assert body["job"]["ingestion_method"] == "manual"
    assert body["job"]["title"] is None
    assert body["job"]["company"] is None
    assert body["job"]["description"] is None
    assert body["application"]["status"] == "saved"
    assert body["application"]["job_id"] == body["job"]["id"]


def test_repeat_url_for_same_user_returns_existing_records() -> None:
    user_id = create_user(1)

    first = save_application(user_id, "https://example.com/jobs/123")
    repeated = save_application(user_id, "https://example.com/jobs/123")

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["job"]["id"] == first.json()["job"]["id"]
    assert repeated.json()["application"]["id"] == first.json()["application"]["id"]
    assert repeated.json()["job_created"] is False
    assert repeated.json()["application_created"] is False


def test_same_url_for_different_users_creates_two_applications_and_one_job() -> None:
    first_user_id = create_user(1)
    second_user_id = create_user(2)

    first = save_application(first_user_id, "https://example.com/jobs/123")
    second = save_application(second_user_id, "https://example.com/jobs/123")

    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert first.json()["application"]["id"] != second.json()["application"]["id"]
    assert second.json()["job_created"] is False
    assert second.json()["application_created"] is True


def test_different_urls_create_different_jobs() -> None:
    user_id = create_user(1)

    first = save_application(user_id, "https://example.com/jobs/123")
    second = save_application(user_id, "https://example.com/jobs/456")

    assert first.json()["job"]["id"] != second.json()["job"]["id"]


def test_nonexistent_user_returns_not_found() -> None:
    response = save_application(999, "https://example.com/jobs/123")

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


@pytest.mark.parametrize(
    "source_url",
    ["not a url", "/jobs/123", "ftp://example.com/jobs/123", "https://exa mple.com/jobs/123"],
)
def test_invalid_url_returns_validation_error(source_url: str) -> None:
    user_id = create_user(1)

    response = save_application(user_id, source_url)

    assert response.status_code == 422


def test_url_normalization_lowercases_scheme_and_host_and_removes_fragment() -> None:
    user_id = create_user(1)

    first = save_application(user_id, "HTTPS://EXAMPLE.COM/jobs/123?ref=telegram#details")
    repeated = save_application(user_id, "https://example.com/jobs/123?ref=telegram#other")

    assert first.status_code == 200
    assert first.json()["job"]["source_url"] == "https://example.com/jobs/123?ref=telegram"
    assert repeated.json()["job"]["id"] == first.json()["job"]["id"]
    assert repeated.json()["application_created"] is False


def test_database_unique_constraints_prevent_duplicate_jobs_and_applications() -> None:
    user_id = create_user(1)
    response = save_application(user_id, "https://example.com/jobs/123")
    job_id = response.json()["job"]["id"]

    with TestSessionLocal() as session:
        session.add(Job(source="company_site", source_url="https://example.com/jobs/123"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(Application(user_id=user_id, job_id=job_id, status="saved"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_database_status_check_rejects_unknown_status() -> None:
    user_id = create_user(1)
    response = save_application(user_id, "https://example.com/jobs/123")
    job_id = response.json()["job"]["id"]

    with TestSessionLocal() as session:
        session.add(Application(user_id=user_id, job_id=job_id, status="unknown"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_database_foreign_key_rejects_unknown_user() -> None:
    user_id = create_user(1)
    response = save_application(user_id, "https://example.com/jobs/123")
    job_id = response.json()["job"]["id"]

    with TestSessionLocal() as session:
        session.add(Application(user_id=999, job_id=job_id, status="saved"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_database_foreign_key_rejects_unknown_job() -> None:
    user_id = create_user(1)

    with TestSessionLocal() as session:
        session.add(Application(user_id=user_id, job_id=999, status="saved"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_enrichment_runs_without_active_database_transaction() -> None:
    class ProbeEnrichment:
        def preflight(self, url: str) -> None:
            return None

        def enrich(self, url: str):
            assert session.in_transaction() is False
            return None, "fetch_timeout"

        @staticmethod
        def values(data):
            return {}

    with TestSessionLocal() as session:
        user = User(telegram_id=100, first_name="Анна")
        session.add(user)
        session.commit()
        job, _, created, _ = save_application_for_user(
            session, user.id, "https://example.com/jobs/transaction", ProbeEnrichment()
        )
        assert created is True
        assert job.parsing_status == "failed"


def test_blocked_url_returns_422_without_persisting_records(monkeypatch) -> None:
    class BlockedEnrichment:
        def preflight(self, url: str) -> None:
            raise BlockedUrlError("Non-public address")

        def enrich(self, url: str):
            raise AssertionError("enrichment must not run")

    monkeypatch.setattr(main_module, "enrichment_service", BlockedEnrichment())
    user_id = create_user(42)
    response = save_application(user_id, "http://127.0.0.1/")
    assert response.status_code == 422
    assert response.json()["detail"] == "Unsafe URL"
    with TestSessionLocal() as session:
        assert session.query(Job).count() == 0
        assert session.query(Application).count() == 0


def test_safe_url_with_fetch_timeout_is_saved_as_failed() -> None:
    user_id = create_user(43)
    response = save_application(user_id, "https://example.com/jobs/timeout")
    assert response.status_code == 200
    assert response.json()["job"]["parsing_status"] == "failed"
