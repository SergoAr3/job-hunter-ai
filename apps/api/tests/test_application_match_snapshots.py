from copy import deepcopy
from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models import (
    Application,
    ApplicationMatchSnapshot,
    ApplicationStatus,
    ApplicationStatusHistory,
    Job,
    UserProfile,
)
from app.services.applications import (
    _application_for_status_update_statement,
    set_application_status,
)
from app.services.job_matching import serialize_match_inputs
from conftest import TestSessionLocal, client
from test_match_api import create_application, create_user, put_profile


def _set_status(user_id: int, application_id: int, status: str):
    return client.put(
        f"/users/{user_id}/applications/{application_id}/status",
        json={"status": status},
    )


def _snapshot(application_id: int) -> ApplicationMatchSnapshot | None:
    with TestSessionLocal() as session:
        return session.scalar(
            select(ApplicationMatchSnapshot).where(
                ApplicationMatchSnapshot.application_id == application_id
            )
        )


def _snapshot_values(snapshot: ApplicationMatchSnapshot) -> dict[str, object]:
    return {
        column.name: deepcopy(getattr(snapshot, column.name))
        for column in ApplicationMatchSnapshot.__table__.columns
    }


def _full_profile(user_id: int) -> None:
    put_profile(
        user_id,
        target_roles=["Backend Developer"],
        skills=["Python", "Docker"],
        experience="middle",
        languages=[{"language": "English", "level": "C1"}],
        location=["Yerevan"],
        workplace_preference="hybrid",
        salary_min=3000,
        salary_currency="USD",
        salary_period="month",
    )


def _full_application(user_id: int) -> tuple[int, int]:
    return create_application(
        user_id,
        title="Backend Developer",
        required_skills=["Python"],
        nice_to_have_skills=["Docker"],
        seniority="middle",
        language_requirements=["English B2"],
        location="Yerevan",
        workplace_type="hybrid",
        salary_min=Decimal("3000.00"),
        salary_max=Decimal("4000.00"),
        salary_currency="USD",
        salary_period="month",
        salary_period_inferred=False,
        parsing_status="success",
        ai_enrichment_status="success",
    )


def test_saved_application_creation_does_not_create_snapshot() -> None:
    user_id = create_user(900)

    response = client.post(
        f"/users/{user_id}/applications",
        json={"source_url": "https://example.com/snapshot/saved"},
    )

    assert response.status_code == 200
    application_id = response.json()["application"]["id"]
    assert _snapshot(application_id) is None


def test_first_applied_captures_metrics_result_inputs_and_trigger() -> None:
    user_id = create_user(901)
    _full_profile(user_id)
    application_id, _ = _full_application(user_id)

    response = _set_status(user_id, application_id, "applied")

    assert response.status_code == 200
    snapshot = _snapshot(application_id)
    assert snapshot is not None
    assert snapshot.snapshot_schema_version == 1
    assert snapshot.capture_status == "captured"
    assert snapshot.unavailable_reason is None
    assert snapshot.algorithm_version == "job-match-v2.1"
    assert (snapshot.score, snapshot.verdict, snapshot.coverage, snapshot.confidence) == (
        100, "high", 100, "high",
    )
    assert snapshot.job_parsing_status == "success"
    assert snapshot.job_ai_enrichment_status == "success"
    assert snapshot.profile_updated_at is not None
    assert snapshot.job_updated_at is not None
    assert set(snapshot.result_detail or {}) == {
        "components", "strengths", "gaps", "unknowns", "conflicts", "recommendation",
    }
    assert (snapshot.result_detail or {})["recommendation"]["code"] == "apply"
    assert "score" not in (snapshot.result_detail or {})
    assert "algorithm_version" not in (snapshot.result_detail or {})

    assert set((snapshot.inputs or {})["profile"]) == {
        "target_roles", "skills", "experience", "languages", "location",
        "workplace_preference", "salary_min", "salary_currency", "salary_period",
    }
    assert set((snapshot.inputs or {})["job"]) == {
        "title", "required_skills", "nice_to_have_skills", "seniority",
        "language_requirements", "location", "workplace_type", "salary_min",
        "salary_max", "salary_currency", "salary_period", "salary_period_inferred",
        "ai_enrichment_status",
    }
    assert (snapshot.inputs or {})["profile"]["salary_min"] == "3000.00"
    assert (snapshot.inputs or {})["job"]["salary_max"] == "4000.00"

    with TestSessionLocal() as session:
        trigger = session.get(ApplicationStatusHistory, snapshot.trigger_status_history_id)
        assert trigger is not None
        assert trigger.application_id == application_id
        assert trigger.status == "applied"


