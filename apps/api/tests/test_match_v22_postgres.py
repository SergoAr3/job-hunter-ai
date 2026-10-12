"""Finite PostgreSQL reads/writes in the existing disposable-schema fixture."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text

from app import config
from app.models import Application, ApplicationMatchSnapshot, ApplicationStatus, Job, UserProfile
from app.schemas import CVProfileDraftOut, UserProfilePutIn
from app.services.applications import run_job_ai_enrichment, set_application_status
from app.services.cv_import import apply_import, prepare_preview
from app.services.cv_import_state import ImportStore
from app.services.match_dispatch import calculate_current_match
from app.services.user_profiles import put_user_profile
from test_ai_enrichment import result as enrichment_result
from test_match_v22 import database_state
from test_matching_shadow import seed_pair
from test_skill_postgres import pg_sessions


@pytest.mark.parametrize("writer_kind", ["profile_put", "cv_apply", "job_enrichment"])
def test_concurrent_real_owner_write_refreshes_stale_semantics_without_false_drift(pg_sessions, writer_kind, tmp_path, caplog):
    aid, pid, jid = seed_pair(["Node.js"] if writer_kind == "job_enrichment" else ["Python"],
                             ["Python"] if writer_kind == "job_enrichment" else ["NodeJS"],
                             j_changes={"description": "Build services with Node.js"}, sessions=pg_sessions)
    ready, release, read_started = Event(), Event(), Event()
    store = ImportStore(tmp_path / "previews")
    with pg_sessions() as session:
        uid = session.get(UserProfile, pid).user_id
        preview = prepare_preview(session, uid, "session", CVProfileDraftOut(target_roles=["Python Backend Developer"], skills=["Node.js"]), store)

    def write():
        with pg_sessions() as session:
            def hold_final_commit(current):
                # Enrichment's initial pending commit must be allowed through.
                profile = current.get(UserProfile, pid)
                job = current.get(Job, jid)
                final = job.required_skills == ["NodeJS"] if writer_kind == "job_enrichment" else "Node.js" in profile.skills
                if final:
                    ready.set()
                    assert release.wait(10), "reader did not release writer"
            event.listen(session, "before_commit", hold_final_commit)
            if writer_kind == "profile_put":
                put_user_profile(session, uid, UserProfilePutIn(target_roles=["Python Backend Developer"], skills=["Node.js"]))
            elif writer_kind == "cv_apply":
                apply_import(session, uid, "session", preview.token, store)
            else:
                run_job_ai_enrichment(session, session.get(Job, jid), SimpleNamespace(
                    configured=True, enrich=lambda _: (enrichment_result(required_skills=["NodeJS"], nice_to_have_skills=[]), None)))

    with pg_sessions() as reader:
        # Intentionally preload the old ORM identity-map state before the writer.
        profile, job, application = reader.get(UserProfile, pid), reader.get(Job, jid), reader.get(Application, aid)
        connection = reader.connection()
        def mark_lock(conn, cursor, statement, parameters, context, executemany):
            if "FOR SHARE" in statement and ("FROM jobs" if writer_kind == "job_enrichment" else "FROM user_profiles") in statement:
                read_started.set()
        event.listen(connection, "before_cursor_execute", mark_lock)
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(write)
            try:
                assert ready.wait(10), "writer did not reach atomic owner+relation update"
                reading = pool.submit(calculate_current_match, reader, profile, job, application,
                                      algorithm_version="job-match-v2.2")
                assert read_started.wait(10), "reader did not attempt the owner lock"
                release.set()
                writer.result(timeout=10)
                match = reading.result(timeout=10)
            finally:
                release.set()
                event.remove(connection, "before_cursor_execute", mark_lock)
        assert match.algorithm_version == "job-match-v2.2"
        assert match.components["required_skills"].matched == ["NodeJS"]
        assert not [message for message in caplog.messages if "match_v22_fallback" in message]
        assert reader.is_active and reader.in_transaction()
        assert connection.exec_driver_sql("SHOW transaction_isolation").scalar_one() == "read committed"


def test_read_failure_savepoint_preserves_real_postgres_snapshot_write_transaction(pg_sessions, monkeypatch, caplog):
    aid, pid, jid = seed_pair(["Node.js"], ["NodeJS"], sessions=pg_sessions)
    monkeypatch.setattr(config, "MATCH_ALGORITHM_VERSION", "job-match-v2.2")
    def db_error(session, profile, job):
        session.execute(text("SELECT 1 / 0"))
    monkeypatch.setattr("app.services.match_dispatch.read_hybrid_skills", db_error)
    with pg_sessions() as session:
        uid = session.get(UserProfile, pid).user_id
        assert set_application_status(session, uid, aid, ApplicationStatus.APPLIED) is not None
        snapshot = session.scalar(select(ApplicationMatchSnapshot))
        assert snapshot.capture_status == "captured"
        assert snapshot.algorithm_version == "job-match-v2.1"
        assert snapshot.result_detail["components"]["required_skills"]["missing"] == ["NodeJS"]
        assert session.get(Application, aid).status == "applied"
        assert session.is_active


def test_postgres_candidate_does_not_write_or_finish_outer_transaction(pg_sessions, monkeypatch):
    aid, pid, jid = seed_pair(["PostgreSQL"], ["Postgres", "PostgreSQL", "Docker"], sessions=pg_sessions)
    with pg_sessions() as session:
        before = database_state(session)
        profile, job, application = session.get(UserProfile, pid), session.get(Job, jid), session.get(Application, aid)
        def forbidden(*args, **kwargs):
            raise AssertionError("matcher took ownership of caller unit of work")
        with monkeypatch.context() as patch:
            for name in ("flush", "commit", "rollback", "close"):
                patch.setattr(session, name, forbidden)
            match = calculate_current_match(session, profile, job, application, algorithm_version="job-match-v2.2")
        assert match.algorithm_version == "job-match-v2.2"
        assert match.components["required_skills"].score == 65
        assert database_state(session) == before
        assert session.in_transaction() and session.is_active
        application.note = "caller uncommitted change"
        session.flush()
        calculate_current_match(session, profile, job, application, algorithm_version="job-match-v2.2")
        session.rollback()
    with pg_sessions() as session:
        assert session.get(Application, aid).note == "PRIVATE_NOTE_MARKER"


def test_snapshot_inputs_refresh_with_same_evaluation(pg_sessions, monkeypatch):
    aid, pid, jid = seed_pair(["Python"], ["NodeJS"], sessions=pg_sessions)
    monkeypatch.setattr(config, "MATCH_ALGORITHM_VERSION", "job-match-v2.2")
    with pg_sessions() as session:
        stale = session.get(UserProfile, pid)
        with pg_sessions() as writer:
            put_user_profile(writer, stale.user_id, UserProfilePutIn(target_roles=["Python Backend Developer"], skills=["Node.js"]))
        assert stale.skills == ["Python"]
        set_application_status(session, stale.user_id, aid, ApplicationStatus.APPLIED)
        snapshot = session.scalar(select(ApplicationMatchSnapshot))
        assert snapshot.algorithm_version == "job-match-v2.2"
        assert snapshot.inputs["profile"]["skills"] == ["Node.js"]
        assert snapshot.result_detail["components"]["required_skills"]["matched"] == ["NodeJS"]
