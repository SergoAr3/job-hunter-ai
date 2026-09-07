from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models import Application
from app.services.applications import set_application_note
from conftest import TestSessionLocal, client
from test_match_api import create_user, create_application, put_profile


def test_note_lifecycle_scoping_and_unchanged_related_data():
    owner, other = create_user(701), create_user(702)
    put_profile(owner)
    app_id, job_id = create_application(owner)
    with TestSessionLocal() as session:
        second = Application(user_id=other, job_id=job_id, note="Other user's note")
        session.add(second)
        session.commit()
        second_id = second.id
    url = f"/users/{owner}/applications/{app_id}"
    before = client.get(url).json()
    match = client.get(url + "/match").json()
    assert before["application"]["note"] is None
    for note in ("HR написал\nСозвон в четверг", "< > _ * https://example.com"):
        response = client.put(url + "/note", json={"note": " \n" + note + "\t "})
        assert response.status_code == 200
        result = response.json()
        assert result["application"]["note"] == note
        assert result["application"]["status"] == before["application"]["status"]
        assert result["job"] == before["job"]
        assert client.get(url).json() == result
        assert client.put(url + "/note", json={"note": note}).json() == result
        assert client.get(url + "/match").json() == match
        assert "note" not in client.get(f"/users/{owner}/applications").json()["items"][0]
    deleted = client.delete(url + "/note")
    assert deleted.status_code == 200
    assert deleted.json()["application"]["note"] is None
    assert client.delete(url + "/note").json() == deleted.json()
    assert client.get(url).json() == deleted.json()
    assert client.get(url + "/match").json() == match
    assert deleted.json()["job"] == before["job"]
    assert deleted.json()["application"]["status"] == before["application"]["status"]
    assert client.get(f"/users/{other}/applications/{second_id}").json()["application"]["note"] == "Other user's note"


@pytest.mark.parametrize("method", ["put", "delete"])
def test_note_foreign_and_missing_are_identical(method):
    owner, other = create_user(703), create_user(704)
    app_id, _ = create_application(owner)
    kwargs = {"json": {"note": "text"}} if method == "put" else {}
    foreign = client.request(method, f"/users/{other}/applications/{app_id}/note", **kwargs)
    missing = client.request(method, f"/users/{owner}/applications/999999/note", **kwargs)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": {"code": "APPLICATION_NOT_FOUND"}}


@pytest.mark.parametrize("payload", [
    {}, {"note": None}, {"note": ""}, {"note": " \n\t "}, {"note": "x" * 1001},
    {"note": 123}, {"note": False}, {"note": []}, {"note": {}},
    {"note": "text", "status": "offer"}, {"note": "a\u0000b"},
])
def test_note_validation(payload):
    owner = create_user(705)
    app_id, _ = create_application(owner)
    assert client.put(f"/users/{owner}/applications/{app_id}/note", json=payload).status_code == 422


def test_note_limit_is_after_trim():
    owner = create_user(706)
    app_id, _ = create_application(owner)
    response = client.put(f"/users/{owner}/applications/{app_id}/note", json={"note": " " + "😀" * 1000 + " "})
    assert response.status_code == 200
    assert response.json()["application"]["note"] == "😀" * 1000


@pytest.mark.parametrize("note", ["replacement", None])
def test_note_db_error_rolls_back(monkeypatch, note):
    owner = create_user(707)
    app_id, _ = create_application(owner)
    with TestSessionLocal() as session:
        set_application_note(session, owner, app_id, "original")
        rollback = Mock(wraps=session.rollback)
        monkeypatch.setattr(session, "rollback", rollback)
        def fail():
            session.flush()
            raise SQLAlchemyError("test")
        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(SQLAlchemyError):
            set_application_note(session, owner, app_id, note)
        rollback.assert_called_once()
        assert session.get(Application, app_id).note == "original"
