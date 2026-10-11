from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select
from fastapi.testclient import TestClient

from app.main import app
from app.models import Application, Job, JobSkill, Skill, SkillAlias, UserProfile, UserSkill
from app.schemas import MatchResultOut
from app.services.job_matching import calculate_match
from app.services.match_explainability import explain_match
from app.services.application_match_snapshots import prepare_match_snapshot
from app.services.skill_sync import sync_job_skills, sync_profile_skills
from conftest import TestSessionLocal
from test_job_matching import job, profile, match
from test_match_api import create_user, put_profile, create_application
from test_auth import register, login, bearer


def explanations(result, name):
    return result.components[name]


@pytest.mark.parametrize("changes", [
    {}, {"skills": []}, {"languages": []},
    {"languages": [{"language": "English", "level": "unknown"}]},
    {"salary_min": None}, {"salary_currency": "EUR"},
    {"experience": "unknown"}, {"target_roles": []},
])
def test_explanation_does_not_mutate_any_legacy_match_field(changes):
    p, j = profile(**changes), job(language_requirements=["English B2", "German conversational"])
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    before = deepcopy(result.model_dump(exclude={"explainability"}))
    explanation = explain_match(p, j, result)
    enriched = result.model_copy(update={"explainability": explanation})
    assert enriched.model_dump(exclude={"explainability"}) == before
    assert result.model_dump(exclude={"explainability"}) == before
    assert set(explanation.components) == set(result.components)
    assert explanation.schema_version == 1
    assert MatchResultOut.model_validate(before).explainability is None


def test_unknown_skills_preserve_requirements_order_duplicates_and_buckets():
    p = profile(skills=[])
    j = job(required_skills=["Python", "Python", "JS"], nice_to_have_skills=["Python"])
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    explanation = explain_match(p, j, result)
    required = explanations(explanation, "required_skills")
    assert [item.value for item in required.unknown_items] == ["Python", "Python", "JS"]
    assert {item.source for item in required.unknown_items} == {"profile"}
    assert result.components["required_skills"].missing == []
    assert [item.value for item in explanations(explanation, "nice_to_have_skills").unknown_items] == ["Python"]


@pytest.mark.parametrize("status", ["failed", "pending", "not_attempted"])
def test_job_evidence_is_unavailable_even_when_arrays_are_retained(status):
    p, j = profile(), job(ai_enrichment_status=status)
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    explanation = explain_match(p, j, result)
    assert result.components["required_skills"].matched == []
    assert [item.code for item in explanation.components["required_skills"].notices] == ["job_skills_unavailable"]
    assert [item.value for item in explanation.components["required_skills"].unknown_items] == j.required_skills


def test_empty_job_requirements_are_not_absence_of_requirements():
    p, j = profile(), job(required_skills=[], nice_to_have_skills=[])
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    explanation = explain_match(p, j, result)
    for name in ["required_skills", "nice_to_have_skills"]:
        assert [item.code for item in explanation.components[name].notices] == ["job_skills_missing"]
        assert explanation.components[name].unknown_items == []


def test_languages_partial_parse_and_unknown_profile_level_do_not_become_missing():
    p = profile(languages=[{"language": "English", "level": "B2"}, {"language": "German", "level": "unknown"}])
    j = job(language_requirements=["English B2", "German B1", "French conversational", "Russian C1"])
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    explanation = explain_match(p, j, result)
    assert result.components["languages"].matched == ["English B2"]
    assert result.components["languages"].missing == ["Russian C1"]
    assert [(item.code, item.source, item.value) for item in explanation.components["languages"].unknown_items] == [
        ("profile_language_level_unknown", "profile", "German B1"),
        ("language_requirement_unparseable", "job", "French conversational"),
    ]
    # Legacy reasons remain unchanged, including their known unknown-level limitation.
    assert any(item.code == "language_not_listed" and item.value == "German B1" for item in result.gaps)


@pytest.mark.parametrize("p_changes,j_changes,expected", [
    ({}, {"salary_min": None, "salary_max": None}, "job_salary_missing"),
    ({"salary_min": None}, {}, "profile_salary_missing"),
    ({"salary_currency": "EUR"}, {}, "salary_currencies_incompatible"),
    ({}, {"salary_currency": None}, "job_salary_currency_missing"),
    ({"salary_currency": None}, {}, "profile_salary_currency_missing"),
    ({}, {"salary_period": "unknown"}, "job_salary_period_unknown"),
    ({"salary_period": "unknown"}, {}, "profile_salary_period_unknown"),
    ({}, {"salary_period_inferred": True}, "salary_period_inferred"),
    ({}, {"salary_min": Decimal("2000"), "salary_max": None}, "salary_not_comparable"),
])
def test_salary_unknown_causes(p_changes, j_changes, expected):
    p, j = profile(**p_changes), job(**j_changes)
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    assert result.components["salary"].score is None
    assert expected in [item.code for item in explain_match(p, j, result).components["salary"].notices]


