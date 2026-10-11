from copy import deepcopy
from decimal import Decimal
import json

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import OperationalError

from app.commands import compare_matching as command
from app.models import (
    Application, ApplicationMatchSnapshot, ApplicationStatusHistory, Job, JobSkill,
    ProfileExperienceFact, Skill, SkillAlias, User, UserProfile, UserSkill,
)
from app.services import job_matching as legacy
from app.services.application_match_snapshots import prepare_match_snapshot
from app.services.matching_shadow import compare_pair
from app.services.matching_shadow_data import compare_matching, consistent_read
from app.services.skill_identity_reader import read_semantic_skills, read_skill_identities
from app.services.skill_sync import sync_job_skills, sync_profile_skills
from app.services import skill_taxonomy
from conftest import TestSessionLocal, engine
from test_job_matching import profile as profile_model, job as job_model


def seed_pair(user_skills=None, required=None, preferred=None, *, p_changes=None, j_changes=None,
              sync=True, sessions=TestSessionLocal):
    with sessions() as session:
        user = User(telegram_id=100 + (session.scalar(select(User.id).order_by(User.id.desc()).limit(1)) or 0))
        session.add(user)
        session.flush()
        p = profile_model(**{**{"user_id": user.id, "skills": user_skills if user_skills is not None else ["Python"]},
                             **(p_changes or {})})
        j = job_model(**{**{"id": None, "required_skills": required if required is not None else ["Python"],
                             "nice_to_have_skills": preferred if preferred is not None else [],
                             "source_url": f"https://example.com/shadow/{user.id}"}, **(j_changes or {})})
        session.add_all([p, j])
        session.flush()
        a = Application(user_id=user.id, job_id=j.id, note="PRIVATE_NOTE_MARKER")
        session.add(a)
        session.flush()
        if sync:
            sync_profile_skills(session, p)
            sync_job_skills(session, j)
        session.commit()
        return a.id, p.id, j.id


def comparison(ids):
    aid, pid, jid = ids
    with TestSessionLocal() as session:
        p, j = session.get(UserProfile, pid), session.get(Job, jid)
        keys = read_semantic_skills(p.skills).keys | read_semantic_skills(j.required_skills).keys | read_semantic_skills(j.nice_to_have_skills).keys
        identities = read_skill_identities(session, keys, {pid}, {jid})
        return compare_pair(p, j, identities)


@pytest.mark.parametrize("user_skill,job_skill,expected,group", [
    ("Postgres", "PostgreSQL", 100, "postgresql"),
    ("JS", "JavaScript", 100, "javascript"),
    ("REST", "REST API", 100, "rest"),
    ("NodeJS", "Node.js", 0, "node_js"),
    ("ReactJS", "React.js", 0, "react"),
    ("ReactJS", "React", 0, "react"),
    ("REST-API", "REST API", 0, "rest_api_dash"),
])
def test_alias_views(user_skill, job_skill, expected, group):
    result = comparison(seed_pair([user_skill], [job_skill]))
    assert result.status == "comparable"
    assert result.views["legacy"].skills["required"].component.score == expected
    for mode in ("hybrid", "direct"):
        skill = result.views[mode].skills["required"]
        assert skill.component.score == 100
        assert skill.component.matched == [job_skill]
        assert skill.component.missing == []
    assert result.alias_groups[group] == 1


@pytest.mark.parametrize("left,right", [
    ("C", "C#"), ("C", "C++"), ("C#", "C++"), ("R", "Rust"),
    ("Go", "Golang"), (".NET", "NET"), ("RESTful", "REST API"),
    ("Node-JS", "Node.js"), ("React js", "React.js"),
])
def test_negative_identities(left, right):
    result = comparison(seed_pair([left], [right]))
    for view in result.views.values():
        assert view.skills["required"].component.score == 0
        assert view.skills["required"].component.missing == [right]


