from datetime import datetime, timezone

import pytest

from app.models import Application, ApplicationStatusHistory, Job
from conftest import TestSessionLocal, client


NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)


def _user(telegram_id: int) -> int:
    return client.post(
        "/users/telegram", json={"telegram_id": telegram_id, "first_name": "Anna"}
    ).json()["id"]


def _application(
    user_id: int,
    suffix: str,
    history: list[tuple[str, datetime]] | None = None,
    *,
    status: str = "saved",
) -> int:
    with TestSessionLocal() as session:
        job = Job(source="company_site", source_url=f"https://example.com/learning/{user_id}/{suffix}")
        session.add(job)
        session.flush()
        application = Application(user_id=user_id, job_id=job.id, status=status)
        session.add(application)
        session.flush()
        for event_status, occurred_at in history or []:
            session.add(ApplicationStatusHistory(
                application_id=application.id, status=event_status, occurred_at=occurred_at,
            ))
            session.flush()
        session.commit()
        return application.id


def _summary(user_id: int) -> dict[str, object]:
    response = client.get(f"/users/{user_id}/applications/learning-summary")
    assert response.status_code == 200
    return response.json()


def test_empty_user_summary_has_null_percentages_and_literal_route() -> None:
    payload = _summary(_user(700))

    assert set(payload) == {
        "total_applications", "applied_count", "interview_count", "offer_count",
        "applied_to_interview", "applied_to_offer", "history_missing_count",
        "funnel_incomplete_count", "as_of",
    }
    assert payload["total_applications"] == 0
    assert payload["applied_to_interview"] == {"numerator": 0, "denominator": 0, "percentage": None}
    assert payload["applied_to_offer"] == {"numerator": 0, "denominator": 0, "percentage": None}
    assert datetime.fromisoformat(payload["as_of"]).utcoffset() is not None


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ([('saved', NOW)], (0, 0, 0, 0, 0)),
        ([('applied', NOW)], (1, 0, 0, 0, 0)),
        ([('applied', NOW), ('interview', NOW)], (1, 1, 0, 0, 0)),
        ([('applied', NOW), ('offer', NOW)], (1, 0, 1, 0, 0)),
        ([('applied', NOW), ('interview', NOW), ('offer', NOW)], (1, 1, 1, 0, 0)),
        ([('applied', NOW), ('interview', NOW), ('saved', NOW), ('interview', NOW)], (1, 1, 0, 0, 0)),
        ([('interview', NOW), ('applied', NOW), ('offer', NOW)], (1, 0, 1, 0, 1)),
        ([('interview', NOW)], (0, 0, 0, 0, 1)),
        ([('offer', NOW)], (0, 0, 0, 0, 1)),
    ],
)
def test_summary_uses_only_ordered_history_events(
    history: list[tuple[str, datetime]], expected: tuple[int, int, int, int, int],
) -> None:
    user_id = _user(701)
    _application(user_id, "events", history)

    payload = _summary(user_id)

    assert (
        payload["applied_count"], payload["interview_count"], payload["offer_count"],
        payload["history_missing_count"], payload["funnel_incomplete_count"],
    ) == expected
    assert payload["total_applications"] == 1
    assert payload["applied_to_interview"]["denominator"] == expected[0]
    assert payload["applied_to_offer"]["numerator"] == expected[2]
    if not expected[0]:
        assert payload["applied_to_interview"]["percentage"] is None


def test_same_timestamp_uses_history_id_and_late_applied_does_not_legalize_earlier_interview() -> None:
    user_id = _user(760)
    _application(user_id, "same-time", [("interview", NOW), ("applied", NOW), ("offer", NOW)])

    payload = _summary(user_id)

    assert payload["applied_count"] == 1
    assert payload["interview_count"] == 0
    assert payload["offer_count"] == 1
    assert payload["funnel_incomplete_count"] == 1


def test_history_missing_and_current_status_do_not_create_events() -> None:
    user_id = _user(761)
    _application(user_id, "legacy-applied", status="applied")
    _application(user_id, "legacy-interview", status="interview")

    payload = _summary(user_id)

    assert payload["total_applications"] == 2
    assert payload["applied_count"] == payload["interview_count"] == payload["offer_count"] == 0
    assert payload["history_missing_count"] == 2
    assert payload["funnel_incomplete_count"] == 0


def test_users_are_scoped_and_percentage_rounds_half_up_to_one_decimal() -> None:
    owner, other = _user(770), _user(771)
    for suffix in range(6):
        history = [("applied", NOW), ("interview", NOW)] if suffix == 0 else [("applied", NOW)]
        _application(owner, f"owner-{suffix}", history)
    _application(other, "other", [("applied", NOW), ("interview", NOW), ("offer", NOW)])

    payload = _summary(owner)

    assert payload["total_applications"] == payload["applied_count"] == 6
    assert payload["interview_count"] == 1
    assert payload["offer_count"] == 0
    assert payload["applied_to_interview"] == {"numerator": 1, "denominator": 6, "percentage": 16.7}
