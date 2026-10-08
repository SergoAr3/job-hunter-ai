import json

import pytest
from sqlalchemy import event, func, select

from app.models import Job, JobSkill, Skill, SkillAlias, User, UserProfile, UserSkill
from app.services.skill_reconciliation import reconcile_skills
from app.services.skill_sync import sync_job_skills, sync_profile_skills
from app.services import skill_reconciliation
from conftest import TestSessionLocal, engine


def seed():
    with TestSessionLocal() as session:
        user = User(telegram_id=1)
        session.add(user)
        session.flush()
        session.add_all([
            UserProfile(user_id=user.id, target_roles=["Engineer"], skills=["Postgres", "PostgreSQL", "SecretTool", "", 42]),
            Job(source="company_site", source_url="https://example.com/1", required_skills=["Python"], nice_to_have_skills=["Python", "SecretTool"]),
        ])
        session.commit()


def counts():
    with TestSessionLocal() as session:
        return [session.scalar(select(func.count()).select_from(model)) for model in (Skill, SkillAlias, UserSkill, JobSkill)]


def test_dry_run_unknown_alias_equivalents_and_zero_writes():
    seed()
    writes = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "CREATE"}:
            writes.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        report = reconcile_skills(TestSessionLocal, dry_run=True)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not writes and counts() == [0, 0, 0, 0]
    assert report["owners_checked"] == report["owners_needing_sync"] == 2
    assert report["expected_associations"] == report["added"] == 5
    assert report["unknown_keys"] == 3 and report["invalid_values"] == 2
    assert "SecretTool" not in json.dumps(report)


def test_apply_idempotency_empty_removal_and_semantic_preservation():
    seed()
    first = reconcile_skills(TestSessionLocal, dry_run=False, batch_size=1)
    assert first["added"] == 5
    second = reconcile_skills(TestSessionLocal, dry_run=False)
    assert second["added"] == second["removed"] == second["owners_needing_sync"] == 0
    with TestSessionLocal() as session:
        profile = session.scalar(select(UserProfile))
        assert profile.skills == ["Postgres", "PostgreSQL", "SecretTool", "", 42]
        profile.skills = []
        session.commit()
    assert reconcile_skills(TestSessionLocal, entity="profiles", dry_run=False)["removed"] == 2


def test_invisible_snapshot_input_is_reported_without_creating_identities_or_losing_valid_links():
    snapshot = ["Python", "Python\u3164", "\u3164"]
    with TestSessionLocal() as session:
        user = User(telegram_id=1)
        session.add(user)
        session.flush()
        profile = UserProfile(user_id=user.id, target_roles=["Engineer"], skills=["Python"])
        session.add(profile)
        session.flush()
        sync_profile_skills(session, profile)
        profile.skills = snapshot
        session.commit()
        profile_id = profile.id
        before = counts()
    for dry_run in (True, False, True):
        report = reconcile_skills(TestSessionLocal, entity="profiles", dry_run=dry_run)
        # A list with invalid elements is incomplete, not a quarantined shape.
        assert report["invalid_values"] == 2 and report["quarantined_owners"] == 0
        assert report["expected_associations"] == report["current_associations"] == 1
        assert report["added"] == report["removed"] == 0
        assert counts() == before
        with TestSessionLocal() as session:
            assert session.get(UserProfile, profile_id).skills == snapshot
            assert set(session.scalars(select(Skill.normalized_key).join(UserSkill))) == {"python"}
            assert session.get(SkillAlias, "python\u1160") is None
            assert session.get(SkillAlias, "\u1160") is None


def test_limit_cursor_scope_and_resume():
    with TestSessionLocal() as session:
        for number in range(1, 5):
            user = User(telegram_id=number)
            session.add(user)
            session.flush()
            session.add(UserProfile(user_id=user.id, target_roles=["Engineer"], skills=[f"Tool{number}"]))
        session.commit()
    first = reconcile_skills(TestSessionLocal, entity="profiles", dry_run=False, batch_size=2, limit=2)
    assert first["owners_checked"] == 2 and counts()[2] == 2
    last = first["last_processed_id"]["profiles"]
    second = reconcile_skills(TestSessionLocal, entity="profiles", dry_run=False, after_id=last, limit=2)
    assert second["owners_checked"] == 2 and counts()[2] == 4
    assert reconcile_skills(TestSessionLocal, entity="jobs")["owners_checked"] == 0


def test_malformed_owner_quarantine_preserves_current_associations():
    seed()
    reconcile_skills(TestSessionLocal, dry_run=False)
    with TestSessionLocal() as session:
        profile = session.scalar(select(UserProfile))
        job = session.scalar(select(Job))
        profile.skills = {"not":"list"}
        job.nice_to_have_skills = "not-list"
        session.commit()
    before = counts()
    report = reconcile_skills(TestSessionLocal, dry_run=False)
    assert report["quarantined_owners"] == 2
    assert report["added"] == report["removed"] == 0 and counts() == before


def test_canonical_alias_drift_report():
    with TestSessionLocal() as session:
        session.add(Skill(canonical_name="Orphan canonical", normalized_key="orphan canonical"))
        session.commit()
    assert reconcile_skills(TestSessionLocal)["canonical_alias_violations"] == 1


def test_deleted_owner_still_consumes_bounded_selection_limit():
    with TestSessionLocal() as session:
        for number in (1, 2):
            user = User(telegram_id=number)
            session.add(user)
            session.flush()
            session.add(UserProfile(user_id=user.id, target_roles=["Engineer"], skills=["Python"]))
        session.commit()
    removed = False
    def delete_between_selection_and_load(conn, cursor, statement, parameters, context, executemany):
        nonlocal removed
        if not removed and "FROM user_profiles" in statement and "WHERE user_profiles.id =" in statement:
            removed = True
            # Emulate concurrent deletion after the batch ID selection. Use the
            # same connection so this deterministic test needs no timing sleeps.
            conn.exec_driver_sql("DELETE FROM user_profiles WHERE id=1")
    event.listen(engine, "before_cursor_execute", delete_between_selection_and_load)
    try:
        report = reconcile_skills(TestSessionLocal, entity="profiles", dry_run=True, batch_size=1, limit=1)
    finally:
        event.remove(engine, "before_cursor_execute", delete_between_selection_and_load)
    assert removed and report["owners_checked"] == 0
    assert report["last_processed_id"] == {"profiles":1}


@pytest.mark.parametrize("options", [{"batch_size":0}, {"batch_size":1001}, {"limit":0}, {"after_id":-1}, {"entity":"other"}, {"entity":"all", "after_id":1}])
def test_invalid_bounds(options):
    with pytest.raises(ValueError):
        reconcile_skills(TestSessionLocal, **options)


def test_cli_safe_output_and_explicit_apply(monkeypatch, capsys):
    from app.commands import reconcile_skills as cli
    monkeypatch.setattr(cli, "SessionLocal", TestSessionLocal)
    seed()
    assert cli.main(["--dry-run", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["owners_checked"] == 1
    assert counts() == [0, 0, 0, 0]
    assert cli.main(["--apply", "--entity", "profiles"]) == 0
    assert json.loads(capsys.readouterr().out)["added"] == 2