@pytest.mark.parametrize("amount,expected", [(Decimal("3500"), "partial"), (Decimal("2000"), "mismatch")])
def test_salary_partial_and_conflict_are_unchanged(amount, expected):
    p, j = profile(), job(salary_min=Decimal("1000"), salary_max=amount)
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    assert result.components["salary"].status == expected
    assert explain_match(p, j, result).components["salary"].notices == []
    assert bool(result.conflicts) == (expected == "mismatch")


def test_remote_location_is_not_a_match_and_any_workplace_is_explained():
    p, j = profile(workplace_preference="any"), job()
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    explanation = explain_match(p, j, result)
    assert result.components["location"].score is None
    assert explanation.components["location"].notices[0].code == "remote_location_not_compared"
    assert explanation.components["workplace"].notices[0].code == "workplace_any"


@pytest.mark.parametrize("user_skill,job_skill,matched", [
    ("Postgres", "PostgreSQL", True), ("JS", "JavaScript", True), ("REST", "REST API", True),
    ("NodeJS", "Node.js", False), ("ReactJS", "React", False), ("REST-API", "REST API", False),
    ("C", "C#", False), ("C#", "C++", False), ("Go", "Django", False),
])
def test_ui_explanation_preserves_scorer_alias_boundaries(user_skill, job_skill, matched):
    p, j = profile(skills=[user_skill]), job(required_skills=[job_skill])
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    before = deepcopy(result.components)
    explain_match(p, j, result)
    assert result.components == before
    assert result.components["required_skills"].matched == ([job_skill] if matched else [])
    assert result.components["required_skills"].missing == ([] if matched else [job_skill])


def test_duplicates_and_same_skill_in_both_buckets_remain_separate():
    result = match(job=job(required_skills=["Python", "Python", "Missing"], nice_to_have_skills=["Python"]))
    assert result.components["required_skills"].score == 65
    assert result.components["required_skills"].matched == ["Python", "Python"]
    assert result.components["nice_to_have_skills"].matched == ["Python"]


def test_api_legacy_fields_identical_with_populated_and_absent_relations():
    from conftest import client
    uid = create_user(101)
    put_profile(uid, skills=["NodeJS", "Python"])
    aid, jid = create_application(uid, required_skills=["Node.js", "Python"])
    with TestSessionLocal() as db:
        p = db.scalar(select(UserProfile).where(UserProfile.user_id == uid))
        j, a = db.get(Job, jid), db.get(Application, aid)
        legacy = calculate_match(p, j, a).model_dump(mode="json", exclude={"explainability"})
        snapshot = prepare_match_snapshot(p, j, a)
        # Remove all foundation rows to compare truly absent vs populated.
        for model in [UserSkill, JobSkill, SkillAlias, Skill]:
            db.execute(delete(model))
        db.commit()
    absent = client.get(f"/users/{uid}/applications/{aid}/match").json()
    assert {k: v for k, v in absent.items() if k != "explainability"} == legacy
    with TestSessionLocal() as db:
        p = db.scalar(select(UserProfile).where(UserProfile.user_id == uid))
        j, a = db.get(Job, jid), db.get(Application, aid)
        sync_profile_skills(db, p)
        sync_job_skills(db, j)
        db.commit()
        assert prepare_match_snapshot(p, j, a) == snapshot
    populated = client.get(f"/users/{uid}/applications/{aid}/match").json()
    assert populated == absent
    assert populated["components"]["required_skills"]["missing"] == ["Node.js"]
    assert set(populated) == set(legacy) | {"explainability"}


def test_bearer_match_ownership_and_profile_required():
    plain = TestClient(app)
    assert register().status_code == 202
    credentials = login()
    with TestSessionLocal() as db:
        from app.models import User
        uid = db.scalar(select(User.id).where(User.email_canonical == "user+tag@example.com"))
    aid, _ = create_application(uid)
    headers = bearer(credentials["session_token"])
    path = f"/users/{uid}/applications/{aid}/match"
    assert plain.get(path).status_code == 401
    assert plain.get(path, headers=headers).json() == {"detail": {"code": "PROFILE_REQUIRED"}}
    put_profile(uid)
    assert plain.get(path, headers=headers).status_code == 200
    other = create_user(102)
    assert plain.get(f"/users/{other}/applications/{aid}/match", headers=headers).status_code == 404
    assert plain.get(f"/users/{uid}/applications/99999/match", headers=headers).status_code == 404


def test_bounded_explanation_codes():
    from app.schemas import MatchExplanationItemOut
    with pytest.raises(ValidationError):
        MatchExplanationItemOut(code="arbitrary error from backend", source="job")


def test_unavailable_job_and_empty_profile_both_have_actionable_notices():
    p, j = profile(skills=[]), job(ai_enrichment_status="failed")
    result = calculate_match(p, j, Application(id=3, user_id=1, job_id=2))
    explanation = explain_match(p, j, result)
    assert {item.code for item in explanation.components["required_skills"].notices} == {
        "profile_skills_missing", "job_skills_unavailable",
    }
    assert result.components["required_skills"].missing == []