def test_input_serializer_has_exact_limited_keys_and_lossless_decimals() -> None:
    user_id = create_user(902)
    _full_profile(user_id)
    _, job_id = _full_application(user_id)
    with TestSessionLocal() as session:
        job = session.get(Job, job_id)
        assert job is not None
        user_profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
        assert user_profile is not None

        payload = serialize_match_inputs(user_profile, job)

    assert set(payload) == {"profile", "job"}
    assert set(payload["profile"]) == {
        "target_roles", "skills", "experience", "languages", "location",
        "workplace_preference", "salary_min", "salary_currency", "salary_period",
    }
    assert set(payload["job"]) == {
        "title", "required_skills", "nice_to_have_skills", "seniority",
        "language_requirements", "location", "workplace_type", "salary_min", "salary_max",
        "salary_currency", "salary_period", "salary_period_inferred", "ai_enrichment_status",
    }
    assert payload["profile"]["salary_min"] == "3000.00"
    assert payload["job"]["salary_min"] == "3000.00"
    assert payload["job"]["salary_max"] == "4000.00"


def test_insufficient_data_is_captured_with_null_score() -> None:
    user_id = create_user(903)
    put_profile(user_id, target_roles=["Backend Developer"], skills=[], experience="unknown")
    application_id, _ = create_application(
        user_id,
        title=None,
        required_skills=[],
        nice_to_have_skills=[],
        seniority="unknown",
        language_requirements=[],
        workplace_type="unknown",
        ai_enrichment_status="failed",
    )

    assert _set_status(user_id, application_id, "applied").status_code == 200

    snapshot = _snapshot(application_id)
    assert snapshot is not None
    assert snapshot.capture_status == "captured"
    assert snapshot.unavailable_reason is None
    assert snapshot.score is None
    assert snapshot.verdict == "insufficient_data"
    assert snapshot.confidence is None
    assert snapshot.coverage == 0


def test_repeat_applied_profile_job_and_algorithm_changes_do_not_mutate_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = create_user(904)
    _full_profile(user_id)
    application_id, job_id = _full_application(user_id)
    assert _set_status(user_id, application_id, "applied").status_code == 200
    original = _snapshot(application_id)
    assert original is not None
    original_values = _snapshot_values(original)

    put_profile(user_id, target_roles=["Designer"], skills=[], experience="junior")
    with TestSessionLocal() as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.title = "Product Designer"
        job.required_skills = ["Figma"]
        job.ai_enrichment_status = "failed"
        job.parsing_status = "failed"
        session.commit()
    monkeypatch.setattr("app.services.job_matching.ALGORITHM_VERSION", "job-match-v99")

    assert _set_status(user_id, application_id, "interview").status_code == 200
    assert _set_status(user_id, application_id, "applied").status_code == 200
    assert _set_status(user_id, application_id, "applied").status_code == 200

    current = _snapshot(application_id)
    assert current is not None
    assert _snapshot_values(current) == original_values
    with TestSessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ApplicationMatchSnapshot)) == 1
        applied_events = session.scalar(
            select(func.count()).select_from(ApplicationStatusHistory).where(
                ApplicationStatusHistory.application_id == application_id,
                ApplicationStatusHistory.status == "applied",
            )
        )
        assert applied_events == 2


def test_historical_applied_without_snapshot_never_captures_later() -> None:
    user_id = create_user(905)
    _full_profile(user_id)
    application_id, _ = _full_application(user_id)
    with TestSessionLocal() as session:
        session.add(ApplicationStatusHistory(application_id=application_id, status="applied"))
        session.commit()

    assert _set_status(user_id, application_id, "applied").status_code == 200

    assert _snapshot(application_id) is None


def test_legacy_current_applied_without_history_only_captures_after_new_applied_event() -> None:
    user_id = create_user(906)
    _full_profile(user_id)
    application_id, _ = _full_application(user_id)
    with TestSessionLocal() as session:
        application = session.get(Application, application_id)
        assert application is not None
        application.status = "applied"
        session.commit()

    assert _set_status(user_id, application_id, "applied").status_code == 200
    assert _snapshot(application_id) is None
    assert _set_status(user_id, application_id, "saved").status_code == 200
    assert _set_status(user_id, application_id, "applied").status_code == 200

    snapshot = _snapshot(application_id)
    assert snapshot is not None
    with TestSessionLocal() as session:
        trigger = session.get(ApplicationStatusHistory, snapshot.trigger_status_history_id)
        assert trigger is not None and trigger.status == "applied"


def test_missing_profile_is_immutable_unavailable_and_applied_is_saved() -> None:
    user_id = create_user(907)
    application_id, _ = _full_application(user_id)

    assert _set_status(user_id, application_id, "applied").status_code == 200

    snapshot = _snapshot(application_id)
    assert snapshot is not None
    assert snapshot.capture_status == "unavailable"
    assert snapshot.unavailable_reason == "profile_missing"
    assert snapshot.score is None
    assert snapshot.inputs is None
    assert snapshot.result_detail is None
    original_values = _snapshot_values(snapshot)

    _full_profile(user_id)
    assert _set_status(user_id, application_id, "interview").status_code == 200
    assert _set_status(user_id, application_id, "applied").status_code == 200
    current = _snapshot(application_id)
    assert current is not None
    assert _snapshot_values(current) == original_values


