from copy import deepcopy
import sqlite3
import unicodedata

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.models import Job, JobSkill, Skill, SkillAlias, User, UserProfile, UserSkill
from app.services.skill_normalization import InvalidSkillValue, normalize_skill
from app.services.skill_sync import sync_job_skills, sync_profile_skills
from app.services.skill_taxonomy import BOOTSTRAP, BOOTSTRAP_VERSION, SkillAliasConflict, bootstrap_skills, register_alias, resolve_skills
from conftest import TestSessionLocal


@pytest.mark.parametrize("value,key,display", [
    (" PostgreSQL ", "postgresql", "PostgreSQL"),
    ("Ｐｙｔｈｏｎ", "python", "Ｐｙｔｈｏｎ"),
    ("Straße", "strasse", "Straße"),
    (" REST\u00a0\u2003API\t", "rest api", "REST API"),
    ("Node.js", "node.js", "Node.js"), ("C#", "c#", "C#"),
    ("C++", "c++", "C++"), (".NET", ".net", ".NET"),
    ("REST-API", "rest-api", "REST-API"),
    ("my INTERNAL tool", "my internal tool", "my INTERNAL tool"),
    ("C", "c", "C"), ("REST API", "rest api", "REST API"),
    ("Go", "go", "Go"), ("R", "r", "R"),
    ("한국어", "한국어", "한국어"), ("Монгол", "монгол", "Монгол"),
    ("Cafe\u0301", "café", "Cafe\u0301"),
])
def test_identity_and_display(value, key, display):
    assert normalize_skill(value) == (key, display)


@pytest.mark.parametrize("left,right", [("C", "C#"), ("C", "C++"), ("C#", "C++"), (".NET", "NET"), ("R", "Rust"), ("Go", "Golang"), ("RESTful", "REST API")])
def test_no_aggressive_normalization(left, right):
    assert normalize_skill(left)[0] != normalize_skill(right)[0]


@pytest.mark.parametrize("value", [None, 4, {}, "", " \t\n", "x" * 101, "x\x00", "x\x1f", "x\u200b", "x\u202e", "x\u034f", "x\ufe0f", "x\ud800", "\ufdfa" * 100])
def test_invalid_values_do_not_leak(value):
    with pytest.raises(InvalidSkillValue) as caught:
        normalize_skill(value)
    assert str(caught.value) in {"not_string", "blank", "too_long", "forbidden_character"}


@pytest.mark.parametrize("symbol", ["\u115f", "\u1160", "\u3164", "\uffa0", "\u180b", "\u180c", "\u180d", "\u180f"])
@pytest.mark.parametrize("template", ["{}", "Python{}", "{}Python"])
def test_invisible_fillers_and_selectors_rejected_before_and_after_nfkc(symbol, template):
    value = template.format(symbol)
    for candidate in (value, unicodedata.normalize("NFKC", value)):
        with pytest.raises(InvalidSkillValue, match="^forbidden_character$"):
            normalize_skill(candidate)


def test_character_validation_precedes_nfkc(monkeypatch):
    from app.services import skill_normalization
    def unexpected_normalization(*args):
        pytest.fail("Forbidden input must be rejected before normalization")
    with monkeypatch.context() as patch:
        patch.setattr(skill_normalization.unicodedata, "normalize", unexpected_normalization)
        with pytest.raises(InvalidSkillValue, match="^forbidden_character$"):
            normalize_skill("Python\u3164")


def test_character_validation_checks_nfkc_output(monkeypatch):
    from app.services import skill_normalization
    # Exercise the output guard independently of the input guard.
    with monkeypatch.context() as patch:
        patch.setattr(skill_normalization.unicodedata, "normalize", lambda form, value: "Python\u1160")
        with pytest.raises(InvalidSkillValue, match="^forbidden_character$"):
            normalize_skill("Python")


def make_profile(session, skills):
    user = User(telegram_id=1)
    session.add(user)
    session.flush()
    profile = UserProfile(user_id=user.id, target_roles=["Engineer"], skills=skills)
    session.add(profile)
    session.flush()
    return profile


def make_job(session, required=None, preferred=None):
    job = Job(source="company_site", source_url="https://example.com/foundation", required_skills=required or [], nice_to_have_skills=preferred or [])
    session.add(job)
    session.flush()
    return job


def names(session, profile_id):
    return set(session.scalars(select(Skill.canonical_name).join(UserSkill).where(UserSkill.user_profile_id == profile_id)))


