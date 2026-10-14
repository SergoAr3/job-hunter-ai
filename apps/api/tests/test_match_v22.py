"""Candidate contracts: frozen membership, full fallback and read-only dispatch."""
import logging
import os
import subprocess
import sys
from copy import deepcopy
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import OperationalError

from app import config
from app.models import (Application, ApplicationMatchSnapshot, Job, JobSkill, Skill,
                        SkillAlias, User, UserProfile, UserSkill)
from app.services.cover_letter import build_context
from app.services.job_matching import calculate_match
from app.services.match_dispatch import calculate_current_match
from app.services.matcher_owner_read import read_hybrid_skills
from app.services.skill_sync import sync_job_skills, sync_profile_skills
from conftest import TestSessionLocal, client
from test_match_api import create_application, create_user, put_profile


def persisted_pair(user_skills=None, required=None, preferred=None, **job_changes):
    uid = create_user(2200)
    put_profile(uid, skills=user_skills if user_skills is not None else ["Node.js"])
    aid, jid = create_application(uid, required_skills=required if required is not None else ["NodeJS"],
                                  nice_to_have_skills=preferred or [], **job_changes)
    with TestSessionLocal() as session:
        sync_job_skills(session, session.get(Job, jid))
        session.commit()
        pid = session.scalar(select(UserProfile.id).where(UserProfile.user_id == uid))
    return uid, pid, aid, jid


def owners(session, pair):
    return session.get(UserProfile, pair[1]), session.get(Job, pair[3]), session.get(Application, pair[2])


def candidate(session, pair):
    return calculate_current_match(session, *owners(session, pair), algorithm_version="job-match-v2.2")


def assert_full_legacy(session, pair):
    result = candidate(session, pair)
    assert result.algorithm_version == "job-match-v2.1"
    assert result == calculate_match(*owners(session, pair))
    return result


def database_state(session):
    return {model.__tablename__: [tuple(deepcopy(getattr(row, column.key)) for column in model.__table__.columns)
                                 for row in session.scalars(select(model).order_by(*model.__table__.primary_key.columns))]
            for model in (Skill, SkillAlias, UserSkill, JobSkill, UserProfile, Job, ApplicationMatchSnapshot)}


