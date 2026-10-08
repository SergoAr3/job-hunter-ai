from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models import Application, Job, JobSkill, ProfileExperienceFact, Skill, SkillAlias, User, UserProfile, UserSkill, WorkExperience
from app.schemas import CVProfileDraftOut, UserProfilePutIn
from app.services import applications, cv_import, user_profiles
from app.cv_import_schemas import ImportEdit
from app.services.cv_import_state import CVImportError, ImportStore
from app.services.job_matching import calculate_match
from app.services.skill_sync import sync_job_skills, sync_profile_skills
from app.services.skill_taxonomy import SkillAliasConflict
from app.work_experience_schema import WorkExperienceIn
from conftest import TestSessionLocal, client
from test_ai_enrichment import result
from test_job_matching import job as match_job, profile as match_profile
from test_discover_api import StubClient, vacancy
from app.services.discover import save_discovered_job, search_discover_jobs


def owner():
    with TestSessionLocal() as session:
        user = User(telegram_id=42)
        session.add(user)
        session.commit()
        return user.id


def taxonomy_count(session):
    return tuple(session.scalar(select(func.count()).select_from(model)) for model in (Skill, SkillAlias, UserSkill, JobSkill))


def test_profile_api_sync_and_pure_normalization():
    assert client.post("/profile/skills/normalize", json={"skills":["Postgres", "new TOOL"]}).json() == {"skills":["PostgreSQL", "new TOOL"]}
    with TestSessionLocal() as session:
        assert taxonomy_count(session) == (0, 0, 0, 0)
    uid = owner()
    for skills, expected in [(["Python", "Postgres"], {"Python", "PostgreSQL"}), (["Python", "Docker"], {"Python", "Docker"}), ([], set())]:
        response = client.put(f"/users/{uid}/profile", json={"target_roles":["Engineer"], "skills":skills})
        assert response.status_code == 200
        assert isinstance(response.json()["skills"], list)
        with TestSessionLocal() as session:
            assert set(session.scalars(select(Skill.canonical_name).join(UserSkill))) == expected


@pytest.mark.parametrize("failure", [SkillAliasConflict(), IntegrityError("INSERT secret", {"name":"PRIVATE_MARKER"}, Exception("PRIVATE_MARKER"))])
def test_profile_sync_failure_is_not_creation_retry_and_is_safe(monkeypatch, failure, caplog):
    uid = owner()
    assert client.put(f"/users/{uid}/profile", json={"target_roles":["Old"], "skills":["Python"]}).status_code == 200
    calls = []
    def fail(session, profile):
        calls.append(profile.id)
        raise failure
    monkeypatch.setattr(user_profiles, "sync_profile_skills", fail)
    response = client.put(f"/users/{uid}/profile", json={"target_roles":["New"], "skills":["Docker"]})
    assert response.status_code == 503 and len(calls) == 1
    assert "PRIVATE_MARKER" not in caplog.text + response.text
    with TestSessionLocal() as session:
        profile = session.scalar(select(UserProfile))
        assert profile.target_roles == ["Old"] and profile.skills == ["Python"]
        assert session.scalar(select(func.count()).select_from(UserSkill)) == 1


def test_profile_first_create_error_does_not_retry(monkeypatch):
    uid = owner()
    calls = []
    def fail(session, profile):
        calls.append(profile.id)
        raise IntegrityError("safe", {}, Exception("safe"))
    monkeypatch.setattr(user_profiles, "sync_profile_skills", fail)
    with TestSessionLocal() as session:
        with pytest.raises(IntegrityError):
            user_profiles.put_user_profile(session, uid, UserProfilePutIn(target_roles=["Engineer"], skills=["Python"]))
        assert session.is_active
        assert session.scalar(select(func.count()).select_from(UserProfile)) == 0
    assert len(calls) == 1


def test_cv_prepare_edit_cancel_are_pure_then_apply_syncs_final_union(tmp_path):
    uid = owner()
    store = ImportStore(tmp_path / "previews")
    with TestSessionLocal() as session:
        profile = UserProfile(user_id=uid, target_roles=["Engineer"], skills=["Git"])
        session.add(profile)
        session.commit()
        draft = CVProfileDraftOut(target_roles=["Engineer"], skills=["Python"])
        preview = cv_import.prepare_preview(session, uid, "session", draft, store)
        edited = cv_import.edit_import(uid, "session", ImportEdit.model_validate({"token":preview.token, "revision":1, "edit":{"kind":"profile", "value":{**preview.proposed.model_dump(mode="json"), "skills":["Postgres"]}}}), store)
        assert taxonomy_count(session) == (0, 0, 0, 0)
        store.cancel(edited.token, uid, "session")
        assert taxonomy_count(session) == (0, 0, 0, 0)
        preview = cv_import.prepare_preview(session, uid, "session", draft, store)
        cv_import.apply_import(session, uid, "session", preview.token, store)
        session.refresh(profile)
        assert profile.skills == ["Git", "Python"]
        assert set(session.scalars(select(Skill.canonical_name).join(UserSkill))) == {"Git", "Python"}


