from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import Application, ApplicationMatchSnapshot, ApplicationStatusHistory


OUTCOME_STATUSES = (
    "recruiter_response",
    "interview",
    "offer",
    "hired",
    "withdrawn",
)
SCORE_BUCKETS = (
    ("high", 75, 100),
    ("medium", 50, 74),
    ("low", 0, 49),
)
MIN_PERCENTAGE_DENOMINATOR = 5


def build_application_match_learning_summary(
    session: Session, user_id: int,
) -> dict[str, object]:
    """Aggregate historical snapshot outcomes without using current match or status."""
    application_ids = list(session.scalars(
        select(Application.id).where(Application.user_id == user_id)
    ))
    if not application_ids:
        return _empty_summary()
    histories = list(session.execute(
        select(
            ApplicationStatusHistory.application_id,
            ApplicationStatusHistory.status,
            ApplicationStatusHistory.occurred_at,
            ApplicationStatusHistory.id,
        )
        .where(ApplicationStatusHistory.application_id.in_(application_ids))
        .order_by(
            ApplicationStatusHistory.application_id,
            ApplicationStatusHistory.occurred_at,
            ApplicationStatusHistory.id,
        )
    ).tuples())

    events_by_application: dict[int, list[tuple[str, datetime, int]]] = {
        application_id: [] for application_id in application_ids
    }
    for application_id, status, occurred_at, event_id in histories:
        events_by_application[application_id].append((status, occurred_at, event_id))

    trigger = aliased(ApplicationStatusHistory)
    snapshot_rows = list(session.execute(
        select(ApplicationMatchSnapshot, trigger)
        .outerjoin(trigger, trigger.id == ApplicationMatchSnapshot.trigger_status_history_id)
        .where(ApplicationMatchSnapshot.application_id.in_(application_ids))
    ).tuples())
    snapshots_by_application = {
        snapshot.application_id: (snapshot, trigger_event)
        for snapshot, trigger_event in snapshot_rows
    }

    applied_application_ids = {
        application_id
        for application_id, events in events_by_application.items()
        if any(status == "applied" for status, _, _ in events)
    }
    coverage = {
        "applied_application_count": len(applied_application_ids),
        "captured_count": 0,
        "unavailable_count": 0,
        "legacy_without_snapshot_count": 0,
        "invalid_anchor_count": 0,
    }
    versions: dict[str, dict[str, object]] = {}

    for application_id in applied_application_ids:
        snapshot_row = snapshots_by_application.get(application_id)
        if snapshot_row is None:
            coverage["legacy_without_snapshot_count"] += 1
            continue

        snapshot, trigger_event = snapshot_row
        if not _valid_anchor(snapshot, trigger_event):
            coverage["invalid_anchor_count"] += 1
            continue
        if snapshot.capture_status == "unavailable":
            coverage["unavailable_count"] += 1
            continue
        if snapshot.capture_status != "captured":
            # The database constraint prevents this for normal data. Keep the
            # coverage partition exhaustive if inconsistent data is encountered.
            coverage["invalid_anchor_count"] += 1
            continue

        coverage["captured_count"] += 1
        assert trigger_event is not None
        outcomes = _subsequent_outcomes(
            events_by_application[application_id], trigger_event
        )
        version = _version_group(versions, snapshot)
        version["captured_count"] += 1
        if snapshot.verdict == "insufficient_data":
            version["insufficient_data_count"] += 1
        if snapshot.score is None:
            continue

        version["scored_count"] += 1
        buckets = version["buckets"]
        assert isinstance(buckets, dict)
        bucket = buckets[_score_bucket_name(int(snapshot.score))]
        bucket["application_count"] += 1
        outcome_counts = bucket["outcome_counts"]
        assert isinstance(outcome_counts, dict)
        for status in outcomes:
            outcome_counts[status] += 1

    algorithm_versions = sorted(
        versions.values(), key=lambda item: str(item["algorithm_version"])
    )
    algorithm_versions.sort(
        key=lambda item: item["latest_recorded_at"], reverse=True
    )

    return {
        "as_of": datetime.now(timezone.utc),
        "snapshot_coverage": coverage,
        "algorithm_versions": [
            _serialize_version(version) for version in algorithm_versions
        ],
    }


