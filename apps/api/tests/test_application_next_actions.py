from datetime import date
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models import Application
from app.services.applications import set_application_next_action
from conftest import TestSessionLocal, client
from test_match_api import create_user, create_application, put_profile


def test_lifecycle_and_unchanged_related_data():
    owner = create_user(901)
    put_profile(owner)
    app_id, _ = create_application(owner)
    url = f"/users/{owner}/applications/{app_id}"
    client.put(url + "/note", json={"note": "Keep note"})
    before = client.get(url).json()
    history = client.get(url + "/status-history").json()
    match = client.get(url + "/match").json()
    assert before["application"]["next_action"] is None
    for action, due_on in [("Написать HR", "2026-09-12"), ("😀" * 500, "2000-02-29")]:
        payload = {"next_action": " \n" + action + "\t ", "next_action_due_on": due_on}
        response = client.put(url + "/next-action", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["application"]["next_action"] == action
        assert result["application"]["next_action_due_on"] == due_on
        assert client.get(url).json() == result
        assert client.put(url + "/next-action", json=payload).json() == result
    deleted = client.delete(url + "/next-action")
    assert deleted.status_code == 200
    result = deleted.json()
    assert result["application"]["next_action"] is None
    assert result["application"]["next_action_due_on"] is None
    assert client.delete(url + "/next-action").json() == result
    assert result["job"] == before["job"]
    assert result["application"]["status"] == before["application"]["status"]
    assert result["application"]["note"] == "Keep note"
    assert client.get(url + "/status-history").json() == history
    assert client.get(url + "/match").json() == match
    assert "next_action" not in client.get(f"/users/{owner}/applications").json()["items"][0]


@pytest.mark.parametrize("changes", [
    {"next_action": value} for value in [None, "", " \n\t", 123, False, [], {}, "a\u0000b", "x" * 501]
] + [
    {"next_action_due_on": value} for value in [None, 123, False, "", "2026-02-29", "2026-13-01",
        "2026-09-12T00:00:00", "2026-09-12T00:00:00Z", "2026-09-12+04:00", "12.09.2026", "20260912", " 2026-09-12 "]
] + [{"extra": 1}])
def test_validation(changes):
    owner = create_user(902)
    app_id, _ = create_application(owner)
    payload = {"next_action": "HR", "next_action_due_on": "2026-09-12", **changes}
    assert client.put(f"/users/{owner}/applications/{app_id}/next-action", json=payload).status_code == 422


@pytest.mark.parametrize("payload", [{}, {"next_action": "HR"}, {"next_action_due_on": "2026-09-12"}])
def test_required_fields(payload):
    owner = create_user(903)
    app_id, _ = create_application(owner)
    assert client.put(f"/users/{owner}/applications/{app_id}/next-action", json=payload).status_code == 422


@pytest.mark.parametrize("method", ["put", "delete"])
def test_foreign_and_missing(method):
    owner, other = create_user(904), create_user(905)
    app_id, _ = create_application(owner)
    kwargs = {"json": {"next_action": "HR", "next_action_due_on": "2026-09-12"}} if method == "put" else {}
    for user_id, target in [(other, app_id), (owner, 999999)]:
        response = client.request(method, f"/users/{user_id}/applications/{target}/next-action", **kwargs)
        assert response.status_code == 404
        assert response.json() == {"detail": {"code": "APPLICATION_NOT_FOUND"}}


@pytest.mark.parametrize("action,due_on", [("New", date(2026, 9, 12)), (None, None)])
def test_rollback_and_noop(monkeypatch, action, due_on):
    owner = create_user(906)
    app_id, _ = create_application(owner)
    with TestSessionLocal() as session:
        set_application_next_action(session, owner, app_id, "Old", date(2020, 1, 1))
        commit = Mock(wraps=session.commit)
        monkeypatch.setattr(session, "commit", commit)
        set_application_next_action(session, owner, app_id, "Old", date(2020, 1, 1))
        commit.assert_not_called()
        rollback = Mock(wraps=session.rollback)
        monkeypatch.setattr(session, "rollback", rollback)
        def fail():
            session.flush()
            raise SQLAlchemyError("test")
        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(SQLAlchemyError):
            set_application_next_action(session, owner, app_id, action, due_on)
        rollback.assert_called_once()
        application = session.get(Application, app_id)
        assert (application.next_action, application.next_action_due_on) == ("Old", date(2020, 1, 1))


@pytest.mark.parametrize("action,due_on", [("Partial", None), (None, date(2026, 9, 12))])
def test_database_pair_constraint(action, due_on):
    owner = create_user(907)
    app_id, _ = create_application(owner)
    with TestSessionLocal() as session:
        application = session.get(Application, app_id)
        application.next_action, application.next_action_due_on = action, due_on
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