@pytest.mark.parametrize("left,right", [
    ("Postgres", "PostgreSQL"), ("JS", "JavaScript"), ("REST", "REST API"),
    ("NodeJS", "Node.js"), ("ReactJS", "React"), ("React.js", "React"), ("REST-API", "REST API"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_frozen_equivalences_labels_reasons_and_total(left, right, reverse):
    profile, label = (left, right) if reverse else (right, left)
    pair = persisted_pair([profile], [label])
    with TestSessionLocal() as session:
        result = candidate(session, pair)
        assert result.algorithm_version == "job-match-v2.2"
        skills = result.components["required_skills"]
        assert (skills.score, skills.matched, skills.missing) == (100, [label], [])
        assert result.score == 100
        assert [reason.value for reason in result.strengths if reason.component == "required_skills"] == [label]
        assert not [reason for reason in result.gaps if reason.component == "required_skills"]


@pytest.mark.parametrize("left,right", [
    ("C", "C#"), ("C", "C++"), ("C#", "C++"), ("R", "Rust"), ("Go", "Golang"),
    (".NET", "NET"), ("RESTful", "REST API"), ("Node-JS", "Node.js"), ("React js", "React.js"),
])
def test_negative_identities(left, right):
    pair = persisted_pair([left], [right], [right])
    with TestSessionLocal() as session:
        result = candidate(session, pair)
        assert result.algorithm_version == "job-match-v2.2"
        for name in ("required_skills", "nice_to_have_skills"):
            component = result.components[name]
            assert (component.score, component.matched, component.missing) == (0, [], [right])
        assert result.score == 50


@pytest.mark.parametrize("required,matched,missing,score", [
    (["Postgres", "Postgres", "Docker"], ["Postgres", "Postgres"], ["Docker"], 65),
    (["POSTGRES", "postgres", "Docker"], ["POSTGRES", "postgres"], ["Docker"], 65),
    (["Postgres", "PostgreSQL", "Docker"], ["Postgres", "PostgreSQL"], ["Docker"], 65),
    (["Docker", "Postgres", "Docker", "PostgreSQL", "Docker"], ["Postgres", "PostgreSQL"],
     ["Docker", "Docker", "Docker"], 40),
])
def test_semantic_occurrences_not_distinct_denominator(required, matched, missing, score):
    pair = persisted_pair(["PostgreSQL"], required, ["Postgres", "Docker", "PostgreSQL"])
    with TestSessionLocal() as session:
        result = candidate(session, pair)
        component = result.components["required_skills"]
        assert (component.matched, component.missing, component.score) == (matched, missing, score)
        assert result.components["nice_to_have_skills"].matched == ["Postgres", "PostgreSQL"]
        assert result.components["nice_to_have_skills"].score == 65
        state = read_hybrid_skills(session, *owners(session, pair)[:2])
        assert [item.position for item in state.required] == list(range(len(required)))
        assert [item.label for item in state.required] == required
        assert all(item.bucket == "required" for item in state.required)
        assert len({item.skill_id for item in state.required}) == 2


def test_bucket_overlap_and_intentional_score_verdict_recommendation_delta():
    pair = persisted_pair(preferred=["NodeJS"])
    with TestSessionLocal() as session:
        legacy = calculate_match(*owners(session, pair))
        result = candidate(session, pair)
        assert (legacy.score, legacy.verdict, legacy.recommendation.code) == (50, "medium", "apply_with_risks")
        assert (result.score, result.verdict, result.recommendation.code) == (100, "high", "apply")
        assert result.coverage == legacy.coverage == 70
        assert result.confidence == legacy.confidence == "medium"
        assert result.components["required_skills"].weight == 30
        assert result.components["nice_to_have_skills"].weight == 5
        assert result.components["required_skills"].matched == result.components["nice_to_have_skills"].matched == ["NodeJS"]


@pytest.mark.parametrize("user,required,preferred,changes", [
    (["Python", "Postgres"], ["PYTHON", "PostgreSQL", "Docker"], ["JS"], {}),
    ([], ["Python"], [], {}), ([], [], [], {"title": None, "seniority": "unknown"}),
    (["Python"], ["Python"], ["Python"], {"ai_enrichment_status": "failed"}),
    (["JS", "REST"], ["JavaScript", "REST API"], [], {"workplace_type": "remote"}),
])
def test_exact_score_parity_except_actual_version(user, required, preferred, changes):
    pair = persisted_pair(user, required, preferred, **changes)
    with TestSessionLocal() as session:
        legacy = calculate_match(*owners(session, pair)).model_dump()
        result = candidate(session, pair).model_dump()
        assert result.pop("algorithm_version") == "job-match-v2.2"
        assert legacy.pop("algorithm_version") == "job-match-v2.1"
        assert result == legacy


@pytest.mark.parametrize("salary_min,expected_conflict", [(3000, False), (7000, True)])
def test_full_component_math_reasons_and_recommendation_parity(salary_min, expected_conflict):
    pair = persisted_pair(["Python", "JS", "REST"], ["Python", "JavaScript", "REST API", "Docker"], ["SQL"],
                          workplace_type="hybrid", location="Yerevan", language_requirements=["English B2", "German C1"],
                          salary_min=3000, salary_max=4000, salary_currency="USD", salary_period="month")
    put_profile(pair[0], skills=["Python", "JS", "REST"], experience="junior", workplace_preference="hybrid",
                location=["Yerevan"], languages=[{"language": "English", "level": "B2"}],
                salary_min=salary_min, salary_currency="USD", salary_period="month")
    with TestSessionLocal() as session:
        legacy = calculate_match(*owners(session, pair)).model_dump()
        result = candidate(session, pair).model_dump()
        assert result.pop("algorithm_version") == "job-match-v2.2"
        legacy.pop("algorithm_version")
        assert result == legacy
        assert bool(result["conflicts"]) is expected_conflict
        assert result["coverage"] == 100 and result["confidence"] == "high"


@pytest.mark.parametrize("user,required", [
    (["PostgreSQL"], ["Postgres", "PostgreSQL", "Docker"]),
    (["Node.js"], ["NodeJS", "NodeJS", "Docker"]),
    (["React"], ["ReactJS", "React.js", "Docker"]),
    (["REST API"], ["REST-API", "REST"]),
])
def test_candidate_seed_semantics_parity_with_unchanged_shadow_hybrid(user, required):
    from app.services.matching_shadow import compare_pair
    from app.services.skill_identity_reader import read_semantic_skills, read_skill_identities
    pair = persisted_pair(user, required, required)
    with TestSessionLocal() as session:
        profile, job, _ = owners(session, pair)
        keys = read_semantic_skills(profile.skills).keys | read_semantic_skills(job.required_skills).keys | read_semantic_skills(job.nice_to_have_skills).keys
        comparison = compare_pair(profile, job, read_skill_identities(session, keys, {profile.id}, {job.id}))
        assert comparison.status == "comparable"
        hybrid = comparison.views["hybrid"]
        match = candidate(session, pair)
        assert (match.score, match.verdict, match.coverage, match.confidence, match.recommendation.code) == (
            hybrid.score, hybrid.verdict, hybrid.coverage, hybrid.confidence, hybrid.recommendation)
        for bucket, name in (("required", "required_skills"), ("preferred", "nice_to_have_skills")):
            assert match.components[name] == hybrid.skills[bucket].component
            assert hybrid.skills[bucket].denominator == len(required)


@pytest.mark.parametrize("drift", ["missing_user", "extra_user", "missing_job", "extra_job",
                                    "wrong_kind", "equal_count_user", "equal_count_job"])
def test_relation_drift_full_fallback(drift, caplog):
    pair = persisted_pair()
    with TestSessionLocal() as session:
        python_id = session.scalar(select(Skill.id).where(Skill.normalized_key == "python"))
        if drift in {"missing_user", "equal_count_user"}:
            session.execute(delete(UserSkill).where(UserSkill.user_profile_id == pair[1]))
        if drift in {"extra_user", "equal_count_user"}:
            session.add(UserSkill(user_profile_id=pair[1], skill_id=python_id))
        if drift in {"missing_job", "equal_count_job", "wrong_kind"}:
            session.execute(delete(JobSkill).where(JobSkill.job_id == pair[3]))
        if drift in {"extra_job", "equal_count_job"}:
            session.add(JobSkill(job_id=pair[3], skill_id=python_id, requirement_kind="required"))
        if drift == "wrong_kind":
            node_id = session.scalar(select(Skill.id).where(Skill.normalized_key == "node.js"))
            session.add(JobSkill(job_id=pair[3], skill_id=node_id, requirement_kind="preferred"))
        session.commit()
        with caplog.at_level(logging.INFO, logger="app.services.match_dispatch"):
            assert_full_legacy(session, pair)
        assert caplog.messages == ["match_v22_fallback reason_code=owner_relation_drift"]


@pytest.mark.parametrize("corruption", ["missing_alias", "wrong_target", "canonical_owner"])
def test_registry_incompatible_full_fallback(corruption, caplog):
    pair = persisted_pair()
    with TestSessionLocal() as session:
        if corruption == "missing_alias":
            session.delete(session.get(SkillAlias, "reactjs"))
        else:
            alias = session.get(SkillAlias, "react" if corruption == "canonical_owner" else "nodejs")
            alias.skill_id = session.scalar(select(Skill.id).where(Skill.normalized_key == "python"))
        session.commit()
        with caplog.at_level(logging.INFO, logger="app.services.match_dispatch"):
            assert_full_legacy(session, pair)
        assert caplog.messages == ["match_v22_fallback reason_code=registry_incompatible"]


def test_unused_extra_alias_does_not_change_evaluation():
    pair = persisted_pair()
    with TestSessionLocal() as session:
        original = candidate(session, pair)
        skill_id = session.get(SkillAlias, "node.js").skill_id
        session.add(SkillAlias(normalized_alias="private mutable synonym", alias="Private mutable synonym", skill_id=skill_id))
        session.commit()
        assert candidate(session, pair) == original
        assert original.algorithm_version == "job-match-v2.2"


def test_go_golang_mutable_merge_cannot_expand_matcher(caplog):
    pair = persisted_pair(["Go"], ["Golang"])
    with TestSessionLocal() as session:
        original = candidate(session, pair)
        assert original.algorithm_version == "job-match-v2.2"
        assert original.components["required_skills"].missing == ["Golang"]
        go_alias = session.get(SkillAlias, "go")
        golang_id = session.get(SkillAlias, "golang").skill_id
        go_alias.skill_id = golang_id
        session.execute(delete(UserSkill).where(UserSkill.user_profile_id == pair[1]))
        session.add(UserSkill(user_profile_id=pair[1], skill_id=golang_id))
        session.commit()
        with caplog.at_level(logging.INFO, logger="app.services.match_dispatch"):
            fallback = assert_full_legacy(session, pair)
        assert fallback.components["required_skills"].missing == ["Golang"]
        assert "Go" not in caplog.text and "Golang" not in caplog.text


@pytest.mark.parametrize("missing", ["unknown_alias", "canonical_alias"])
def test_unknown_exact_identity_must_resolve_own_canonical(missing):
    pair = persisted_pair(["Django"], ["Django"])
    with TestSessionLocal() as session:
        assert candidate(session, pair).algorithm_version == "job-match-v2.2"
        alias = session.get(SkillAlias, "django")
        if missing == "unknown_alias":
            alias.skill_id = session.get(SkillAlias, "python").skill_id
        else:
            session.delete(alias)
        session.commit()
        assert_full_legacy(session, pair)


@pytest.mark.parametrize("table", ["skills", "skill_aliases", "user_skills", "job_skills"])
def test_schema_missing_full_fallback(table):
    pair = persisted_pair()
    with TestSessionLocal() as session:
        connection = session.connection()
        if table == "skills":
            for dependent in (JobSkill, UserSkill, SkillAlias):
                dependent.__table__.drop(connection)
        {"skills": Skill, "skill_aliases": SkillAlias, "user_skills": UserSkill, "job_skills": JobSkill}[table].__table__.drop(connection)
        assert_full_legacy(session, pair)
        assert session.is_active


@pytest.mark.parametrize("value", [None, 42, {"skill": "NodeJS"}, "NodeJS", [None], [42], [{}]])
def test_malformed_skills_never_mask_corruption(value):
    pair = persisted_pair()
    with TestSessionLocal() as session:
        profile, job, _ = owners(session, pair)
        job.required_skills = value
        session.commit()
        with pytest.raises(ValueError, match="^invalid_match_semantic_input$"):
            candidate(session, pair)


@pytest.mark.parametrize("label", ["", "\u200bprivate", "x" * 101])
def test_frozen_invalid_but_legacy_string_uses_full_legacy(label, caplog):
    pair = persisted_pair()
    with TestSessionLocal() as session:
        owners(session, pair)[1].required_skills = [label]
        session.commit()
        with caplog.at_level(logging.INFO, logger="app.services.match_dispatch"):
            assert_full_legacy(session, pair)
        assert caplog.messages == ["match_v22_fallback reason_code=invalid_semantic_input"]


def test_db_read_error_savepoint_preserves_caller_transaction_and_private_diagnostics(caplog):
    pair = persisted_pair()
    with TestSessionLocal() as session:
        connection = session.connection()
        def fail(conn, cursor, statement, parameters, context, executemany):
            if "FROM job_skills" in statement:
                raise OperationalError("private SQL", {"secret": "private"}, Exception("private label"))
        event.listen(connection, "before_cursor_execute", fail)
        try:
            with caplog.at_level(logging.INFO, logger="app.services.match_dispatch"):
                assert_full_legacy(session, pair)
        finally:
            event.remove(connection, "before_cursor_execute", fail)
        assert caplog.messages == ["match_v22_fallback reason_code=db_consistency_failure"]
        assert session.is_active and session.in_transaction()
        owners(session, pair)[2].note = "caller still owns write"
        session.commit()


def test_no_write_no_flush_no_transaction_ownership(monkeypatch):
    pair = persisted_pair()
    with TestSessionLocal() as session:
        before = database_state(session)
        models = owners(session, pair)
        for name in ("flush", "commit", "rollback", "close"):
            monkeypatch.setattr(session, name, Mock(side_effect=AssertionError(f"matcher called {name}")))
        result = calculate_current_match(session, *models, algorithm_version="job-match-v2.2")
        assert result.algorithm_version == "job-match-v2.2"
        assert database_state(session) == before
        assert not session.new and not session.dirty and not session.deleted
        assert session.in_transaction()
        monkeypatch.undo()


def test_pending_caller_mutation_is_not_flushed_or_overwritten(monkeypatch):
    pair = persisted_pair()
    with TestSessionLocal() as session:
        profile, job, application = owners(session, pair)
        profile.skills = ["Caller unsaved skill"]
        session.add(User(telegram_id=3300))
        monkeypatch.setattr(session, "flush", Mock(side_effect=AssertionError("unexpected flush")))
        assert calculate_current_match(session, profile, job, application, algorithm_version="job-match-v2.2") == calculate_match(profile, job, application)
        assert profile.skills == ["Caller unsaved skill"]
        assert session.new and session.dirty
        monkeypatch.undo()


def test_fallback_lazy_semantic_read_cannot_autoflush_pending_caller_mutations(monkeypatch):
    pair = persisted_pair()
    with TestSessionLocal(autoflush=True) as session:
        profile, job, application = owners(session, pair)
        session.expire(profile, ["skills"])
        session.add(User(telegram_id=4400))
        with monkeypatch.context() as patch:
            patch.setattr(session, "flush", Mock(side_effect=AssertionError("fallback autoflushed")))
            result = calculate_current_match(session, profile, job, application, algorithm_version="job-match-v2.2")
        assert result.algorithm_version == "job-match-v2.1"
        assert session.new


def test_flushed_uncommitted_semantics_and_relations_are_visible_and_caller_can_rollback():
    pair = persisted_pair()
    with TestSessionLocal() as session:
        profile, job, _ = owners(session, pair)
        profile.skills = ["React"]
        job.required_skills = ["ReactJS"]
        sync_profile_skills(session, profile)
        sync_job_skills(session, job)
        assert not session.dirty and not session.new
        result = candidate(session, pair)
        assert result.algorithm_version == "job-match-v2.2"
        assert result.components["required_skills"].matched == ["ReactJS"]
        session.rollback()
    with TestSessionLocal() as session:
        assert session.get(UserProfile, pair[1]).skills == ["Node.js"]
        assert session.get(Job, pair[3]).required_skills == ["NodeJS"]


def test_detached_owner_falls_back_without_reading_another_session(monkeypatch):
    pair = persisted_pair()
    with TestSessionLocal() as first:
        profile, job, application = owners(first, pair)
    monkeypatch.setattr("app.services.match_dispatch.read_hybrid_skills", Mock(side_effect=AssertionError("unsafe refresh")))
    with TestSessionLocal() as second:
        assert calculate_current_match(second, profile, job, application, algorithm_version="job-match-v2.2") == calculate_match(profile, job, application)


def test_wrong_session_fallback_does_not_autoflush_object_owner_session(monkeypatch):
    pair = persisted_pair()
    with TestSessionLocal(autoflush=True) as first, TestSessionLocal() as second:
        profile, job, application = owners(first, pair)
        first.expire(profile, ["skills"])
        first.add(User(telegram_id=5500))
        with monkeypatch.context() as patch:
            patch.setattr(first, "flush", Mock(side_effect=AssertionError("owner autoflushed")))
            result = calculate_current_match(second, profile, job, application, algorithm_version="job-match-v2.2")
        assert result.algorithm_version == "job-match-v2.1"
        assert first.new


@pytest.mark.parametrize("version", [None, "job-match-v2.1", "job-match-v2.2", "latest", ""])
def test_config_validation_unset_explicit_invalid(version):
    env = dict(os.environ)
    env.pop("MATCH_ALGORITHM_VERSION", None)
    if version is not None:
        env["MATCH_ALGORITHM_VERSION"] = version
    result = subprocess.run([sys.executable, "-c", "from app.config import MATCH_ALGORITHM_VERSION; print(MATCH_ALGORITHM_VERSION)"],
                            env=env, capture_output=True, text=True, timeout=10)
    if version in {"latest", ""}:
        assert result.returncode != 0 and "unsupported_match_algorithm_version" in result.stderr
    else:
        assert result.returncode == 0
        assert result.stdout.strip() == (version or "job-match-v2.1")


def test_default_dispatch_does_not_touch_foundation(monkeypatch):
    assert config.MATCH_ALGORITHM_VERSION == "job-match-v2.1"
    pair = persisted_pair()
    monkeypatch.setattr("app.services.match_dispatch.read_hybrid_skills", Mock(side_effect=AssertionError("candidate called")))
    with TestSessionLocal() as session:
        assert calculate_current_match(session, *owners(session, pair)) == calculate_match(*owners(session, pair))
    assert client.get(f"/users/{pair[0]}/applications/{pair[2]}/match").json()["algorithm_version"] == "job-match-v2.1"


def test_discover_stays_legacy_preview_under_v22_config(monkeypatch):
    from app.services.discover import _discover_item
    from test_discover_api import vacancy
    pair = persisted_pair()
    monkeypatch.setattr(config, "MATCH_ALGORITHM_VERSION", "job-match-v2.2")
    monkeypatch.setattr("app.services.match_dispatch.read_hybrid_skills", Mock(side_effect=AssertionError("candidate called")))
    with TestSessionLocal() as session:
        item = _discover_item(vacancy(), owners(session, pair)[0], None)
    assert item.preview_match.algorithm_version == "job-match-v2.1"
    assert item.preview_match.components["required_skills"].score is None


@pytest.mark.parametrize("drift", [False, True])
def test_current_get_truthfulness_explanation_and_no_write(monkeypatch, drift):
    pair = persisted_pair()
    monkeypatch.setattr(config, "MATCH_ALGORITHM_VERSION", "job-match-v2.2")
    with TestSessionLocal() as session:
        if drift:
            session.execute(delete(JobSkill).where(JobSkill.job_id == pair[3]))
            session.commit()
        before = database_state(session)
    response = client.get(f"/users/{pair[0]}/applications/{pair[2]}/match")
    assert response.status_code == 200
    payload = response.json()
    assert payload["algorithm_version"] == ("job-match-v2.1" if drift else "job-match-v2.2")
    assert payload["components"]["required_skills"]["matched" if not drift else "missing"] == ["NodeJS"]
    assert payload["explainability"]["schema_version"] == 1
    with TestSessionLocal() as session:
        assert database_state(session) == before


@pytest.mark.parametrize("configured,drift,actual", [
    ("job-match-v2.1", False, "job-match-v2.1"),
    ("job-match-v2.2", False, "job-match-v2.2"),
    ("job-match-v2.2", True, "job-match-v2.1"),
])
def test_new_snapshots_use_actual_version_and_remain_immutable(monkeypatch, configured, drift, actual):
    pair = persisted_pair()
    monkeypatch.setattr(config, "MATCH_ALGORITHM_VERSION", configured)
    with TestSessionLocal() as session:
        if drift:
            session.execute(delete(JobSkill).where(JobSkill.job_id == pair[3]))
            session.commit()
    endpoint = f"/users/{pair[0]}/applications/{pair[2]}/status"
    assert client.put(endpoint, json={"status": "applied"}).status_code == 200
    with TestSessionLocal() as session:
        snapshot = session.scalar(select(ApplicationMatchSnapshot))
        assert snapshot.capture_status == "captured" and snapshot.algorithm_version == actual
        assert snapshot.inputs["job"]["required_skills"] == ["NodeJS"]
        before = database_state(session)["application_match_snapshots"]
    monkeypatch.setattr(config, "MATCH_ALGORITHM_VERSION", "job-match-v2.1")
    assert client.put(endpoint, json={"status": "interview"}).status_code == 200
    assert client.put(endpoint, json={"status": "applied"}).status_code == 200
    with TestSessionLocal() as session:
        assert database_state(session)["application_match_snapshots"] == before


def test_malformed_candidate_snapshot_uses_existing_safe_unavailable_marker(monkeypatch):
    from app.models import ApplicationStatus
    from app.services.applications import set_application_status
    pair = persisted_pair()
    monkeypatch.setattr(config, "MATCH_ALGORITHM_VERSION", "job-match-v2.2")
    with TestSessionLocal() as session:
        session.get(Job, pair[3]).required_skills = [42]
        session.commit()
    with TestSessionLocal() as session:
        # Exercise the snapshot domain flow. A corrupt Job cannot also satisfy
        # the existing endpoint's JobOut string-array validation.
        assert set_application_status(session, pair[0], pair[2], ApplicationStatus.APPLIED) is not None
        snapshot = session.scalar(select(ApplicationMatchSnapshot))
        assert snapshot.capture_status == "unavailable"
        assert snapshot.unavailable_reason == "matcher_error"
        assert snapshot.algorithm_version is None and snapshot.score is None
        assert session.get(Application, pair[2]).status == "applied"


@pytest.mark.parametrize("configured,drift,actual", [
    ("job-match-v2.1", False, "job-match-v2.1"),
    ("job-match-v2.2", False, "job-match-v2.2"),
    ("job-match-v2.2", True, "job-match-v2.1"),
])
def test_cover_letter_context_consumes_actual_evaluation(monkeypatch, configured, drift, actual):
    from app.services import cover_letter
    pair = persisted_pair()
    monkeypatch.setattr(config, "MATCH_ALGORITHM_VERSION", configured)
    dispatch = Mock(wraps=calculate_current_match)
    monkeypatch.setattr(cover_letter, "calculate_current_match", dispatch)
    with TestSessionLocal() as session:
        if drift:
            session.execute(delete(UserSkill).where(UserSkill.user_profile_id == pair[1]))
            session.commit()
        before = database_state(session)
        context = build_context(session, pair[0], pair[2], "en")
        matching = calculate_current_match(session, *owners(session, pair))
        assert matching.algorithm_version == actual
        assert context.matching == {name: [reason.model_dump() for reason in getattr(matching, name)]
                                    for name in ("strengths", "gaps", "conflicts", "unknowns")}
        assert dispatch.call_count == 1
        assert database_state(session) == before
