from datetime import datetime, timedelta, timezone

import pytest

from app.models import Application, ApplicationStatus, Job
from conftest import TestSessionLocal, client


def _user(telegram_id: int) -> int:
    return client.post("/users/telegram", json={"telegram_id": telegram_id, "first_name": "Anna"}).json()["id"]


def _application(
    user_id: int, title: str, created_at: datetime, status: str = "saved", company: str | None = None,
) -> int:
    with TestSessionLocal() as session:
        job = Job(
            source="company_site",
            source_url=f"https://example.com/{user_id}/{title}",
            title=title,
            company=company,
            workplace_type="remote",
        )
        session.add(job)
        session.flush()
        application = Application(user_id=user_id, job_id=job.id, status=status, created_at=created_at)
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
    assert set(payload["items"][0]) == {"app_id", "status", "job_id", "created_at", "title", "company", "location", "workplace_type", "parsing_status", "ai_enrichment_status"}
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


@pytest.mark.parametrize("status", list(ApplicationStatus))
def test_status_filter_is_user_scoped_and_precedes_pagination(status):
    owner, other = _user(104), _user(105)
    now = datetime.now(timezone.utc)
    ids = []
    different = "applied" if status.value == "saved" else "saved"
    for index in range(6):
        ids.append(_application(owner, f"match-{index}", now, status.value))
        _application(owner, f"nonmatch-{index}", now, different)
    _application(other, "foreign", now + timedelta(days=1), status.value)
    # A shared Job still has independent per-user application statuses.
    with TestSessionLocal() as session:
        own = session.get(Application, ids[0])
        assert own is not None
        session.add(Application(user_id=other, job_id=own.job_id, status=different))
        session.commit()
    url = f"/users/{owner}/applications"
    first = client.get(url, params={"status": status.value, "limit": 5, "offset": 0})
    assert first.status_code == 200
    assert [item["app_id"] for item in first.json()["items"]] == list(reversed(ids))[:5]
    assert {item["status"] for item in first.json()["items"]} == {status.value}
    assert first.json()["has_next"] is True
    second = client.get(url, params={"status": status.value, "limit": 5, "offset": 5}).json()
    assert [item["app_id"] for item in second["items"]] == ids[:1]
    assert second["has_next"] is False
    for offset in (6, 100):
        response = client.get(url, params={"status": status.value, "offset": offset})
        assert response.status_code == 200
        assert response.json() == {"items": [], "has_next": False}


def test_no_status_returns_all_statuses_in_existing_order():
    owner = _user(106)
    now = datetime.now(timezone.utc)
    ids = [_application(owner, status.value, now + timedelta(seconds=index), status.value)
           for index, status in enumerate(ApplicationStatus)]
    response = client.get(f"/users/{owner}/applications")
    assert response.status_code == 200
    assert [item["app_id"] for item in response.json()["items"]] == list(reversed(ids))
    assert {item["status"] for item in response.json()["items"]} == {s.value for s in ApplicationStatus}
    assert response.json()["has_next"] is False


@pytest.mark.parametrize("status", ["all", "unknown", "", "SAVED", "saved,applied"])
def test_invalid_status_filter(status):
    assert client.get("/users/123/applications", params={"status": status}).status_code == 422


def test_empty_status_filter_returns_empty_page():
    owner = _user(107)
    _application(owner, "saved", datetime.now(timezone.utc))
    response = client.get(f"/users/{owner}/applications", params={"status": "offer"})
    assert response.status_code == 200
    assert response.json() == {"items": [], "has_next": False}


def test_status_update_moves_application_between_filtered_lists():
    owner = _user(108)
    app_id = _application(owner, "changing", datetime.now(timezone.utc))
    url = f"/users/{owner}/applications"
    assert client.get(url, params={"status": "saved"}).json()["items"][0]["app_id"] == app_id
    assert client.put(f"{url}/{app_id}/status", json={"status": "applied"}).status_code == 200
    assert client.get(url, params={"status": "saved"}).json() == {"items": [], "has_next": False}
    for params in ({"status": "applied"}, {}):
        assert [item["app_id"] for item in client.get(url, params=params).json()["items"]] == [app_id]


def test_search_matches_title_or_company_case_insensitively_as_a_substring():
    owner = _user(109)
    now = datetime.now(timezone.utc)
    title_id = _application(owner, "Senior Python Engineer", now)
    company_id = _application(owner, "Platform Engineer", now + timedelta(seconds=1), company="PyThOn Labs")
    _application(owner, "Designer", now + timedelta(seconds=2), company="Elsewhere")

    response = client.get(f"/users/{owner}/applications", params={"q": "ytho"})
    assert response.status_code == 200
    assert [item["app_id"] for item in response.json()["items"]] == [company_id, title_id]


def test_search_escapes_like_wildcards_and_is_user_scoped():
    owner, other = _user(110), _user(111)
    now = datetime.now(timezone.utc)
    percent = _application(owner, "100% Remote", now)
    underscore = _application(owner, "Data_Engineer", now + timedelta(seconds=1))
    slash = _application(owner, r"C\Developer", now + timedelta(seconds=2))
    _application(owner, "100x Remote", now + timedelta(seconds=3))
    _application(other, "100% Remote", now + timedelta(seconds=4))

    url = f"/users/{owner}/applications"
    assert [item["app_id"] for item in client.get(url, params={"q": "%"}).json()["items"]] == [percent]
    assert [item["app_id"] for item in client.get(url, params={"q": "_"}).json()["items"]] == [underscore]
    assert [item["app_id"] for item in client.get(url, params={"q": "\\"}).json()["items"]] == [slash]


def test_search_combines_status_before_pagination_and_preserves_order():
    owner = _user(112)
    now = datetime.now(timezone.utc)
    ids = [
        _application(owner, f"Python {index}", now + timedelta(seconds=index), "saved")
        for index in range(6)
    ]
    _application(owner, "Python ignored", now + timedelta(seconds=7), "applied")
    url = f"/users/{owner}/applications"
    first = client.get(url, params={"q": "PYTHON", "status": "saved", "limit": 5})
    assert [item["app_id"] for item in first.json()["items"]] == list(reversed(ids))[:5]
    assert first.json()["has_next"] is True
    second = client.get(url, params={"q": "PYTHON", "status": "saved", "limit": 5, "offset": 5})
    assert [item["app_id"] for item in second.json()["items"]] == [ids[0]]
    assert second.json()["has_next"] is False


def test_search_empty_values_normalize_to_no_filter_and_validates_input():
    owner = _user(113)
    _application(owner, "Backend", datetime.now(timezone.utc))
    url = f"/users/{owner}/applications"
    expected = client.get(url).json()
    for query in ("", " \t "):
        assert client.get(url, params={"q": query}).json() == expected
    assert client.get(url, params={"q": "x" * 100}).status_code == 200
    assert client.get(url, params={"q": "x" * 101}).status_code == 422
    assert client.get(url, params={"q": "x\x00y"}).status_code == 422


def test_search_zero_result_and_sql_like_input_are_literal():
    owner = _user(114)
    _application(owner, "Backend", datetime.now(timezone.utc))
    url = f"/users/{owner}/applications"
    assert client.get(url, params={"q": "missing"}).json() == {"items": [], "has_next": False}
    assert client.get(url, params={"q": "' OR 1=1 --"}).json() == {"items": [], "has_next": False}
