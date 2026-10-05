from datetime import date
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models import Application
from app.schemas import ApplicationPatchIn
from app.services.applications import patch_application
from conftest import TestSessionLocal, client
from test_match_api import create_user, create_application


def application_url():
    owner = create_user(1801)
    app_id, _ = create_application(owner)
    return owner, app_id, f"/users/{owner}/applications/{app_id}"


def test_patch_lifecycle_partial_clear_serialization_and_status_history():
    owner, app_id, url = application_url()
    before = client.get(url).json()
    assert before["application"]["note"] is None
    assert before["application"]["next_action"] is None
    history = client.get(url + "/status-history").json()
    updated = client.patch(url, json={"note": "  Заметка\nстрока  ", "next_action": "  Написать HR  "})
    assert updated.status_code == 200
    value = updated.json()["application"]
    assert value["note"] == "Заметка\nстрока"
    assert value["next_action"] == "Написать HR"
    assert value["next_action_due_on"] is None
    assert client.get(url).json() == updated.json()
    assert client.get(f"/users/{owner}/applications").json()["items"][0]["next_action"] == "Написать HR"
    changed = client.patch(url, json={"note": "Edited"}).json()["application"]
    assert changed["next_action"] == "Написать HR"
    changed = client.patch(url, json={"next_action": "Next"}).json()["application"]
    assert changed["note"] == "Edited"
    assert client.patch(url, json={}).json()["application"] == changed
    assert client.patch(url, json={"note": None}).json()["application"]["next_action"] == "Next"
    cleared = client.patch(url, json={"next_action": " \n\t ", "note": "  "}).json()["application"]
    assert cleared["note"] is cleared["next_action"] is cleared["next_action_due_on"] is None
    assert client.get(url + "/status-history").json() == history
    assert client.get(url).json()["job"] == before["job"]
    status = client.put(url + "/status", json={"status": "applied"})
    assert status.status_code == 200
    assert status.json()["application"]["status"] == "applied"
    assert status.json()["application"]["note"] is None


@pytest.mark.parametrize("clear", [None, " \n "])
def test_preserves_dated_action_when_editing_and_clears_date(clear):
    _, _, url = application_url()
    client.put(url + "/next-action", json={"next_action": "Old", "next_action_due_on": "2026-10-10"})
    value = client.patch(url, json={"next_action": "New"}).json()["application"]
    assert value["next_action_due_on"] == "2026-10-10"
    value = client.patch(url, json={"note": "Note"}).json()["application"]
    assert value["next_action"] == "New"
    assert value["next_action_due_on"] == "2026-10-10"
    value = client.patch(url, json={"next_action": clear}).json()["application"]
    assert value["next_action_due_on"] is value["next_action"] is None
    assert value["note"] == "Note"


@pytest.mark.parametrize("field,limit", [("note", 1000), ("next_action", 500)])
def test_limits_after_trim_and_unicode(field, limit):
    _, _, url = application_url()
    accepted = client.patch(url, json={field: "  " + "😀" * limit + " \n"})
    assert accepted.status_code == 200
    assert accepted.json()["application"][field] == "😀" * limit
    assert client.patch(url, json={field: "x" * (limit + 1)}).status_code == 422
    assert client.get(url).json()["application"][field] == "😀" * limit


@pytest.mark.parametrize("field", ["note", "next_action"])
def test_oversized_patch_validation_is_bounded_and_does_not_echo_input(field):
    _, _, url = application_url()
    rejected = "PRIVATE_OVERSIZED_" + "x" * 10000
    response = client.patch(url, json={field: rejected})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "APPLICATION_INVALID"}}
    assert response.headers["cache-control"] == "no-store"
    assert len(response.content) < 200
    assert rejected not in response.text
    assert "PRIVATE_OVERSIZED_" not in response.text
    assert client.get(url).json()["application"][field] is None


def test_patch_safe_validation_does_not_change_legacy_note_contract():
    _, _, url = application_url()
    response = client.put(url + "/note", json={"note": "x" * 1001})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_too_long"