@pytest.mark.parametrize("user_skills,required,legacy_score,hybrid_score,direct_score", [
    (["Python"], ["Python", "Python", "Missing"], 65, 65, 50),
    (["Python"], ["Python", "python", "Missing"], 65, 65, 50),
    (["Python"], ["Python", "python", "Postgres", "PostgreSQL"], 50, 50, 50),
    (["PostgreSQL"], ["Postgres", "PostgreSQL", "Docker"], 65, 65, 50),
    (["Python"], ["Python", "Postgres", "PostgreSQL"], 35, 35, 50),
    (["Node.js"], ["NodeJS", "Node.js", "Docker"], 35, 65, 50),
])
def test_occurrences_and_distinct_weighting(user_skills, required, legacy_score, hybrid_score, direct_score):
    result = comparison(seed_pair(user_skills, required))
    assert [result.views[mode].skills["required"].component.score for mode in ("legacy", "hybrid", "direct")] == [legacy_score, hybrid_score, direct_score]
    hybrid = result.views["hybrid"].skills["required"]
    assert hybrid.denominator == len(required)
    assert sorted(hybrid.matched_positions + hybrid.missing_positions) == list(range(len(required)))
    assert hybrid.component.matched == [required[index] for index in hybrid.matched_positions]
    assert hybrid.component.missing == [required[index] for index in hybrid.missing_positions]
    assert result.views["direct"].skills["required"].denominator <= hybrid.denominator


def test_profile_multiplicity_buckets_and_duplicate_diagnostics():
    ids = seed_pair(["Python", "python", "Postgres", "PostgreSQL"],
                    ["Python", "python", "Postgres", "PostgreSQL"], ["PostgreSQL"])
    result = comparison(ids)
    assert result.duplicates["required"] == {"same_key_collapse": 1, "alias_collapse": 1}
    assert result.duplicates["profile"] == {"same_key_collapse": 1, "alias_collapse": 1}
    assert result.duplicates["buckets"]["required_preferred_overlap"] == 1
    for view in result.views.values():
        assert view.skills["required"].component.score == 100
        assert view.skills["preferred"].component.score == 100
        assert view.skills["required"].component.weight == 30
        assert view.skills["preferred"].component.weight == 5
    with TestSessionLocal() as session:
        p = session.get(UserProfile, ids[1])
        p.skills = ["Python", "PostgreSQL"]
        sync_profile_skills(session, p)
        session.commit()
    reduced = comparison(ids)
    assert [v.score for v in result.views.values()] == [v.score for v in reduced.views.values()]


@pytest.mark.parametrize("user_skills,required,preferred,state,status", [
    ([], ["Python"], ["Python"], "success", "evidence_unavailable"),
    (["Python"], [], [], "success", "evidence_unavailable"),
    (["Python"], [], ["Python"], "success", "comparable"),
    (["Python"], ["Python"], [], "success", "comparable"),
    (["Python"], ["Python"], ["Python"], "pending", "evidence_unavailable"),
    (["Python"], ["Python"], ["Python"], "failed", "evidence_unavailable"),
])
def test_availability(user_skills, required, preferred, state, status):
    ids = seed_pair(user_skills, required, preferred, j_changes={"ai_enrichment_status": state})
    result = comparison(ids)
    assert result.status == status
    for kind in ("required", "preferred"):
        expected = result.views["legacy"].skills[kind].component
        for mode in ("hybrid", "direct"):
            actual = result.views[mode].skills[kind].component
            assert actual.score == expected.score
            assert actual.status == expected.status
    report = compare_matching(TestSessionLocal, pair_limit=1)
    if status != "comparable":
        assert report["total_score_histograms"]["hybrid"] == {}
        assert all(delta is None for delta in report["pairs"][0]["score_deltas"].values())


