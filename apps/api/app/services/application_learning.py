from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, ApplicationStatusHistory

LATE_FUNNEL_STATUSES = {
    "recruiter_response", "interview", "offer", "hired", "withdrawn",
}


def build_application_learning_summary(session: Session, user_id: int) -> dict[str, object]:
    """Calculate a deterministic funnel from explicitly recorded status history."""
    applications = list(session.scalars(
        select(Application).where(Application.user_id == user_id)
    ))
    histories = list(session.execute(
        select(ApplicationStatusHistory.application_id, ApplicationStatusHistory.status,
               ApplicationStatusHistory.occurred_at, ApplicationStatusHistory.id)
        .join(Application, Application.id == ApplicationStatusHistory.application_id)
        .where(Application.user_id == user_id)
        .order_by(
            ApplicationStatusHistory.application_id,
            ApplicationStatusHistory.occurred_at,
            ApplicationStatusHistory.id,
        )
    ).tuples())

    events_by_application: dict[int, list[tuple[str, datetime, int]]] = {
        application.id: [] for application in applications
    }
    for application_id, status, occurred_at, event_id in histories:
        events_by_application[application_id].append((status, occurred_at, event_id))

    applied_count = recruiter_response_count = interview_count = offer_count = 0
    hired_count = withdrawn_count = 0
    history_missing_count = funnel_incomplete_count = 0
    for events in events_by_application.values():
        if not events:
            history_missing_count += 1
            continue

        first_applied_index = next(
            (index for index, (status, _, _) in enumerate(events) if status == "applied"), None
        )
        first_late_stage_index = next(
            (index for index, (status, _, _) in enumerate(events) if status in LATE_FUNNEL_STATUSES),
            None,
        )
        if first_late_stage_index is not None and (
            first_applied_index is None or first_applied_index > first_late_stage_index
        ):
            funnel_incomplete_count += 1
        if first_applied_index is None:
            continue

        applied_count += 1
        later_events = events[first_applied_index + 1:]
        if any(status == "recruiter_response" for status, _, _ in later_events):
            recruiter_response_count += 1
        if any(status == "interview" for status, _, _ in later_events):
            interview_count += 1
        if any(status == "offer" for status, _, _ in later_events):
            offer_count += 1
        if any(status == "hired" for status, _, _ in later_events):
            hired_count += 1
        if any(status == "withdrawn" for status, _, _ in later_events):
            withdrawn_count += 1

    return {
        "total_applications": len(applications),
        "applied_count": applied_count,
        "recruiter_response_count": recruiter_response_count,
        "interview_count": interview_count,
        "offer_count": offer_count,
        "hired_count": hired_count,
        "withdrawn_count": withdrawn_count,
        "applied_to_recruiter_response": _conversion(recruiter_response_count, applied_count),
        "applied_to_interview": _conversion(interview_count, applied_count),
        "applied_to_offer": _conversion(offer_count, applied_count),
        "applied_to_hired": _conversion(hired_count, applied_count),
        "history_missing_count": history_missing_count,
        "funnel_incomplete_count": funnel_incomplete_count,
        "as_of": datetime.now(timezone.utc),
    }


def _conversion(numerator: int, denominator: int) -> dict[str, int | float | None]:
    percentage = None
    if denominator:
        # One decimal place, rounded half up, is stable and visible in the API contract.
        percentage = float(
            (Decimal(numerator) * Decimal("100") / Decimal(denominator)).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        )
    return {"numerator": numerator, "denominator": denominator, "percentage": percentage}