@pytest.mark.parametrize("payload", [
    {field: invalid} for field in ["note", "next_action"]
    for invalid in [12, False, [], {}, "a\0b"]
] + [{"notes": "Wrong name"}, {"user_id": 1}, {"status": "offer"}, {"next_action_due_on": "2026-10-10"}])
def test_invalid_patch_is_atomic(payload):
    _, _, url = application_url()
    client.patch(url, json={"note": "Keep"})
    before = client.get(url).json()
    assert client.patch(url, json=payload).status_code == 422
    assert client.get(url).json() == before


def test_foreign_and_missing_patch_and_read_denied():
    owner, app_id, url = application_url()
    other = create_user(1802)
    client.patch(url, json={"note": "Private"})
    for path in [f"/users/{other}/applications/{app_id}", f"/users/{owner}/applications/999999"]:
        assert client.patch(path, json={"note": "Overwrite"}).status_code == 404
        assert client.get(path).status_code == 404
    assert client.get(url).json()["application"]["note"] == "Private"


def test_noop_does_not_commit_and_failure_rolls_back_both_fields(monkeypatch):
    owner, app_id, url = application_url()
    client.put(url + "/next-action", json={"next_action": "Old", "next_action_due_on": "2026-10-10"})
    client.patch(url, json={"note": "Old note"})
    with TestSessionLocal() as session:
        commit = Mock(wraps=session.commit)
        monkeypatch.setattr(session, "commit", commit)
        patch_application(session, owner, app_id, ApplicationPatchIn(note="Old note"))
        commit.assert_not_called()
        def fail():
            session.flush()
            raise SQLAlchemyError("test")
        monkeypatch.setattr(session, "commit", fail)
        rollback = Mock(wraps=session.rollback)
        monkeypatch.setattr(session, "rollback", rollback)
        with pytest.raises(SQLAlchemyError):
            patch_application(session, owner, app_id, ApplicationPatchIn(note="New", next_action=None))
        rollback.assert_called_once()
        stored = session.get(Application, app_id)
        assert (stored.note, stored.next_action, stored.next_action_due_on) == ("Old note", "Old", date(2026, 10, 10))


def test_bearer_cannot_patch_another_user_path_or_application():
    from test_auth import register, login, bearer, plain
    assert register("patch-owner@example.com").status_code == 202
    token = login("patch-owner@example.com")["session_token"]
    from sqlalchemy import select
    from app.models import User
    with TestSessionLocal() as session:
        owner = session.scalar(select(User).where(User.email_canonical == "patch-owner@example.com")).id
    app_id, _ = create_application(owner)
    other = create_user(1803)
    other_app, _ = create_application(other)
    headers = bearer(token)
    assert plain.patch(f"/users/{owner}/applications/{app_id}", json={"note": "Private"}, headers=headers).status_code == 200
    assert plain.patch(f"/users/{other}/applications/{other_app}", json={"note": "Cross path"}, headers=headers).status_code == 404
    assert plain.patch(f"/users/{owner}/applications/{other_app}", json={"note": "Cross app"}, headers=headers).status_code == 404
    assert plain.patch(f"/users/{owner}/applications/{app_id}", json={"note": "Anonymous"}).status_code == 401


def test_create_save_response_defaults_and_duplicate_preserve_crm():
    owner = create_user(1804)
    url = f"/users/{owner}/applications"
    payload = {"source_url": "https://example.com/notes-defaults"}
    created = client.post(url, json=payload)
    assert created.status_code == 200
    value = created.json()["application"]
    assert value["note"] is value["next_action"] is None
    detail = f"{url}/{value['id']}"
    client.patch(detail, json={"note": "Keep", "next_action": "Call"})
    repeated = client.post(url, json=payload)
    assert repeated.status_code == 200
    assert repeated.json()["application"]["note"] == "Keep"
    assert repeated.json()["application"]["next_action"] == "Call"
    assert repeated.json()["application_created"] is False
