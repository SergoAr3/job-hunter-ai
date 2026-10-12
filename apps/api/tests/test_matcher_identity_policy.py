from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import json

import pytest
from sqlalchemy import delete, event, inspect, select
from sqlalchemy.orm import Session

from app.models import Skill, SkillAlias
from app.services.matcher_identity_policy import (
    MATCHER_IDENTITY_POLICY_V22, POLICIES_BY_ALGORITHM, MatcherIdentityPolicy,
    canonicalize_matcher_skill, equivalent_matcher_skills,
    get_matcher_identity_policy, normalize_matcher_skill,
)
from app.services.matcher_registry_compatibility import validate_registry_compatibility
from app.services.matching_shadow_data import consistent_read
from app.services.skill_normalization import InvalidSkillValue
from app.services import skill_sync, skill_taxonomy
from conftest import TestSessionLocal, engine


@pytest.mark.parametrize("left,right,identity", [
    ("Postgres", "PostgreSQL", "postgresql"), ("JS", "JavaScript", "javascript"),
    ("REST", "REST API", "rest api"), ("NodeJS", "Node.js", "node.js"),
    ("ReactJS", "React", "react"), ("React.js", "React", "react"),
    ("REST-API", "REST API", "rest api"),
])
def test_positive_equivalence(left, right, identity):
    assert canonicalize_matcher_skill(left) == canonicalize_matcher_skill(right) == identity
    assert equivalent_matcher_skills(left, right)
    assert equivalent_matcher_skills(right, left)


@pytest.mark.parametrize("left,right", [
    ("C", "C#"), ("C", "C++"), ("C#", "C++"), ("R", "Rust"),
    ("Go", "Golang"), (".NET", "NET"), ("RESTful", "REST API"),
    ("Node-JS", "Node.js"), ("React js", "React.js"),
])
def test_negative_identity(left, right):
    assert not equivalent_matcher_skills(left, right)
    assert not equivalent_matcher_skills(right, left)


@pytest.mark.parametrize("value,key,identity", [
    (" POSTGRES ", "postgres", "postgresql"),
    (" REST\u00a0\u2003API\t", "rest api", "rest api"),
    ("ＮｏｄｅＪＳ", "nodejs", "node.js"), ("ＲＥＳＴ－ＡＰＩ", "rest-api", "rest api"),
    ("C++", "c++", "c++"), ("C#", "c#", "c#"), (".NET", ".net", ".net"),
    ("Node-JS", "node-js", "node-js"), ("Straße", "strasse", "strasse"),
    ("Cafe\u0301", "café", "café"), (" Django ", "django", "django"),
])
def test_normalization_and_unknown_exact_keys(value, key, identity):
    assert normalize_matcher_skill(value) == key
    assert canonicalize_matcher_skill(value) == identity


@pytest.mark.parametrize("value,reason", [
    (None, "not_string"), (4, "not_string"), ("", "blank"), (" \t", "blank"),
    ("PRIVATE\u200bVALUE", "forbidden_character"), ("x" * 101, "too_long"),
])
def test_invalid_values_have_only_fixed_error_codes(value, reason):
    with pytest.raises(InvalidSkillValue) as caught:
        canonicalize_matcher_skill(value)
    assert str(caught.value) == reason


def test_exact_version_binding_and_frozen_mapping():
    policy = get_matcher_identity_policy("job-match-v2.2")
    assert policy is MATCHER_IDENTITY_POLICY_V22
    assert policy.algorithm_version == "job-match-v2.2"
    assert policy.policy_version == "matcher-identity-v1"
    assert dict(policy.aliases) == {
        "postgres": "postgresql", "postgresql": "postgresql", "js": "javascript", "javascript": "javascript",
        "rest": "rest api", "rest api": "rest api", "rest-api": "rest api", "nodejs": "node.js", "node.js": "node.js",
        "reactjs": "react", "react.js": "react", "react": "react",
    }
    with pytest.raises(TypeError):
        policy.aliases["golang"] = "go"
    with pytest.raises(FrozenInstanceError):
        policy.policy_version = "changed"
    with pytest.raises(TypeError):
        POLICIES_BY_ALGORITHM["job-match-v2.2"] = None
    source = {"python": "python"}
    copied = MatcherIdentityPolicy("test", "test", source)
    source["python"] = "changed"
    assert copied.aliases["python"] == "python"


@pytest.mark.parametrize("version", ["latest", "job-match-v2.1", "PRIVATE_VERSION"])
def test_unknown_version_does_not_select_latest_or_touch_database(version):
    with pytest.raises(ValueError, match="^unsupported_matcher_algorithm$"):
        canonicalize_matcher_skill("Python", algorithm_version=version)
    with pytest.raises(ValueError, match="^unsupported_matcher_algorithm$"):
        validate_registry_compatibility(None, algorithm_version=version)


def seed_registry():
    # Explicitly non-default IDs demonstrate that the policy is not ID-bound.
    policy = MATCHER_IDENTITY_POLICY_V22
    ids = {key: 1000 + offset for offset, key in enumerate(sorted(set(policy.aliases.values())))}
    with TestSessionLocal() as session:
        session.add_all(Skill(id=identity, canonical_name=key, normalized_key=key) for key, identity in ids.items())
        session.flush()
        session.add_all(SkillAlias(normalized_alias=key, alias=key, skill_id=ids[canonical])
                        for key, canonical in policy.aliases.items())
        session.commit()
    return ids