def test_profile_sync_skips_invisible_values_without_rewriting_snapshot():
    with TestSessionLocal() as session:
        profile = make_profile(session, ["Python", "Python\u3164", "\u3164"])
        before = deepcopy(profile.skills)
        result = sync_profile_skills(session, profile)
        assert result.invalid_count == 2 and not result.quarantined
        assert result.added == 1 and result.removed == 0
        assert names(session, profile.id) == {"Python"}
        assert session.scalar(select(func.count()).select_from(Skill)) == len(BOOTSTRAP)
        assert session.get(SkillAlias, "python\u1160") is None
        assert session.get(SkillAlias, "\u1160") is None
        session.commit()
        session.refresh(profile)
        assert profile.skills == before
        repeat = sync_profile_skills(session, profile)
        assert repeat.invalid_count == 2 and repeat.added == repeat.removed == 0


def test_bootstrap_exact_versioned_data_and_repeat():
    assert BOOTSTRAP_VERSION == 1
    assert BOOTSTRAP == (
        ("API", ()), ("Git", ()), ("HTML", ()), ("JavaScript", ("JS",)),
        ("Node.js", ("NodeJS",)), ("PostgreSQL", ("Postgres",)), ("Python", ()),
        ("React", ("ReactJS", "React.js")), ("Redis", ()),
        ("REST API", ("REST", "REST-API")), ("SQL", ()),
    )
    with TestSessionLocal() as session:
        bootstrap_skills(session)
        before = list(session.execute(select(Skill.id, Skill.canonical_name, Skill.normalized_key)))
        bootstrap_skills(session)
        assert list(session.execute(select(Skill.id, Skill.canonical_name, Skill.normalized_key))) == before
        for skill in session.scalars(select(Skill)):
            assert session.get(SkillAlias, skill.normalized_key).skill_id == skill.id


@pytest.mark.parametrize("values", [["Postgres", "PostgreSQL"], ["JS", "JavaScript"], ["Node.js", "NodeJS"], ["React", "ReactJS", "React.js"], ["REST", "REST API", "REST-API"]])
def test_explicit_aliases(values):
    with TestSessionLocal() as session:
        assert len(resolve_skills(session, values).skill_ids) == 1


def test_unknown_identity_preserves_display_and_outer_rollback():
    with TestSessionLocal() as session:
        first = resolve_skills(session, ["my INTERNAL tool", "MY internal TOOL", "", 2])
        assert first.created_count == 1 and first.invalid_count == 2
        second = resolve_skills(session, ["MY INTERNAL TOOL"])
        assert second.skill_ids == first.skill_ids and second.created_count == 0
        assert session.get(Skill, next(iter(first.skill_ids))).canonical_name == "my INTERNAL tool"
        session.rollback()
        assert session.scalar(select(func.count()).select_from(Skill)) == 0


def test_alias_conflict_is_explicit_and_bootstrap_atomic():
    with TestSessionLocal() as session:
        bootstrap_skills(session)
        python = session.scalar(select(Skill).where(Skill.normalized_key == "python"))
        postgres = session.scalar(select(Skill).where(Skill.normalized_key == "postgresql"))
        register_alias(session, python, "PYTHON")
        with pytest.raises(SkillAliasConflict):
            register_alias(session, postgres, "Python")
        assert session.get(SkillAlias, "python").skill_id == python.id
        session.rollback()
        # A pre-existing conflicting synonym must roll back the entire seed.
        session.expunge_all()
        skill = Skill(canonical_name="Postgres", normalized_key="postgres")
        session.add(skill)
        session.flush()
        session.add(SkillAlias(normalized_alias="postgres", alias="Postgres", skill_id=skill.id))
        session.flush()
        with pytest.raises(SkillAliasConflict):
            bootstrap_skills(session)
        assert list(session.scalars(select(Skill.normalized_key))) == ["postgres"]
        assert session.scalar(select(func.count()).select_from(SkillAlias)) == 1


def test_canonical_name_collision_resolves_alias_owner_without_orphan(monkeypatch):
    from app.services import skill_taxonomy as taxonomy
    with TestSessionLocal() as session:
        bootstrap_skills(session)
        python = session.scalar(select(Skill).where(Skill.normalized_key == "python"))
        register_alias(session, python, "private tool")
        lookup = taxonomy._lookup
        calls = 0
        def stale_lookup(db, key):
            nonlocal calls
            if key == "private tool":
                calls += 1
                if calls == 1:
                    return None
            return lookup(db, key)
        monkeypatch.setattr(taxonomy, "_lookup", stale_lookup)
        winner, created = taxonomy._create_identity(session, "private tool", "Private tool")
        assert winner.id == python.id and not created
        assert session.scalar(select(Skill).where(Skill.normalized_key == "private tool")) is None
        assert session.scalar(select(func.count()).select_from(Skill)) == len(BOOTSTRAP)