@pytest.mark.parametrize("corruption", ["empty", "user_missing", "user_extra", "job_missing", "job_extra", "wrong_bucket", "equal_counts"])
def test_set_drift_excludes_candidates(corruption):
    ids = seed_pair(["Python"], ["Python"], ["SQL"])
    with TestSessionLocal() as session:
        python = session.scalar(select(Skill.id).where(Skill.normalized_key == "python"))
        sql = session.scalar(select(Skill.id).where(Skill.normalized_key == "sql"))
        if corruption in {"empty", "user_missing", "equal_counts"}:
            session.execute(delete(UserSkill).where(UserSkill.user_profile_id == ids[1]))
        if corruption in {"user_extra", "equal_counts"}:
            session.add(UserSkill(user_profile_id=ids[1], skill_id=sql))
        if corruption in {"empty", "job_missing", "wrong_bucket"}:
            session.execute(delete(JobSkill).where(JobSkill.job_id == ids[2], JobSkill.requirement_kind == "required"))
        if corruption == "job_extra":
            session.add(JobSkill(job_id=ids[2], skill_id=sql, requirement_kind="required"))
        if corruption == "wrong_bucket":
            session.add(JobSkill(job_id=ids[2], skill_id=python, requirement_kind="preferred"))
        session.commit()
    result = comparison(ids)
    assert result.status == "drift"
    assert set(result.views) == {"legacy"}
    report = compare_matching(TestSessionLocal, pair_limit=1)
    assert report["statuses"]["drift"] == 1
    assert all(delta is None for delta in report["pairs"][0]["score_deltas"].values())
    assert all(not histogram for histogram in report["total_score_histograms"].values())
    if corruption == "equal_counts":
        assert result.consistency["profile"] == {"expected": 1, "actual": 1, "missing": 1, "extra": 1}


@pytest.mark.parametrize("value,status", [("", "invalid"), (4, "invalid"), ("Python\u200b", "invalid"),
                                          ("x" * 101, "invalid"), ({"not": "list"}, "quarantined")])
@pytest.mark.parametrize("field", ["profile", "required", "preferred"])
def test_invalid_and_quarantine(value, status, field):
    ids = seed_pair()
    with TestSessionLocal() as session:
        values = value if isinstance(value, dict) else [value]
        if field == "profile":
            session.get(UserProfile, ids[1]).skills = values
        else:
            setattr(session.get(Job, ids[2]), "required_skills" if field == "required" else "nice_to_have_skills", values)
        session.commit()
    report = compare_matching(TestSessionLocal, pair_limit=1)
    diagnostic = report["pairs"][0]
    assert diagnostic["status"] == status
    assert diagnostic["views"] == {}
    assert all(delta is None for delta in diagnostic["score_deltas"].values())


def test_unresolved_does_not_seed_or_guess_identity():
    ids = seed_pair(sync=False)
    report = compare_matching(TestSessionLocal, pair_limit=1)
    assert report["statuses"]["unresolved"] == 1
    assert report["pairs"][0]["unresolved_keys"] == 1
    with TestSessionLocal() as session:
        assert list(session.scalars(select(Skill.id))) == []
        assert list(session.scalars(select(SkillAlias.normalized_alias))) == []


def test_malformed_non_skill_json_is_dirty_data():
    ids = seed_pair()
    with TestSessionLocal() as session:
        session.get(UserProfile, ids[1]).languages = ["invalid_language_object"]
        session.commit()
    result = comparison(ids)
    assert result.status == "invalid"
    assert result.reasons == ["legacy_input_invalid"]


def test_scorer_programmer_error_is_not_a_dirty_pair(monkeypatch, capsys):
    seed_pair()
    monkeypatch.setattr(command, "SessionLocal", TestSessionLocal)
    def internal_bug(*args):
        raise TypeError("PRIVATE_INTERNAL_MARKER")
    monkeypatch.setattr(legacy, "_calculate_match_core", internal_bug)
    assert command.main([]) == 1
    output = capsys.readouterr()
    assert "PRIVATE_INTERNAL_MARKER" not in output.err
    assert json.loads(output.err)["exception_class"] == "TypeError"


def test_alias_namespace_corruption_is_not_trusted():
    ids = seed_pair(["NodeJS"], ["Node.js"])
    with TestSessionLocal() as session:
        python = session.scalar(select(Skill.id).where(Skill.normalized_key == "python"))
        session.get(SkillAlias, "nodejs").skill_id = python
        session.commit()
    assert comparison(ids).status == "drift"


