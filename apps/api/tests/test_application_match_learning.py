from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    Application,
    ApplicationMatchSnapshot,
    ApplicationStatusHistory,
    Job,
)
from app.services.application_match_learning import build_application_match_learning_summary
from conftest import TestSessionLocal, client


NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
OUTCOMES = ("recruiter_response", "interview", "offer", "hired", "withdrawn")


def _user(telegram_id: int) -> int:
    response = client.post(
        "/users/telegram", json={"telegram_id": telegram_id, "first_name": "Snapshot"}
    )
    assert response.status_code == 200
    return response.json()["id"]


def _application(user_id: int, suffix: str, *, status: str = "saved") -> int:
    with TestSessionLocal() as session:
        job = Job(
            source="company_site",
            source_url=f"https://example.com/match-learning/{user_id}/{suffix}",
        )
        session.add(job)
        session.flush()
        application = Application(user_id=user_id, job_id=job.id, status=status)
        session.add(application)
        session.commit()
        return application.id


def _event(application_id: int, status: str, occurred_at: datetime = NOW) -> int:
    with TestSessionLocal() as session:
        event = ApplicationStatusHistory(
            application_id=application_id, status=status, occurred_at=occurred_at
        )
        session.add(event)
        session.commit()
        return event.id


def _captured_snapshot(
    application_id: int,
    trigger_id: int,
    *,
    score: int | None = 75,
    algorithm_version: str = "job-match-v2.1",
    recorded_at: datetime = NOW,
) -> None:
    insufficient = score is None
    with TestSessionLocal() as session:
        session.add(ApplicationMatchSnapshot(
            application_id=application_id,
            trigger_status_history_id=trigger_id,
            snapshot_schema_version=1,
            capture_status="captured",
            unavailable_reason=None,
            recorded_at=recorded_at,
            algorithm_version=algorithm_version,
            score=score,
            verdict=(
                "insufficient_data" if insufficient else
                "high" if score >= 75 else "medium" if score >= 50 else "low"
            ),
            coverage=20 if insufficient else 80,
            confidence=None if insufficient else "high",
            profile_updated_at=NOW,
            job_updated_at=NOW,
            job_parsing_status="success",
            job_ai_enrichment_status="success",
            inputs={"private": "historical salary"},
            result_detail={"private": "raw matcher detail"},
        ))
        session.commit()


def _unavailable_snapshot(application_id: int, trigger_id: int) -> None:
    with TestSessionLocal() as session:
        session.add(ApplicationMatchSnapshot(
            application_id=application_id,
            trigger_status_history_id=trigger_id,
            snapshot_schema_version=1,
            capture_status="unavailable",
            unavailable_reason="profile_missing",
        ))
        session.commit()


def _summary(user_id: int) -> dict[str, object]:
    response = client.get(f"/users/{user_id}/applications/match-learning-summary")
    assert response.status_code == 200
    return response.json()


def _bucket(payload: dict[str, object], name: str, version_index: int = 0) -> dict[str, object]:
    versions = payload["algorithm_versions"]
    return next(item for item in versions[version_index]["score_buckets"] if item["bucket"] == name)


@pytest.mark.parametrize(
    ("score", "expected_bucket"),
    [(0, "low"), (49, "low"), (50, "medium"), (74, "medium"),
     (75, "high"), (100, "high")],
)
def test_score_boundaries_use_deterministic_buckets(score: int, expected_bucket: str) -> None:
    user_id = _user(1000 + score)
    application_id = _application(user_id, str(score))
    trigger_id = _event(application_id, "applied")
    _captured_snapshot(application_id, trigger_id, score=score)

    payload = _summary(user_id)

    version = payload["algorithm_versions"][0]
    assert [item["bucket"] for item in version["score_buckets"]] == ["high", "medium", "low"]
    assert version["scored_count"] == 1
    assert _bucket(payload, expected_bucket)["application_count"] == 1
    assert sum(item["application_count"] for item in version["score_buckets"]) == 1


