from datetime import date, datetime, timedelta, timezone

import pytest

from app.models import Application, Job
from conftest import TestSessionLocal, client
from test_match_api import create_user, create_application


TODAY = date(2026, 9, 28)


def _application(
    user_id: int,
    suffix: str,
    *,
    due_on: date | None,
    status: str = "applied",
    created_at: datetime | None = None,
) -> int:
    with TestSessionLocal() as session:
        job = Job(
            source="company_site",
            source_url=f"https://example.com/follow-ups/{user_id}/{suffix}",
            title=f"Title {suffix}",
            company=f"Company {suffix}",
            workplace_type="remote",
        )
        session.add(job)
        session.flush()
        application = Application(
            user_id=user_id,
            job_id=job.id,
            status=status,
            next_action=f"Action {suffix}" if due_on else None,
            next_action_due_on=due_on,
            created_at=created_at or datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        session.add(application)
        session.commit()
        return application.id


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.applications.current_utc_date", lambda: TODAY)


def _queue(user_id: int, **params: int) -> dict[str, object]:
    response = client.get(f"/users/{user_id}/applications/follow-ups", params=params)
    assert response.status_code == 200
    return response.json()


def test_empty_user_and_applications_without_actions_return_empty_queue() -> None:
    user_id = create_user(940)
    _application(user_id, "no-action", due_on=None)

    assert _queue(user_id) == {"items": [], "has_next": False}
    assert _queue(999999) == {"items": [], "has_next": False}


def test_queue_groups_by_utc_date_and_returns_exact_dates() -> None:
    user_id = create_user(941)
    overdue = _application(user_id, "overdue", due_on=TODAY - timedelta(days=1))
    today = _application(user_id, "today", due_on=TODAY)
    upcoming = _application(user_id, "upcoming", due_on=TODAY + timedelta(days=1))

    items = _queue(user_id)["items"]

    assert [(item["application_id"], item["due_state"], item["next_action_due_on"]) for item in items] == [
        (overdue, "overdue", "2026-09-27"),
        (today, "today", "2026-09-28"),
        (upcoming, "upcoming", "2026-09-29"),
    ]


def test_queue_order_is_deterministic_and_paginates_without_losing_actions() -> None:
    user_id = create_user(942)
    older = _application(
        user_id, "older", due_on=TODAY - timedelta(days=2),
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    newer_low_id = _application(
        user_id, "newer-low", due_on=TODAY - timedelta(days=2),
        created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    newer_high_id = _application(
        user_id, "newer-high", due_on=TODAY - timedelta(days=2),
        created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    later = _application(user_id, "later", due_on=TODAY + timedelta(days=1))

    first = _queue(user_id, limit=2, offset=0)
    second = _queue(user_id, limit=2, offset=2)

    assert [item["application_id"] for item in first["items"]] == [newer_high_id, newer_low_id]
    assert first["has_next"] is True
    assert [item["application_id"] for item in second["items"]] == [older, later]
    assert second["has_next"] is False


@pytest.mark.parametrize("status", ["rejected", "hired", "withdrawn"])
def test_terminal_status_actions_remain_in_queue(status: str) -> None:
    user_id = create_user(943)
    app_id = _application(user_id, status, due_on=TODAY, status=status)

    item = _queue(user_id)["items"][0]

    assert item["application_id"] == app_id
    assert item["status"] == status


def test_status_change_does_not_remove_action_and_foreign_actions_are_scoped() -> None:
    owner, other = create_user(944), create_user(945)
    owner_app, _ = create_application(owner)
    client.put(
        f"/users/{owner}/applications/{owner_app}/next-action",
        json={"next_action": "Review offer", "next_action_due_on": TODAY.isoformat()},
    )
    client.put(f"/users/{owner}/applications/{owner_app}/status", json={"status": "hired"})
    _application(other, "foreign", due_on=TODAY)

    items = _queue(owner)["items"]

    assert [(item["application_id"], item["status"], item["next_action"]) for item in items] == [
        (owner_app, "hired", "Review offer")
    ]


def test_follow_up_route_is_literal_and_validates_existing_pagination_bounds() -> None:
    user_id = create_user(946)

    assert client.get(f"/users/{user_id}/applications/follow-ups").status_code == 200
    assert client.get(f"/users/{user_id}/applications/follow-ups?limit=0").status_code == 422
    assert client.get(f"/users/{user_id}/applications/follow-ups?offset=-1").status_code == 422