def test_profile_sync_replace_clear_repeat_and_semantic_preservation():
    with TestSessionLocal() as session:
        profile = make_profile(session, ["Postgres", "PostgreSQL", "Python", "Python"])
        before = deepcopy(profile.skills)
        assert sync_profile_skills(session, profile).added == 2
        assert sync_profile_skills(session, profile).added == 0
        assert profile.skills == before
        assert names(session, profile.id) == {"Python", "PostgreSQL"}
        profile.skills = ["Python", "Docker"]
        result = sync_profile_skills(session, profile)
        assert (result.added, result.removed) == (1, 1)
        profile.skills = []
        assert sync_profile_skills(session, profile).removed == 2
        assert names(session, profile.id) == set()
        assert session.scalar(select(func.count()).select_from(Skill)) > 0


def test_job_sync_keeps_both_kinds_and_removes_stale():
    with TestSessionLocal() as session:
        job = make_job(session, ["Python", "Postgres", "PostgreSQL"], ["Docker", "PostgreSQL"])
        before = deepcopy((job.required_skills, job.nice_to_have_skills))
        assert sync_job_skills(session, job).added == 4
        assert sync_job_skills(session, job).added == 0
        assert (job.required_skills, job.nice_to_have_skills) == before
        rows = set(session.execute(select(Skill.canonical_name, JobSkill.requirement_kind).join(JobSkill)).tuples())
        assert rows == {("Python", "required"), ("PostgreSQL", "required"), ("PostgreSQL", "preferred"), ("Docker", "preferred")}
        job.required_skills = []
        job.nice_to_have_skills = ["Python"]
        result = sync_job_skills(session, job)
        assert (result.added, result.removed) == (1, 4)


def test_malformed_snapshot_quarantines_owner_and_keeps_links():
    with TestSessionLocal() as session:
        profile = make_profile(session, ["Python"])
        sync_profile_skills(session, profile)
        profile.skills = {"wrong": "shape"}
        assert sync_profile_skills(session, profile).quarantined
        assert names(session, profile.id) == {"Python"}
        job = make_job(session, ["Python"])
        sync_job_skills(session, job)
        job.nice_to_have_skills = None
        assert sync_job_skills(session, job).quarantined
        assert session.scalar(select(func.count()).select_from(JobSkill)) == 1


def test_pending_owner_flush_failure_is_not_dictionary_race():
    with TestSessionLocal() as session:
        session.add(Skill(canonical_name="bad", normalized_key=""))
        with pytest.raises(IntegrityError):
            resolve_skills(session, ["New valid skill"])
        assert not session.is_active
        session.rollback()
        assert session.scalar(select(func.count()).select_from(Skill)) == 0


@pytest.mark.parametrize("table,values", [
    (Skill, {"canonical_name":" ", "normalized_key":"key"}),
    (Skill, {"canonical_name":"x" * 101, "normalized_key":"key"}),
    (Skill, {"canonical_name":" x" + " " * 100, "normalized_key":"key"}),
    (Skill, {"canonical_name":"valid", "normalized_key":" "}),
    (Skill, {"canonical_name":"valid", "normalized_key":"x" * 513}),
    (SkillAlias, {"normalized_alias":"", "alias":"valid", "skill_id":1}),
    (SkillAlias, {"normalized_alias":"valid", "alias":"x" * 101, "skill_id":1}),
    (JobSkill, {"job_id":1, "skill_id":1, "requirement_kind":"inferred"}),
])
def test_database_constraints(table, values):
    with TestSessionLocal() as session:
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(table(**values))
                session.flush()
        assert session.scalar(select(func.count()).select_from(Skill)) == 0


def test_unique_foreign_keys_cascades_and_restricted_skill_delete():
    with TestSessionLocal() as session:
        profile = make_profile(session, ["Python"])
        job = make_job(session, ["Python"])
        sync_profile_skills(session, profile)
        sync_job_skills(session, job)
        python = session.scalar(select(Skill).where(Skill.normalized_key == "python"))
        for statement in [
            Skill.__table__.insert().values(canonical_name="Other", normalized_key="python"),
            SkillAlias.__table__.insert().values(normalized_alias="python", skill_id=python.id, alias="Other"),
            UserSkill.__table__.insert().values(user_profile_id=profile.id, skill_id=python.id),
            JobSkill.__table__.insert().values(job_id=job.id, skill_id=python.id, requirement_kind="required"),
            UserSkill.__table__.insert().values(user_profile_id=999, skill_id=python.id),
            delete(Skill).where(Skill.id == python.id),
        ]:
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.execute(statement)
        session.delete(profile)
        session.delete(job)
        session.flush()
        assert session.scalar(select(func.count()).select_from(UserSkill)) == 0
        assert session.scalar(select(func.count()).select_from(JobSkill)) == 0
        session.execute(delete(Skill).where(Skill.id == python.id))
        assert session.get(SkillAlias, "python") is None