def test_cv_sync_failure_rolls_back_profile_work_facts_and_token_stays_terminal(monkeypatch, tmp_path):
    uid = owner()
    store = ImportStore(tmp_path / "previews")
    with TestSessionLocal() as session:
        profile = user_profiles.put_user_profile(session, uid, UserProfilePutIn(target_roles=["Old"], skills=["Git"]))
        session.add(WorkExperience(user_profile_id=profile.id, company="Old"))
        session.add(ProfileExperienceFact(user_profile_id=profile.id, text="Old fact"))
        session.commit()
        draft = CVProfileDraftOut(target_roles=["New"], skills=["Python"], suggested_work_experience=[WorkExperienceIn(company="New")], suggested_experience_facts=["New fact"])
        preview = cv_import.prepare_preview(session, uid, "session", draft, store)
        def fail(db, current):
            sync_profile_skills(db, current)
            raise SQLAlchemyError("safe failure")
        monkeypatch.setattr(cv_import, "sync_profile_skills", fail)
        with pytest.raises(CVImportError, match="cv_import_apply_failed"):
            cv_import.apply_import(session, uid, "session", preview.token, store)
        session.refresh(profile)
        assert profile.skills == ["Git"] and profile.target_roles == ["Old"]
        assert session.scalar(select(WorkExperience.company)) == "Old"
        assert session.scalar(select(ProfileExperienceFact.text)) == "Old fact"
        assert set(session.scalars(select(Skill.canonical_name).join(UserSkill))) == {"Git"}
        with pytest.raises(CVImportError, match="cv_import_used"):
            cv_import.apply_import(session, uid, "session", preview.token, store)


def test_locked_cv_snapshot_refreshes_stale_identity_map():
    uid = owner()
    with TestSessionLocal() as first:
        profile = user_profiles.put_user_profile(first, uid, UserProfilePutIn(target_roles=["Old"], skills=["Git"]))
        old = cv_import.snapshot(first, uid)[-1]
        with TestSessionLocal() as second:
            user_profiles.put_user_profile(second, uid, UserProfilePutIn(target_roles=["New"], skills=["Python"]))
        fresh, _, _, _, fingerprint = cv_import.snapshot(first, uid, lock=True)
        assert fresh is profile and fresh.skills == ["Python"] and fingerprint != old


def test_job_enrichment_uses_final_merge_arrays_network_has_no_transaction():
    with TestSessionLocal() as session:
        job = Job(source="company_site", source_url="https://example.com/ai", description="Requires skills", required_skills=["Postgres"], nice_to_have_skills=[])
        session.add(job)
        session.commit()
        assert taxonomy_count(session) == (0, 0, 0, 0)
        def enrich(vacancy):
            assert not session.in_transaction()
            return result(required_skills=["Python"], nice_to_have_skills=["PostgreSQL"]), None
        applications.run_job_ai_enrichment(session, job, SimpleNamespace(configured=True, enrich=enrich), merge_existing=True)
        assert job.required_skills == ["Postgres"] and job.nice_to_have_skills == ["PostgreSQL"]
        assert set(session.execute(select(Skill.canonical_name, JobSkill.requirement_kind).join(JobSkill)).tuples()) == {("PostgreSQL", "required"), ("PostgreSQL", "preferred")}
        applications.run_job_ai_enrichment(session, job, SimpleNamespace(configured=True, enrich=lambda _: (None, "timeout")))
        assert job.ai_enrichment_status == "failed"
        assert job.required_skills == ["Postgres"]
        assert session.scalar(select(func.count()).select_from(JobSkill)) == 2


def test_job_sync_error_rolls_back_and_logs_no_sql_parameters(monkeypatch, caplog):
    with TestSessionLocal() as session:
        job = Job(source="company_site", source_url="https://example.com/ai", description="Text", required_skills=["Git"])
        session.add(job)
        session.flush()
        sync_job_skills(session, job)
        session.commit()
        def fail(db, current):
            sync_job_skills(db, current)
            raise IntegrityError("INSERT PRIVATE_MARKER", {"skill":"PRIVATE_MARKER"}, Exception("PRIVATE_MARKER"))
        monkeypatch.setattr(applications, "sync_job_skills", fail)
        applications.run_job_ai_enrichment(session, job, SimpleNamespace(configured=True, enrich=lambda _: (result(required_skills=["Python"]), None)))
        assert job.required_skills == ["Git"] and job.ai_enrichment_status == "failed"
        assert set(session.scalars(select(Skill.canonical_name).join(JobSkill))) == {"Git"}
        assert "PRIVATE_MARKER" not in caplog.text


def test_discover_transient_is_pure_and_saved_job_uses_common_sync():
    uid = owner()
    source = StubClient([vacancy()])
    with TestSessionLocal() as session:
        session.add(UserProfile(user_id=uid, target_roles=["Engineer"], skills=["Python"]))
        session.commit()
        search_discover_jobs(session, uid, market_country="RU", query="Engineer", limit=10, offset=0, region_code=None, remote_only=False, client=source)
        assert taxonomy_count(session) == (0, 0, 0, 0)
        v = vacancy()
        saved = save_discovered_job(session, uid, source="trudvsem", source_scope=v.source_scope, external_id=v.external_id, client=source,
                                   ai_service=SimpleNamespace(configured=True, enrich=lambda _: (result(), None)))
        assert saved.job.ai_enrichment_status == "success"
        assert session.scalar(select(func.count()).select_from(JobSkill)) == 2


def test_matcher_structurally_identical_with_and_without_relations():
    uid = owner()
    with TestSessionLocal() as session:
        profile = match_profile(user_id=uid)
        job = match_job()
        session.add_all([profile, job])
        session.flush()
        app = Application(user_id=uid, job_id=job.id)
        session.add(app)
        session.flush()
        before = deepcopy(calculate_match(profile, job, app).model_dump())
        sync_profile_skills(session, profile)
        sync_job_skills(session, job)
        assert calculate_match(profile, job, app).model_dump() == before
