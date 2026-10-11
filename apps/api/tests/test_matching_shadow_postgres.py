"""Existing PostgreSQL integration opt-in and disposable-schema infrastructure."""
import pytest
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from app.models import UserProfile
from app.services.matching_shadow_data import compare_matching, consistent_read
from app.services.skill_sync import sync_profile_skills
from test_matching_shadow import seed_pair
from test_skill_postgres import pg_sessions


def test_repeatable_read_does_not_mix_concurrent_owner_commit(pg_sessions):
    ids = seed_pair(sessions=pg_sessions)
    engine = pg_sessions.kw["bind"]
    changed = False

    def commit_between_reads(conn, cursor, statement, params, context, executemany):
        nonlocal changed
        if changed or "FROM user_profiles" not in statement:
            return
        changed = True
        with pg_sessions() as writer:
            profile = writer.get(UserProfile, ids[1])
            profile.skills = ["SQL"]
            sync_profile_skills(writer, profile)
            writer.commit()

    event.listen(engine, "after_cursor_execute", commit_between_reads)
    try:
        report = compare_matching(pg_sessions, limit=1, pair_limit=1)
    finally:
        event.remove(engine, "after_cursor_execute", commit_between_reads)
    assert changed
    assert report["statuses"]["comparable"] == 1
    assert report["pairs"][0]["views"]["hybrid"]["skills"]["required"]["score"] == 100
    # A new batch/run sees the committed replacement, still internally consistent.
    after = compare_matching(pg_sessions, limit=1, pair_limit=1)
    assert after["statuses"]["comparable"] == 1
    assert after["pairs"][0]["views"]["hybrid"]["skills"]["required"]["score"] == 0


def test_postgres_transaction_is_read_only_and_resets_pool(pg_sessions):
    seed_pair(sessions=pg_sessions)
    with consistent_read(pg_sessions) as session:
        connection = session.connection()
        assert connection.exec_driver_sql("SHOW transaction_isolation").scalar_one() == "repeatable read"
        assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        with pytest.raises(DBAPIError) as caught:
            connection.exec_driver_sql("DELETE FROM skills")
        assert caught.value.orig.sqlstate == "25006"
    with pg_sessions() as session:
        assert session.connection().exec_driver_sql("SHOW transaction_read_only").scalar_one() == "off"
