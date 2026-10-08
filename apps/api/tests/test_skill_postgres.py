"""Real independent PostgreSQL sessions; disposable schemas only."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.database import Base
from app.models import Job, JobSkill, Skill, SkillAlias, User, UserProfile, UserSkill
from app.schemas import CVProfileDraftOut, UserProfilePutIn
from app.services import skill_taxonomy as taxonomy
from app.services.applications import run_job_ai_enrichment
from app.services.cv_import import apply_import, prepare_preview
from app.services.cv_import_state import CVImportError, ImportStore
from app.services.skill_sync import sync_profile_skills
from app.services.user_profiles import put_user_profile
from test_ai_enrichment import result


@pytest.fixture
def pg_sessions():
    if os.getenv("RUN_MATCHING_POSTGRES") != "1":
        pytest.skip("set RUN_MATCHING_POSTGRES=1 for disposable PostgreSQL schemas")
    admin = create_engine(os.getenv("IDENTITY_TEST_DATABASE_URL", DATABASE_URL), connect_args={"connect_timeout":3}, hide_parameters=True)
    schema = f"skills_test_{uuid4().hex}"
    engine = None
    try:
        with admin.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(admin.url, connect_args={"options":f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=20000"}, hide_parameters=True)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with sessions() as session:
            taxonomy.bootstrap_skills(session)
            session.commit()
        yield sessions
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()


def add_profiles(sessions, count=2):
    ids = []
    with sessions() as session:
        for number in range(count):
            user = User(telegram_id=number + 1)
            session.add(user)
            session.flush()
            profile = UserProfile(user_id=user.id, target_roles=["Old"], skills=[])
            session.add(profile)
            session.flush()
            ids.append((user.id, profile.id))
        session.commit()
    return ids


def names_for_profile(session, profile_id):
    return set(session.scalars(select(Skill.canonical_name).join(UserSkill).where(UserSkill.user_profile_id == profile_id)))


def race_lookup(monkeypatch, key):
    gate = Barrier(2)
    state = local()
    lookup = taxonomy._lookup
    def synchronized(session, value):
        found = lookup(session, value)
        if value == key and not getattr(state, "seen", False):
            state.seen = True
            gate.wait(timeout=10)
        return found
    monkeypatch.setattr(taxonomy, "_lookup", synchronized)


def test_concurrent_unknown_identity_savepoint_preserves_owners_and_session(pg_sessions, monkeypatch):
    owners = add_profiles(pg_sessions)
    race_lookup(monkeypatch, "new concurrent tool")
    def write(owner):
        with pg_sessions() as session:
            profile = session.get(UserProfile, owner[1])
            profile.skills = ["New Concurrent Tool"]
            profile.target_roles = [f"Writer {owner[0]}"]
            sync_profile_skills(session, profile)
            assert session.is_active
            assert session.scalar(select(func.count()).select_from(User)) == 2
            session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, owners))
    with pg_sessions() as session:
        skill = session.scalar(select(Skill).where(Skill.normalized_key == "new concurrent tool"))
        assert session.get(SkillAlias, "new concurrent tool").skill_id == skill.id
        assert session.scalar(select(func.count()).select_from(Skill).where(Skill.normalized_key == "new concurrent tool")) == 1
        assert session.scalar(select(func.count()).select_from(UserSkill)) == 2
        for uid, pid in owners:
            assert session.get(UserProfile, pid).target_roles == [f"Writer {uid}"]


def test_concurrent_bootstrapped_synonyms(pg_sessions):
    owners = add_profiles(pg_sessions)
    gate = Barrier(2)
    def write(value):
        owner, spelling = value
        with pg_sessions() as session:
            gate.wait(timeout=10)
            put_user_profile(session, owner[0], UserProfilePutIn(target_roles=["Engineer"], skills=[spelling]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, zip(owners, ["Postgres", "PostgreSQL"])))
    with pg_sessions() as session:
        assert len(set(session.scalars(select(UserSkill.skill_id)))) == 1
        assert session.scalar(select(func.count()).select_from(UserSkill)) == 2


def test_concurrent_first_bootstrap_is_deterministic(pg_sessions):
    # Remove only this disposable schema's seed to exercise first-live-write race.
    from sqlalchemy import delete
    with pg_sessions() as session:
        session.execute(delete(Skill))
        session.commit()
    gate = Barrier(2)
    def bootstrap(_):
        with pg_sessions() as session:
            gate.wait(timeout=10)
            taxonomy.bootstrap_skills(session)
            session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(bootstrap, [0, 1]))
    with pg_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Skill)) == len(taxonomy.BOOTSTRAP)
        for skill in session.scalars(select(Skill)):
            assert session.get(SkillAlias, skill.normalized_key).skill_id == skill.id


def test_concurrent_profile_put_same_owner_is_not_mixed(pg_sessions):
    uid, pid = add_profiles(pg_sessions, 1)[0]
    gate = Barrier(2)
    def write(skills):
        with pg_sessions() as session:
            session.get(UserProfile, pid)  # Deliberately preload a stale instance.
            gate.wait(timeout=10)
            put_user_profile(session, uid, UserProfilePutIn(target_roles=["Engineer"], skills=skills))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, [["Python", "SQL"], ["Git", "Redis"]]))
    with pg_sessions() as session:
        profile = session.get(UserProfile, pid)
        assert profile.skills in [["Python", "SQL"], ["Git", "Redis"]]
        assert names_for_profile(session, pid) == set(profile.skills)


def test_concurrent_first_profile_creation_uses_user_lock(pg_sessions):
    with pg_sessions() as session:
        user = User(telegram_id=1)
        session.add(user)
        session.commit()
        uid = user.id
    gate = Barrier(2)
    def write(skill):
        with pg_sessions() as session:
            gate.wait(timeout=10)
            return put_user_profile(session, uid, UserProfilePutIn(target_roles=["Engineer"], skills=[skill])).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(write, ["Python", "SQL"]))
    assert len(set(ids)) == 1
    with pg_sessions() as session:
        profile = session.get(UserProfile, ids[0])
        assert names_for_profile(session, profile.id) == set(profile.skills)


def test_cv_apply_vs_profile_put_same_owner(pg_sessions, tmp_path):
    uid, pid = add_profiles(pg_sessions, 1)[0]
    store = ImportStore(tmp_path / "previews")
    with pg_sessions() as session:
        preview = prepare_preview(session, uid, "session", CVProfileDraftOut(target_roles=["CV"], skills=["Python"]), store)
    gate = Barrier(2)
    def write(kind):
        with pg_sessions() as session:
            stale = session.get(UserProfile, pid)
            gate.wait(timeout=10)
            if kind == "put":
                put_user_profile(session, uid, UserProfilePutIn(target_roles=["Manual"], skills=["Git"]))
                return "put"
            try:
                apply_import(session, uid, "session", preview.token, store)
                return "applied"
            except CVImportError as error:
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ["cv", "put"]))
    assert outcomes[0] in {"applied", "cv_import_stale"} and outcomes[1] == "put"
    with pg_sessions() as session:
        profile = session.get(UserProfile, pid)
        assert profile.skills == ["Git"]
        assert names_for_profile(session, pid) == {"Git"}


def test_concurrent_job_enrichment_final_snapshot_is_not_mixed(pg_sessions):
    with pg_sessions() as session:
        job = Job(source="company_site", source_url="https://example.com/race", description="Skills")
        session.add(job)
        session.commit()
        job_id = job.id
    network_gate = Barrier(2)
    def write(skills):
        with pg_sessions() as session:
            job = session.get(Job, job_id)
            def enrich(_):
                assert not session.in_transaction()
                network_gate.wait(timeout=10)
                return result(required_skills=skills, nice_to_have_skills=[]), None
            run_job_ai_enrichment(session, job, SimpleNamespace(configured=True, enrich=enrich))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, [["Python", "SQL"], ["Git", "Redis"]]))
    with pg_sessions() as session:
        job = session.get(Job, job_id)
        assert job.ai_enrichment_status == "success"
        assert set(session.scalars(select(Skill.canonical_name).join(JobSkill).where(JobSkill.job_id == job_id))) == set(job.required_skills)


def test_concurrent_alias_conflict_savepoint_does_not_poison_outer(pg_sessions, monkeypatch):
    race_lookup(monkeypatch, "shared alias")
    def register(key):
        with pg_sessions() as session:
            skill = session.scalar(select(Skill).where(Skill.normalized_key == key))
            try:
                taxonomy.register_alias(session, skill, "Shared Alias")
                outcome = "registered"
            except taxonomy.SkillAliasConflict:
                outcome = "conflict"
            assert session.is_active
            session.add(User(telegram_id=1 if key == "python" else 2))
            session.commit()
            return outcome
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(register, ["python", "sql"]))
    assert sorted(outcomes) == ["conflict", "registered"]
    with pg_sessions() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 2
        assert session.scalar(select(func.count()).select_from(SkillAlias).where(SkillAlias.normalized_alias == "shared alias")) == 1