def test_coverage_categories_are_exclusive_and_insufficient_is_captured() -> None:
    user_id = _user(1100)
    captured = _application(user_id, "captured")
    insufficient = _application(user_id, "insufficient")
    unavailable = _application(user_id, "unavailable")
    legacy = _application(user_id, "legacy")
    invalid = _application(user_id, "invalid")

    captured_trigger = _event(captured, "applied")
    insufficient_trigger = _event(insufficient, "applied")
    unavailable_trigger = _event(unavailable, "applied")
    _event(legacy, "applied")
    _event(invalid, "applied")
    invalid_trigger = _event(invalid, "saved")

    _captured_snapshot(captured, captured_trigger, score=80)
    _captured_snapshot(insufficient, insufficient_trigger, score=None)
    _unavailable_snapshot(unavailable, unavailable_trigger)
    _captured_snapshot(invalid, invalid_trigger, score=90)

    payload = _summary(user_id)
    coverage = payload["snapshot_coverage"]

    assert coverage == {
        "applied_application_count": 5,
        "captured_count": 2,
        "unavailable_count": 1,
        "legacy_without_snapshot_count": 1,
        "invalid_anchor_count": 1,
    }
    assert coverage["applied_application_count"] == sum(
        value for key, value in coverage.items() if key != "applied_application_count"
    )
    version = payload["algorithm_versions"][0]
    assert (version["captured_count"], version["scored_count"], version["insufficient_data_count"]) == (2, 1, 1)


def test_trigger_from_another_application_is_invalid_and_not_reanchored() -> None:
    user_id = _user(1101)
    application_id = _application(user_id, "owner")
    other_id = _application(user_id, "other")
    _event(application_id, "applied")
    other_trigger = _event(other_id, "applied")
    _captured_snapshot(application_id, other_trigger, score=100)

    payload = _summary(user_id)

    assert payload["snapshot_coverage"] == {
        "applied_application_count": 2,
        "captured_count": 0,
        "unavailable_count": 0,
        "legacy_without_snapshot_count": 1,
        "invalid_anchor_count": 1,
    }
    assert payload["algorithm_versions"] == []


def test_total_order_outcomes_are_explicit_binary_and_current_status_is_ignored() -> None:
    user_id = _user(1102)
    application_id = _application(user_id, "ordered", status="hired")
    _event(application_id, "interview", NOW - timedelta(seconds=1))
    _event(application_id, "recruiter_response", NOW)  # lower id at equal timestamp
    trigger_id = _event(application_id, "applied", NOW)
    _event(application_id, "interview", NOW)  # higher id at equal timestamp
    _event(application_id, "interview", NOW)
    _event(application_id, "offer", NOW + timedelta(seconds=1))
    _event(application_id, "withdrawn", NOW + timedelta(seconds=2))
    _event(application_id, "saved", NOW + timedelta(seconds=3))
    _event(application_id, "rejected", NOW + timedelta(seconds=4))
    _captured_snapshot(application_id, trigger_id, score=90)

    bucket = _bucket(_summary(user_id), "high")
    outcomes = bucket["outcomes"]

    assert bucket["application_count"] == 1
    assert outcomes["recruiter_response"]["numerator"] == 0
    assert outcomes["interview"]["numerator"] == 1
    assert outcomes["offer"]["numerator"] == 1
    assert outcomes["hired"]["numerator"] == 0
    assert outcomes["withdrawn"]["numerator"] == 1
    assert all(item["denominator"] == 1 and item["percentage"] is None for item in outcomes.values())


def test_offer_and_hired_do_not_infer_other_stages_and_no_events_stays_in_denominator() -> None:
    user_id = _user(1103)
    offer_app = _application(user_id, "offer-only")
    hired_app = _application(user_id, "hired-only")
    no_outcome_app = _application(user_id, "no-outcome")
    for application_id, outcome in ((offer_app, "offer"), (hired_app, "hired")):
        trigger_id = _event(application_id, "applied")
        _event(application_id, outcome, NOW + timedelta(seconds=1))
        _captured_snapshot(application_id, trigger_id, score=60)
    trigger_id = _event(no_outcome_app, "applied")
    _captured_snapshot(no_outcome_app, trigger_id, score=60)

    outcomes = _bucket(_summary(user_id), "medium")["outcomes"]

    assert outcomes["recruiter_response"]["numerator"] == 0
    assert outcomes["interview"]["numerator"] == 0
    assert outcomes["offer"]["numerator"] == 1
    assert outcomes["hired"]["numerator"] == 1
    assert all(item["denominator"] == 3 for item in outcomes.values())


def test_each_explicit_outcome_is_counted_once_after_trigger() -> None:
    user_id = _user(1104)
    application_id = _application(user_id, "all-outcomes")
    trigger_id = _event(application_id, "applied")
    for index, status in enumerate(OUTCOMES, start=1):
        _event(application_id, status, NOW + timedelta(seconds=index))
        _event(application_id, status, NOW + timedelta(seconds=index + 10))
    _captured_snapshot(application_id, trigger_id, score=10)

    outcomes = _bucket(_summary(user_id), "low")["outcomes"]

    assert all(conversion["numerator"] == 1 for conversion in outcomes.values())
    assert all(conversion["denominator"] == 1 for conversion in outcomes.values())