@pytest.mark.parametrize("value", [None, "Python", 123, {"bad": "shape"}])
def test_non_list_snapshots_quarantine_without_normalizing(value):
    ids = seed_pair()
    with TestSessionLocal() as session:
        session.get(Job, ids[2]).nice_to_have_skills = value
        session.commit()
    result = comparison(ids)
    assert result.status == "quarantined"
    assert result.views == {}


def test_private_alias_diagnostics_are_counts_only():
    ids = seed_pair(["PRIVATE_CANONICAL"], ["PRIVATE_CANONICAL"])
    with TestSessionLocal() as session:
        skill = session.scalar(select(Skill).where(Skill.normalized_key == "private_canonical"))
        skill_taxonomy.register_alias(session, skill, "PRIVATE_ALIAS")
        p = session.get(UserProfile, ids[1])
        p.skills = ["PRIVATE_ALIAS"]
        sync_profile_skills(session, p)
        session.commit()
    result = comparison(ids)
    assert result.private_alias_matches == 1
    assert result.alias_groups == {}
    output = json.dumps(compare_matching(TestSessionLocal, pair_limit=1))
    assert "PRIVATE_CANONICAL" not in output and "PRIVATE_ALIAS" not in output


def test_private_alias_to_public_identity_is_not_a_seed_alias():
    ids = seed_pair(["Node.js"], ["Node.js"])
    with TestSessionLocal() as session:
        skill = session.scalar(select(Skill).where(Skill.normalized_key == "node.js"))
        skill_taxonomy.register_alias(session, skill, "PRIVATE_NODE_ALIAS")
        p = session.get(UserProfile, ids[1])
        p.skills = ["PRIVATE_NODE_ALIAS"]
        sync_profile_skills(session, p)
        session.commit()
    result = comparison(ids)
    assert result.alias_groups == {}
    assert result.private_alias_matches == 1


def test_unknown_association_bucket_is_drift():
    ids = seed_pair()
    with TestSessionLocal() as session:
        p, j = session.get(UserProfile, ids[1]), session.get(Job, ids[2])
        identities = read_skill_identities(session, {"python"}, {p.id}, {j.id})
        # Simulate manually corrupted derived rows without disabling DB checks.
        identities.jobs[j.id]["unsupported"] = set(identities.jobs[j.id]["required"])
        result = compare_pair(p, j, identities)
    assert result.status == "drift"
    assert "association_bucket_invalid" in result.reasons


def test_unicode_identity_and_whitespace_preserve_original_labels():
    ids = seed_pair(["C++", "REST API"], ["Ｃ＋＋", " REST\u00a0 API\t"])
    result = comparison(ids)
    for view in result.views.values():
        assert view.skills["required"].component.matched == ["Ｃ＋＋", " REST\u00a0 API\t"]