def test_matcher_exception_creates_unavailable_without_losing_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = create_user(908)
    _full_profile(user_id)
    application_id, _ = _full_application(user_id)

    def fail(*args: object) -> None:
        raise RuntimeError("matcher failed")

    monkeypatch.setattr("app.services.application_match_snapshots.calculate_match", fail)

    response = _set_status(user_id, application_id, "applied")

    assert response.status_code == 200
    assert response.json()["application"]["status"] == "applied"
    snapshot = _snapshot(application_id)
    assert snapshot is not None
    assert snapshot.capture_status == "unavailable"
    assert snapshot.unavailable_reason == "matcher_error"


def test_foreign_status_update_does_not_create_snapshot() -> None:
    owner, foreign = create_user(909), create_user(910)
    _full_profile(owner)
    application_id, _ = _full_application(owner)

    assert _set_status(foreign, application_id, "applied").status_code == 404
    assert _snapshot(application_id) is None


def test_snapshot_insert_failure_rolls_back_status_history_and_snapshot() -> None:
    user_id = create_user(911)
    _full_profile(user_id)
    application_id, _ = _full_application(user_id)
    with TestSessionLocal() as session:
        def fail_snapshot_insert(current_session, flush_context, instances) -> None:
            if any(isinstance(item, ApplicationMatchSnapshot) for item in current_session.new):
                raise SQLAlchemyError("snapshot insert failed")

        event.listen(session, "before_flush", fail_snapshot_insert)
        try:
            with pytest.raises(SQLAlchemyError, match="snapshot insert failed"):
                set_application_status(session, user_id, application_id, ApplicationStatus.APPLIED)
        finally:
            event.remove(session, "before_flush", fail_snapshot_insert)

        application = session.get(Application, application_id)
        assert application is not None and application.status == "saved"
        assert session.scalar(
            select(func.count()).select_from(ApplicationStatusHistory).where(
                ApplicationStatusHistory.application_id == application_id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(ApplicationMatchSnapshot).where(
                ApplicationMatchSnapshot.application_id == application_id
            )
        ) == 0


def test_outer_commit_failure_rolls_back_first_applied_as_one_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = create_user(912)
    _full_profile(user_id)
    application_id, _ = _full_application(user_id)
    with TestSessionLocal() as session:
        rollback = Mock(wraps=session.rollback)

        def fail_commit() -> None:
            session.flush()
            raise SQLAlchemyError("outer commit failed")

        monkeypatch.setattr(session, "commit", fail_commit)
        monkeypatch.setattr(session, "rollback", rollback)
        with pytest.raises(SQLAlchemyError, match="outer commit failed"):
            set_application_status(session, user_id, application_id, ApplicationStatus.APPLIED)

        rollback.assert_called_once()
        application = session.get(Application, application_id)
        assert application is not None and application.status == "saved"
        assert session.scalar(
            select(func.count()).select_from(ApplicationStatusHistory).where(
                ApplicationStatusHistory.application_id == application_id
            )
        ) == 0
        assert session.scalar(select(func.count()).select_from(ApplicationMatchSnapshot)) == 0


def test_unique_application_constraint_and_application_delete_cascade() -> None:
    user_id = create_user(913)
    _full_profile(user_id)
    application_id, _ = _full_application(user_id)
    assert _set_status(user_id, application_id, "applied").status_code == 200
    snapshot = _snapshot(application_id)
    assert snapshot is not None

    with TestSessionLocal() as session:
        session.add(ApplicationMatchSnapshot(
            application_id=application_id,
            trigger_status_history_id=snapshot.trigger_status_history_id,
            snapshot_schema_version=1,
            capture_status="unavailable",
            unavailable_reason="profile_missing",
        ))
        with pytest.raises(IntegrityError, match="application_match_snapshots.application_id"):
            session.commit()
        session.rollback()

        application = session.get(Application, application_id)
        assert application is not None
        session.delete(application)
        session.commit()
        assert session.scalar(select(func.count()).select_from(ApplicationMatchSnapshot)) == 0
        assert session.scalar(
            select(func.count()).select_from(ApplicationStatusHistory).where(
                ApplicationStatusHistory.application_id == application_id
            )
        ) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"capture_status": "unknown"},
        {"capture_status": "unavailable", "unavailable_reason": "unknown"},
        {"capture_status": "unavailable", "unavailable_reason": "profile_missing", "score": 0},
        {"capture_status": "captured", "unavailable_reason": None, "score": 101},
    ],
)
def test_snapshot_database_checks_reject_inconsistent_rows(changes: dict[str, object]) -> None:
    user_id = create_user(920)
    application_id, _ = _full_application(user_id)
    with TestSessionLocal() as session:
        history = ApplicationStatusHistory(application_id=application_id, status="applied")
        session.add(history)
        session.flush()
        values: dict[str, object] = {
            "application_id": application_id,
            "trigger_status_history_id": history.id,
            "snapshot_schema_version": 1,
            "capture_status": "unavailable",
            "unavailable_reason": "profile_missing",
        }
        values.update(changes)
        session.add(ApplicationMatchSnapshot(**values))
        with pytest.raises(IntegrityError):
            session.commit()


def test_status_update_statement_uses_postgresql_row_lock_for_every_transition() -> None:
    statement = _application_for_status_update_statement(user_id=7, application_id=11)

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in sql