@pytest.mark.parametrize(
    ("denominator", "interviews", "percentage"),
    [(1, 1, None), (4, 2, None), (5, 1, 20.0), (6, 1, 16.7)],
)
def test_percentage_guardrail_and_rounding(
    denominator: int, interviews: int, percentage: float | None,
) -> None:
    user_id = _user(1200 + denominator)
    for index in range(denominator):
        application_id = _application(user_id, f"percentage-{index}")
        trigger_id = _event(application_id, "applied")
        if index < interviews:
            _event(application_id, "interview", NOW + timedelta(seconds=1))
        _captured_snapshot(application_id, trigger_id, score=40)

    conversion = _bucket(_summary(user_id), "low")["outcomes"]["interview"]

    assert conversion == {
        "numerator": interviews,
        "denominator": denominator,
        "percentage": percentage,
    }
    assert _bucket(_summary(user_id), "high")["outcomes"]["interview"] == {
        "numerator": 0, "denominator": 0, "percentage": None,
    }


def test_versions_are_isolated_and_ordered_by_latest_snapshot_then_name() -> None:
    user_id = _user(1300)
    observations = (
        ("v2-old", "job-match-v2.2", NOW, "interview"),
        ("v1-new", "job-match-v2.1", NOW + timedelta(days=1), "offer"),
        ("v3-tie", "job-match-v3.0", NOW + timedelta(days=1), "hired"),
    )
    for suffix, version, recorded_at, outcome in observations:
        application_id = _application(user_id, suffix)
        trigger_id = _event(application_id, "applied")
        _event(application_id, outcome, NOW + timedelta(days=2))
        _captured_snapshot(
            application_id, trigger_id, score=80,
            algorithm_version=version, recorded_at=recorded_at,
        )

    payload = _summary(user_id)

    assert [item["algorithm_version"] for item in payload["algorithm_versions"]] == [
        "job-match-v2.1", "job-match-v3.0", "job-match-v2.2",
    ]
    assert [item["captured_count"] for item in payload["algorithm_versions"]] == [1, 1, 1]
    assert _bucket(payload, "high", 0)["outcomes"]["offer"]["numerator"] == 1
    assert _bucket(payload, "high", 1)["outcomes"]["hired"]["numerator"] == 1
    assert _bucket(payload, "high", 2)["outcomes"]["interview"]["numerator"] == 1


def test_empty_and_owned_response_exposes_no_snapshot_payload_or_current_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = _user(1400)
    other_id = _user(1401)
    application_id = _application(other_id, "private")
    trigger_id = _event(application_id, "applied")
    _captured_snapshot(application_id, trigger_id, score=100)

    def fail_current_match(*args: object, **kwargs: object) -> None:
        raise AssertionError("current match must not be used")

    monkeypatch.setattr("app.services.job_matching.calculate_match", fail_current_match)
    payload = _summary(owner_id)

    assert payload["snapshot_coverage"] == {
        "applied_application_count": 0,
        "captured_count": 0,
        "unavailable_count": 0,
        "legacy_without_snapshot_count": 0,
        "invalid_anchor_count": 0,
    }
    assert payload["algorithm_versions"] == []
    assert "historical salary" not in str(payload)
    assert "raw matcher detail" not in str(payload)
    assert datetime.fromisoformat(payload["as_of"]).utcoffset() is not None


def test_new_application_between_queries_is_outside_the_fixed_cohort() -> None:
    user_id = _user(1500)
    original_id = _application(user_id, "original")
    original_trigger = _event(original_id, "applied")
    _captured_snapshot(original_id, original_trigger, score=80)

    class SessionWithConcurrentSave:
        def __init__(self) -> None:
            self.session = TestSessionLocal()
            self.execute_calls = 0

        def scalars(self, statement):
            return self.session.scalars(statement)

        def execute(self, statement):
            self.execute_calls += 1
            if self.execute_calls == 1:
                concurrent_id = _application(user_id, "concurrent")
                _event(concurrent_id, "applied")
            return self.session.execute(statement)

        def close(self) -> None:
            self.session.close()

    concurrent_session = SessionWithConcurrentSave()
    try:
        payload = build_application_match_learning_summary(concurrent_session, user_id)
    finally:
        concurrent_session.close()

    assert payload["snapshot_coverage"] == {
        "applied_application_count": 1,
        "captured_count": 1,
        "unavailable_count": 0,
        "legacy_without_snapshot_count": 0,
        "invalid_anchor_count": 0,
    }