@pytest.mark.parametrize("count", range(9))
@pytest.mark.parametrize("scenario", ["full", "limited", "conflict", "role_unknown"])
def test_counterfactual_math_matches_real_core(count, scenario):
    required = ["NodeJS"] + [f"Tool {i}" for i in range(7)]
    skills = ["Node.js"] + required[1:count] if count else ["Unrelated"]
    changes = {"salary_max": Decimal("1000"), "salary_min": Decimal("500")} if scenario == "conflict" else {}
    p_changes = {}
    if scenario == "limited":
        changes.update(seniority="unknown", language_requirements=[], workplace_type="unknown", salary_min=None, salary_max=None)
    if scenario == "role_unknown":
        p_changes["target_roles"] = []
    ids = seed_pair(skills, required, ["NodeJS"], p_changes=p_changes, j_changes=changes)
    with TestSessionLocal() as session:
        p, j = session.get(UserProfile, ids[1]), session.get(Job, ids[2])
        keys = read_semantic_skills(p.skills).keys | read_semantic_skills(j.required_skills).keys | read_semantic_skills(j.nice_to_have_skills).keys
        identities = read_skill_identities(session, keys, {p.id}, {j.id})
        result = compare_pair(p, j, identities)
        for mode in ("hybrid", "direct"):
            # Reference: give the actual unchanged scorer identity strings, so
            # its own full aggregation/availability/reasons are the oracle.
            p_ref = UserProfile(**{c.name: deepcopy(getattr(p, c.name)) for c in UserProfile.__table__.columns})
            j_ref = Job(**{c.name: deepcopy(getattr(j, c.name)) for c in Job.__table__.columns})
            p_ref.skills = [str(identities.aliases[item.key]) for item in read_semantic_skills(p.skills).occurrences]
            for name in ("required_skills", "nice_to_have_skills"):
                values = [str(identities.aliases[item.key]) for item in read_semantic_skills(getattr(j, name)).occurrences]
                setattr(j_ref, name, list(dict.fromkeys(values)) if mode == "direct" else values)
            actual = legacy.calculate_match(p_ref, j_ref, session.get(Application, ids[0]))
            candidate = result.views[mode]
            assert (candidate.score, candidate.verdict, candidate.coverage, candidate.confidence, candidate.recommendation) == (
                actual.score, actual.verdict, actual.coverage, actual.confidence, actual.recommendation.code)
            assert candidate.primary_reason_code == (actual.recommendation.primary_reason.code if actual.recommendation.primary_reason else None)
        assert result.views["hybrid"].skills["required"].component.score == legacy._round_percent(count, 8)


@pytest.mark.parametrize("salary,score", [(Decimal("3000"), 75), (Decimal("1000"), 50)])
def test_coverage_40_and_verdict_boundaries(salary, score):
    ids = seed_pair(["Python"], [], [], p_changes={"languages": [{"language": "English", "level": "A1"}]},
                    j_changes={"seniority": "unknown", "workplace_type": "unknown", "salary_min": salary, "salary_max": salary})
    result = comparison(ids)
    for view in result.views.values():
        assert view.coverage == 40
        assert view.score == score
        assert view.verdict == ("high" if score == 75 else "medium")


def test_insufficient_core_and_salary_conflict_priority():
    ids = seed_pair(["Python"], [], [], p_changes={"target_roles": []})
    result = comparison(ids)
    for view in result.views.values():
        assert view.score is None and view.verdict == "insufficient_data"
    conflict = comparison(seed_pair(["Node.js"], ["NodeJS"],
                                    j_changes={"salary_min": Decimal("500"), "salary_max": Decimal("1000")}))
    for view in conflict.views.values():
        assert view.primary_reason_code == "salary_below_minimum"
    assert conflict.views["hybrid"].verdict == "high"
    assert conflict.views["hybrid"].recommendation == "apply_with_risks"


def db_snapshot():
    with TestSessionLocal() as session:
        return {name: deepcopy(session.execute(select(table)).all())
                for name, table in sorted(User.metadata.tables.items())}


def test_zero_writes_privacy_and_no_live_resolver(monkeypatch):
    ids = seed_pair(["PRIVATE_TOOL_MARKER"], ["PRIVATE_TOOL_MARKER"],
                    j_changes={"description": "PRIVATE_DESCRIPTION_MARKER"})
    with TestSessionLocal() as session:
        p, j, a = session.get(UserProfile, ids[1]), session.get(Job, ids[2]), session.get(Application, ids[0])
        history = ApplicationStatusHistory(application_id=a.id, status="applied")
        session.add(history)
        session.add(ProfileExperienceFact(user_profile_id=p.id, text="PRIVATE_CV_MARKER"))
        session.flush()
        session.add(ApplicationMatchSnapshot(application_id=a.id, trigger_status_history_id=history.id,
                                             **prepare_match_snapshot(p, j, a)))
        session.commit()
    before = db_snapshot()
    def forbidden(*args, **kwargs):
        pytest.fail("A read-only comparison attempted a write helper")
    monkeypatch.setattr(skill_taxonomy, "resolve_skills", forbidden)
    monkeypatch.setattr(skill_taxonomy, "bootstrap_skills", forbidden)
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Session, "flush", forbidden)
    monkeypatch.setattr(Session, "commit", forbidden)
    statements = []
    def capture(conn, cursor, statement, params, context, executemany):
        statements.append(statement)
        assert statement.lstrip().split()[0].upper() in {"SELECT", "PRAGMA", "BEGIN"}
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for pair_limit in (0, 1):
            output = json.dumps(compare_matching(TestSessionLocal, pair_limit=pair_limit))
            for private in ("PRIVATE_TOOL_MARKER", "PRIVATE_CV_MARKER", "PRIVATE_DESCRIPTION_MARKER", "PRIVATE_NOTE_MARKER"):
                assert private not in output
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert db_snapshot() == before
    assert not any("description" in statement or "profile_experience_facts" in statement or "application_match_snapshots" in statement for statement in statements)