def _empty_summary() -> dict[str, object]:
    return {
        "as_of": datetime.now(timezone.utc),
        "snapshot_coverage": {
            "applied_application_count": 0,
            "captured_count": 0,
            "unavailable_count": 0,
            "legacy_without_snapshot_count": 0,
            "invalid_anchor_count": 0,
        },
        "algorithm_versions": [],
    }


def _valid_anchor(
    snapshot: ApplicationMatchSnapshot,
    trigger_event: ApplicationStatusHistory | None,
) -> bool:
    return (
        trigger_event is not None
        and trigger_event.application_id == snapshot.application_id
        and trigger_event.status == "applied"
    )


def _subsequent_outcomes(
    events: list[tuple[str, datetime, int]],
    trigger_event: ApplicationStatusHistory,
) -> set[str]:
    trigger_key = (trigger_event.occurred_at, trigger_event.id)
    return {
        status
        for status, occurred_at, event_id in events
        if status in OUTCOME_STATUSES and (occurred_at, event_id) > trigger_key
    }


def _version_group(
    versions: dict[str, dict[str, object]], snapshot: ApplicationMatchSnapshot,
) -> dict[str, object]:
    algorithm_version = str(snapshot.algorithm_version)
    version = versions.get(algorithm_version)
    if version is None:
        version = {
            "algorithm_version": algorithm_version,
            "captured_count": 0,
            "scored_count": 0,
            "insufficient_data_count": 0,
            "latest_recorded_at": snapshot.recorded_at,
            "buckets": {
                name: {
                    "bucket": name,
                    "score_min": score_min,
                    "score_max": score_max,
                    "application_count": 0,
                    "outcome_counts": {status: 0 for status in OUTCOME_STATUSES},
                }
                for name, score_min, score_max in SCORE_BUCKETS
            },
        }
        versions[algorithm_version] = version
    elif snapshot.recorded_at > version["latest_recorded_at"]:
        version["latest_recorded_at"] = snapshot.recorded_at
    return version


def _score_bucket_name(score: int) -> str:
    for name, score_min, score_max in SCORE_BUCKETS:
        if score_min <= score <= score_max:
            return name
    raise ValueError(f"Snapshot score is outside the supported range: {score}")


def _serialize_version(version: dict[str, object]) -> dict[str, object]:
    buckets = version["buckets"]
    assert isinstance(buckets, dict)
    serialized_buckets: list[dict[str, object]] = []
    for name, _, _ in SCORE_BUCKETS:
        bucket = buckets[name]
        outcome_counts = bucket["outcome_counts"]
        denominator = int(bucket["application_count"])
        serialized_buckets.append({
            "bucket": bucket["bucket"],
            "score_min": bucket["score_min"],
            "score_max": bucket["score_max"],
            "application_count": denominator,
            "outcomes": {
                status: _conversion(int(outcome_counts[status]), denominator)
                for status in OUTCOME_STATUSES
            },
        })
    return {
        "algorithm_version": version["algorithm_version"],
        "captured_count": version["captured_count"],
        "scored_count": version["scored_count"],
        "insufficient_data_count": version["insufficient_data_count"],
        "score_buckets": serialized_buckets,
    }


def _conversion(numerator: int, denominator: int) -> dict[str, int | float | None]:
    percentage = None
    if denominator >= MIN_PERCENTAGE_DENOMINATOR:
        percentage = float(
            (Decimal(numerator) * Decimal("100") / Decimal(denominator)).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        )
    return {"numerator": numerator, "denominator": denominator, "percentage": percentage}
