from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models import Application, ApplicationStatus
from app.services.applications import set_application_status
from conftest import TestSessionLocal, client
from test_match_api import create_user, create_application, put_profile


@pytest.mark.parametrize("status", list(ApplicationStatus))
def test_status_put_is_scoped_idempotent_and_preserves_match(status):
    owner, other = create_user(101), create_user(102)
    put_profile(owner)
    app_id, job_id = create_application(owner)
    with TestSessionLocal() as session:
        second = Application(user_id=other, job_id=job_id)
        session.add(second)
        session.commit()
        second_id = second.id
        assert second.status == "saved"
    url = f"/users/{owner}/applications/{app_id}"
    before = client.get(url).json()
    profile_before = client.get(f"/users/{owner}/profile").json()
    match = client.get(url + "/match").json()
    response = client.put(url + "/status", json={"status": status.value})
    assert response.status_code == 200
    result = response.json()
    assert result["application"]["status"] == status.value
    assert result["job"] == before["job"]
    assert client.get(f"/users/{owner}/profile").json() == profile_before
    assert client.put(url + "/status", json={"status": status.value}).json() == result
    assert client.get(url).json() == result
    assert client.get(f"/users/{owner}/applications").json()["items"][0]["status"] == status.value
    assert client.get(url + "/match").json() == match
    assert client.get(f"/users/{other}/applications/{second_id}").json()["application"]["status"] == "saved"
    repeated = client.post(f"/users/{owner}/applications", json={"source_url": before["job"]["source_url"]})
    assert repeated.json()["application"]["status"] == status.value


def test_all_status_transitions_are_allowed():
    owner = create_user(101)
    app_id, _ = create_application(owner)
    url = f"/users/{owner}/applications/{app_id}/status"
    for source in ApplicationStatus:
        for target in ApplicationStatus:
            assert client.put(url, json={"status": source.value}).status_code == 200
            response = client.put(url, json={"status": target.value})
            assert response.status_code == 200
            assert response.json()["application"]["status"] == target.value


@pytest.mark.parametrize("payload", [{}, {"status": None}, {"status": "unknown"}, {"status": "saved", "job_id": 7}])
def test_status_invalid_payload(payload):
    owner = create_user(101)
    app_id, _ = create_application(owner)
    assert client.put(f"/users/{owner}/applications/{app_id}/status", json=payload).status_code == 422


def test_status_foreign_and_missing_are_identical():
    owner, other = create_user(101), create_user(102)
    app_id, _ = create_application(owner)
    foreign = client.put(f"/users/{other}/applications/{app_id}/status", json={"status": "offer"})
    missing = client.put(f"/users/{owner}/applications/999999/status", json={"status": "offer"})
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": {"code": "APPLICATION_NOT_FOUND"}}


def test_status_db_failure_rolls_back(monkeypatch):
    owner = create_user(101)
    app_id, _ = create_application(owner)
    with TestSessionLocal() as session:
        rollback = Mock(wraps=session.rollback)
        monkeypatch.setattr(session, "rollback", rollback)
        def fail():
            session.flush()
            raise SQLAlchemyError("test failure after flush")
        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(SQLAlchemyError):
            set_application_status(session, owner, app_id, ApplicationStatus.OFFER)
        rollback.assert_called_once()
        assert session.get(Application, app_id).status == "saved"