def database_snapshot():
    with TestSessionLocal() as session:
        tables = set(inspect(session.connection()).get_table_names())
        return {name: deepcopy(session.execute(select(table)).all())
                for name, table in sorted(Skill.metadata.tables.items()) if name in tables}


@pytest.mark.parametrize("scenario,reason", [
    ("clean", None), ("missing", "policy_alias_missing"),
    ("wrong_target", "policy_alias_wrong_target"),
    ("canonical_missing", "canonical_alias_inconsistent"),
    ("canonical_wrong_owner", "canonical_alias_inconsistent"),
    ("schema_missing", "registry_schema_missing"),
])
def test_registry_compatibility_is_bounded_private_and_zero_write(monkeypatch, scenario, reason):
    ids = seed_registry()
    with TestSessionLocal() as session:
        if scenario == "missing":
            session.execute(delete(SkillAlias).where(SkillAlias.normalized_alias == "nodejs"))
        elif scenario == "wrong_target":
            private = Skill(canonical_name="PRIVATE_TARGET_MARKER", normalized_key="private_target_marker")
            session.add(private)
            session.flush()
            session.add(SkillAlias(normalized_alias=private.normalized_key, alias=private.canonical_name, skill_id=private.id))
            session.get(SkillAlias, "nodejs").skill_id = private.id
        elif scenario == "canonical_missing":
            session.execute(delete(SkillAlias).where(SkillAlias.normalized_alias == "node.js"))
        elif scenario == "canonical_wrong_owner":
            session.get(SkillAlias, "node.js").skill_id = ids["react"]
        session.commit()
    if scenario == "schema_missing":
        SkillAlias.__table__.drop(engine)
    before = database_snapshot()
    def forbidden(*args, **kwargs):
        pytest.fail("Compatibility checker attempted a write")
    for name in ("bootstrap_skills", "resolve_skills", "register_alias"):
        monkeypatch.setattr(skill_taxonomy, name, forbidden)
    for name in ("sync_profile_skills", "sync_job_skills"):
        monkeypatch.setattr(skill_sync, name, forbidden)
    monkeypatch.setattr(Session, "flush", forbidden)
    monkeypatch.setattr(Session, "commit", forbidden)
    statements = []
    def capture(conn, cursor, statement, params, context, executemany):
        assert statement.lstrip().split()[0].upper() in {"SELECT", "PRAGMA", "BEGIN"}
        statements.append((statement, len(params)))
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with consistent_read(TestSessionLocal) as session:
            result = validate_registry_compatibility(session)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert result.compatible is (reason is None)
    assert result.aliases_expected == 12
    assert result.aliases_checked == (0 if scenario == "schema_missing" else 12)
    if reason:
        assert reason in {issue.reason_code for issue in result.issues}
    assert len(result.issues) <= 24
    assert all(issue.policy_alias is None or issue.policy_alias in MATCHER_IDENTITY_POLICY_V22.aliases for issue in result.issues)
    assert "PRIVATE_TARGET_MARKER" not in json.dumps(asdict(result))
    assert "private_target_marker" not in json.dumps(asdict(result))
    assert database_snapshot() == before
    alias_reads = [(statement, count) for statement, count in statements if "FROM skill_aliases" in statement]
    assert len(alias_reads) == (0 if scenario == "schema_missing" else 1)
    assert all(count == 12 for _, count in alias_reads)


def test_extra_mutable_registry_alias_does_not_change_replay_or_policy():
    seed_registry()
    values = ["Go", "Golang", "PRIVATE_EXTRA_ALIAS", "ReactJS", "React.js", "Django"]
    before = [canonicalize_matcher_skill(value) for value in values]
    with TestSessionLocal() as session:
        go = skill_taxonomy.resolve_skills(session, ["Go"])
        owner = session.get(Skill, next(iter(go.skill_ids)))
        skill_taxonomy.register_alias(session, owner, "Golang")
        skill_taxonomy.register_alias(session, owner, "PRIVATE_EXTRA_ALIAS")
        session.commit()
    with consistent_read(TestSessionLocal) as session:
        assert validate_registry_compatibility(session).compatible
        assert session.get(SkillAlias, "golang").skill_id == session.get(SkillAlias, "go").skill_id
    assert [canonicalize_matcher_skill(value) for value in values] == before
    assert not equivalent_matcher_skills("Go", "Golang")
    assert not equivalent_matcher_skills("Go", "PRIVATE_EXTRA_ALIAS")
    assert equivalent_matcher_skills("ReactJS", "React.js")


def test_checker_disables_autoflush_and_preserves_caller_transaction(monkeypatch):
    seed_registry()
    with TestSessionLocal() as session:
        session.autoflush = True
        pending = Skill(canonical_name="PRIVATE_PENDING", normalized_key="private_pending")
        session.add(pending)
        def forbidden(*args, **kwargs):
            pytest.fail("Compatibility checker flushed pending owner state")
        monkeypatch.setattr(session, "flush", forbidden)
        assert validate_registry_compatibility(session).compatible
        assert pending in session.new and pending.id is None
        assert session.in_transaction() and session.is_active


def test_pure_replay_without_database(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pure identity decision attempted database access")
    monkeypatch.setattr(Session, "execute", forbidden)
    assert canonicalize_matcher_skill("NodeJS") == "node.js"
    assert canonicalize_matcher_skill("Django") == "django"
    assert not equivalent_matcher_skills("Go", "Golang")