def test_sqlite_transaction_blocks_writes_and_restores_connection():
    seed_pair()
    with consistent_read(TestSessionLocal) as session:
        assert session.connection().connection.driver_connection.in_transaction
        with pytest.raises(OperationalError):
            session.connection().exec_driver_sql("DELETE FROM skills")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 0


def test_existing_transaction_cannot_bypass_consistent_read():
    with TestSessionLocal() as session:
        session.execute(select(Skill.id))
        with pytest.raises(ValueError, match="shadow_requires_clean_session"):
            with consistent_read(lambda: session):
                pytest.fail("Existing mutable transaction accepted")


def test_sqlite_read_only_setting_restored_when_begin_fails():
    def fail_begin(conn, cursor, statement, params, context, executemany):
        if statement == "BEGIN":
            raise RuntimeError("simulated_begin_failure")
    event.listen(engine, "before_cursor_execute", fail_begin)
    try:
        with pytest.raises(RuntimeError, match="simulated_begin_failure"):
            with consistent_read(TestSessionLocal):
                pytest.fail("Failed BEGIN accepted")
    finally:
        event.remove(engine, "before_cursor_execute", fail_begin)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 0


def test_missing_profile_consumes_limit_and_cursor_advances():
    missing = seed_pair()
    valid = seed_pair()
    with TestSessionLocal() as session:
        session.execute(delete(UserProfile).where(UserProfile.id == missing[1]))
        session.commit()
    first = compare_matching(TestSessionLocal, limit=1)
    assert first["statuses"]["quarantined"] == 1
    assert first["last_processed_id"] == missing[0]
    resumed = compare_matching(TestSessionLocal, after_id=first["last_processed_id"], limit=1)
    assert resumed["statuses"]["comparable"] == 1
    assert resumed["last_processed_id"] == valid[0]


def test_report_separates_identity_and_reweighting():
    seed_pair(["Node.js"], ["NodeJS", "Node.js", "Docker"])
    report = compare_matching(TestSessionLocal, pair_limit=1)
    assert report["skill_score_delta_histograms"]["required"]["legacy_to_hybrid"] == {"30": 1}
    assert report["skill_score_delta_histograms"]["required"]["hybrid_to_direct"] == {"-15": 1}
    assert report["duplicate_collapse_counts"]["required.alias_collapse"] == 1
    assert report["pairs"][0]["views"]["direct"]["skills"]["required"]["denominator"] == 2


@pytest.mark.parametrize("arguments", [
    ["--limit", "0"], ["--limit", "-1"], ["--limit", "10001"],
    ["--batch-size", "0"], ["--batch-size", "1001"], ["--after-id", "-1"],
    ["--pair-limit", "0"], ["--pair-limit", "1001"],
])
def test_cli_invalid_options_do_not_execute(monkeypatch, capsys, arguments):
    def forbidden_execution(*args, **kwargs):
        pytest.fail("Invalid options reached comparator execution")
    monkeypatch.setattr(command, "compare_matching", forbidden_execution)
    assert command.main(arguments) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error": "invalid_shadow_options"}


