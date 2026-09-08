from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Application, ApplicationStatus, ApplicationStatusHistory, User
from app.services.applications import save_application_for_user, set_application_status
from conftest import StubEnrichmentService, TestSessionLocal, client
from test_match_api import create_application, create_user, put_profile


def _history(user_id: int, application_id: int) -> list[dict[str, str]]:
    response = client.get(
        f"/users/{user_id}/applications/{application_id}/status-history"
    )
    assert response.status_code == 200
    return response.json()["items"]


def _create_new_application(owner: int, suffix: str) -> tuple[int, int]:
    response = client.post(
        f"/users/{owner}/applications",
        json={"source_url": f"https://example.com/jobs/{suffix}"},
    )
    assert response.status_code == 200
    payload = response.json()
    return payload["application"]["id"], payload["job"]["id"]


def test_new_application_has_one_initial_saved_event_and_repeat_does_not_duplicate() -> None:
    owner = create_user(201)
    app_id, _ = _create_new_application(owner, "initial-history")

    items = _history(owner, app_id)
    assert [item["status"] for item in items] == ["saved"]
    assert datetime.fromisoformat(items[0]["occurred_at"]).utcoffset() is not None

    detail = client.get(f"/users/{owner}/applications/{app_id}").json()
    repeated = client.post(
        f"/users/{owner}/applications",
        json={"source_url": detail["job"]["source_url"]},
    )
    assert repeated.status_code == 200
    assert repeated.json()["application_created"] is False
    assert [item["status"] for item in _history(owner, app_id)] == ["saved"]


def test_application_and_initial_history_rollback_together(monkeypatch) -> None:
    with TestSessionLocal() as session:
        user = User(telegram_id=202, first_name="Анна")
        session.add(user)
        session.commit()
        rollback = Mock(wraps=session.rollback)

        flush = session.flush
        flush_calls = 0

        def fail_history_flush(*args, **kwargs) -> None:
            nonlocal flush_calls
            flush_calls += 1
            flush(*args, **kwargs)
            if flush_calls == 3:
                raise SQLAlchemyError("failure after application and history flush")

        monkeypatch.setattr(session, "flush", fail_history_flush)
        monkeypatch.setattr(session, "rollback", rollback)
        with pytest.raises(SQLAlchemyError):
            save_application_for_user(
                session,
                user.id,
                "https://example.com/jobs/atomic-history",
                StubEnrichmentService(),
            )
        rollback.assert_called_once()
        assert session.scalar(select(func.count()).select_from(Application)) == 0
        assert session.scalar(select(func.count()).select_from(ApplicationStatusHistory)) == 0


def test_legacy_application_has_empty_history() -> None:
    owner = create_user(203)
    _, job_id = create_application(owner)
    other = create_user(204)
    with TestSessionLocal() as session:
        legacy = Application(user_id=other, job_id=job_id, status="saved")
        session.add(legacy)
        session.commit()
        legacy_id = legacy.id

    assert _history(other, legacy_id) == []


def test_transitions_are_newest_first_and_same_status_retry_is_idempotent() -> None:
    owner = create_user(205)
    app_id, _ = _create_new_application(owner, "transitions")
    url = f"/users/{owner}/applications/{app_id}/status"

    assert client.put(url, json={"status": "applied"}).status_code == 200
    assert client.put(url, json={"status": "offer"}).status_code == 200
    assert client.put(url, json={"status": "offer"}).status_code == 200

    assert [item["status"] for item in _history(owner, app_id)] == [
        "offer", "applied", "saved"
    ]


def test_history_order_uses_id_desc_for_equal_timestamps() -> None:
    owner = create_user(206)
    _, job_id = create_application(owner)
    other = create_user(207)
    occurred_at = datetime(2026, 9, 7, 17, 14, tzinfo=timezone.utc)
    with TestSessionLocal() as session:
        application = Application(user_id=other, job_id=job_id, status="interview")
        session.add(application)
        session.flush()
        session.add_all([
            ApplicationStatusHistory(
                application_id=application.id, status="applied", occurred_at=occurred_at
            ),
            ApplicationStatusHistory(
                application_id=application.id, status="interview", occurred_at=occurred_at
            ),
        ])
        session.commit()
        application_id = application.id

    assert [item["status"] for item in _history(other, application_id)] == [
        "interview", "applied"
    ]


def test_history_foreign_and_missing_applications_are_identical() -> None:
    owner, other = create_user(208), create_user(209)
    app_id, _ = create_application(owner)

    foreign = client.get(
        f"/users/{other}/applications/{app_id}/status-history"
    )
    missing = client.get(
        f"/users/{owner}/applications/999999/status-history"
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {
        "detail": {"code": "APPLICATION_NOT_FOUND"}
    }


def test_status_and_history_rollback_together_on_commit_failure(monkeypatch) -> None:
    owner = create_user(210)
    app_id, _ = _create_new_application(owner, "rollback")
    with TestSessionLocal() as session:
        rollback = Mock(wraps=session.rollback)

        def fail_commit() -> None:
            session.flush()
            raise SQLAlchemyError("failure after flush")

        monkeypatch.setattr(session, "commit", fail_commit)
        monkeypatch.setattr(session, "rollback", rollback)
        with pytest.raises(SQLAlchemyError):
            set_application_status(session, owner, app_id, ApplicationStatus.OFFER)
        rollback.assert_called_once()
        assert session.get(Application, app_id).status == "saved"
        events = session.scalars(
            select(ApplicationStatusHistory)
            .where(ApplicationStatusHistory.application_id == app_id)
        ).all()
        assert [event.status for event in events] == ["saved"]


def test_status_history_does_not_change_job_note_or_match() -> None:
    owner = create_user(211)
    put_profile(owner)
    app_id, job_id = create_application(owner)
    note_url = f"/users/{owner}/applications/{app_id}/note"
    assert client.put(note_url, json={"note": "Follow up Friday"}).status_code == 200
    detail_before = client.get(f"/users/{owner}/applications/{app_id}").json()
    match_before = client.get(f"/users/{owner}/applications/{app_id}/match").json()

    assert client.put(
        f"/users/{owner}/applications/{app_id}/status", json={"status": "interview"}
    ).status_code == 200

    detail_after = client.get(f"/users/{owner}/applications/{app_id}").json()
    assert detail_after["application"]["note"] == "Follow up Friday"
    assert detail_after["job"] == detail_before["job"]
    assert detail_after["job"]["id"] == job_id
    assert client.get(f"/users/{owner}/applications/{app_id}/match").json() == match_before


def test_history_rows_cascade_when_application_is_deleted() -> None:
    owner = create_user(212)
    app_id, _ = _create_new_application(owner, "cascade")
    with TestSessionLocal() as session:
        application = session.get(Application, app_id)
        assert application is not None
        session.delete(application)
        session.commit()
        assert session.scalar(
            select(func.count())
            .select_from(ApplicationStatusHistory)
            .where(ApplicationStatusHistory.application_id == app_id)
        ) == 0