def test_cli_internal_value_error_is_safe_execution_failure(monkeypatch, capsys):
    def internal_failure(*args, **kwargs):
        raise ValueError("PRIVATE_EXECUTION_SQL_PARAMS_LABEL_MARKER")
    monkeypatch.setattr(command, "compare_matching", internal_failure)
    assert command.main(["--limit", "1"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error": "shadow_comparison_failed", "exception_class": "ValueError"}
    assert "invalid_shadow_options" not in output.err
    assert "PRIVATE_EXECUTION_SQL_PARAMS_LABEL_MARKER" not in output.err


def test_cli_limits_resume_and_safe_errors(monkeypatch, capsys):
    for _ in range(3):
        seed_pair()
    monkeypatch.setattr(command, "SessionLocal", TestSessionLocal)
    assert command.main(["--limit", "2", "--batch-size", "1", "--per-pair", "--pair-limit", "1"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["pairs_checked"] == 2 and len(first["pairs"]) == 1
    assert first["pair_diagnostics_truncated"]
    assert command.main(["--after-id", str(first["last_processed_id"])]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["pairs_checked"] == 1 and "pairs" not in resumed
    with pytest.raises(SystemExit):
        command.main(["--apply"])
    capsys.readouterr()
    assert command.main(["--limit", "0"]) == 2
    assert "invalid_shadow_options" in capsys.readouterr().err
    def fail(**kwargs):
        raise RuntimeError("PRIVATE_SQL_PARAMS_MARKER")
    monkeypatch.setattr(command, "compare_matching", lambda *a, **kw: fail())
    assert command.main([]) == 1
    output = capsys.readouterr()
    assert "PRIVATE_SQL_PARAMS_MARKER" not in output.err
    assert json.loads(output.err)["exception_class"] == "RuntimeError"
    def sql_failure(*args, **kwargs):
        raise OperationalError("SELECT PRIVATE_SQL_TEXT", {"label": "PRIVATE_BOUND_PARAMETER"}, Exception("PRIVATE_DRIVER_MESSAGE"))
    monkeypatch.setattr(command, "compare_matching", sql_failure)
    assert command.main([]) == 1
    error_output = capsys.readouterr().err
    assert json.loads(error_output)["exception_class"] == "OperationalError"
    assert all(marker not in error_output for marker in ("PRIVATE_SQL_TEXT", "PRIVATE_BOUND_PARAMETER", "PRIVATE_DRIVER_MESSAGE"))


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": 10001}, {"batch_size": 1001},
                                    {"batch_size": 0}, {"after_id": -1}, {"pair_limit": 1001}])
def test_invalid_bounds_rejected_before_database_read(options):
    def no_database():
        pytest.fail("Invalid options opened a database session")
    with pytest.raises(ValueError, match="invalid_shadow_options"):
        compare_matching(no_database, **options)


def test_schema_missing_and_missing_owner(monkeypatch):
    ids = seed_pair()
    with TestSessionLocal() as session:
        session.execute(delete(UserProfile).where(UserProfile.id == ids[1]))
        session.commit()
    assert compare_matching(TestSessionLocal)["statuses"]["quarantined"] == 1
    seed_pair()
    SkillAlias.__table__.drop(engine)
    report = compare_matching(TestSessionLocal, pair_limit=2)
    assert not report["schema_ready"]
    assert report["statuses"]["schema_missing"] == 1
    monkeypatch.setattr(command, "SessionLocal", TestSessionLocal)
    assert command.main([]) == 1


def test_batch_queries_do_not_scale_per_occurrence():
    for _ in range(5):
        seed_pair(["Python"] * 20, ["Python"] * 20)
    alias_reads = []
    def capture(conn, cursor, statement, params, context, executemany):
        if "FROM skill_aliases" in statement:
            alias_reads.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        report = compare_matching(TestSessionLocal, batch_size=5, limit=5)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(alias_reads) == 1
    assert report["skills"]["required"]["hybrid"]["matched"] == 100
    assert report["skills"]["required"]["direct"]["matched"] == 5
