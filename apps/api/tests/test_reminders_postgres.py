"""Opt-in finite concurrency and migration checks in a disposable PG schema."""
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.database import Base
from app.models import Application, ApplicationReminder, Job, User
from app.schemas import ApplicationPatchIn
from app.services.applications import patch_application
from app.services import reminders as r
from app.services.telegram_notifications import Outcome
from test_reminder_migration import run
from app.workers.reminders import process_one

NOW = datetime(2026, 10, 10, 7, tzinfo=timezone.utc)


@pytest.fixture
def pg():
    if os.getenv("RUN_REMINDERS_POSTGRES") != "1":
        pytest.skip("set RUN_REMINDERS_POSTGRES=1 for real PostgreSQL checks")
    admin = create_engine(os.getenv("IDENTITY_TEST_DATABASE_URL", DATABASE_URL))
    schema = "reminders_test_" + uuid4().hex
    engine = None
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(admin.url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=5000"})
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions() as session:
            session.add(User(id=1, telegram_id=1))
            for app_id in (1, 2):
                session.add(Job(id=app_id, source="company_site", source_url=f"https://example.invalid/reminders/{app_id}", ingestion_method="manual", parsing_status="pending"))
            session.flush()
            for app_id in (1, 2):
                session.add(Application(id=app_id, user_id=1, job_id=app_id, next_action="Call"))
            session.commit()
            for app_id in (1, 2):
                app = session.get(Application, app_id)
                r.apply_schedule(session, app, {"next_action_remind_at": NOW, "next_action_timezone": "UTC"}, now=NOW - timedelta(minutes=2))
            session.commit()
        yield engine, sessions
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()


def test_two_consumers_claim_competing_rows_once_and_skip_locked(pg):
    _, sessions = pg
    # Hold application 1 to prove SKIP LOCKED, not merely sequential claiming.
    with sessions() as locked:
        locked.scalar(select(Application).where(Application.id == 1).with_for_update())
        with sessions() as second:
            claim2 = r.claim_due(second, NOW)
            assert claim2.application_id == 2
        locked.rollback()
    gate = Barrier(2)
    def claim():
        with sessions() as session:
            gate.wait(timeout=5)
            return r.claim_due(session, NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    claims = [value for value in results if value]
    assert len(claims) == 1 and claims[0].application_id == 1
    with sessions() as session:
        assert r.claim_due(session, NOW) is None
        assert session.get(ApplicationReminder, 1).attempt_count == 1
        assert r.preflight(session, claims[0], NOW).action == "Call"
        assert not session.in_transaction()
        r.complete(session, claims[0], Outcome("sent"), NOW)
        assert r.claim_due(session, NOW) is None


@pytest.mark.parametrize("changes", [
    {"next_action_remind_at": (NOW + timedelta(days=1)).isoformat(), "next_action_timezone": "UTC"},
    {"next_action_remind_at": None},
    {"next_action": None},
])
def test_edit_while_claimed_fences_preflight_and_completion(pg, changes):
    _, sessions = pg
    with sessions() as session:
        claim = r.claim_due(session, NOW)
    gate = Barrier(2)
    # The fake sender is paused at the preflight boundary until edit commits.
    def worker():
        gate.wait(timeout=5)
        with sessions() as session:
            assert r.preflight(session, claim, NOW) is None
            assert not r.complete(session, claim, Outcome("sent"), NOW)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        with sessions() as session:
            patch_application(session, 1, claim.application_id, ApplicationPatchIn(**changes))
        gate.wait(timeout=5)
        future.result(timeout=5)
    with sessions() as session:
        reminder = session.get(ApplicationReminder, claim.application_id)
        if changes.get("next_action_remind_at"):
            assert reminder.generation != claim.generation and reminder.delivery_state == "pending"
        else:
            assert reminder is None


def test_expired_claim_restart_is_terminal_and_same_past_schedule_noop(pg):
    _, sessions = pg
    with sessions() as session:
        claim = r.claim_due(session, NOW)
    with sessions() as session:
        assert r.recover_expired(session, NOW + timedelta(seconds=120)) == 1
        assert not r.complete(session, claim, Outcome("sent"), NOW + timedelta(seconds=121))
        app = session.scalar(select(Application).where(Application.id == claim.application_id).with_for_update())
        assert not r.apply_schedule(session, app, {"next_action_remind_at": NOW, "next_action_timezone": "UTC"}, now=NOW + timedelta(days=1))
        session.commit()
        value = session.get(ApplicationReminder, claim.application_id)
        assert value.generation == claim.generation and value.last_error_code == "DELIVERY_UNCERTAIN"


def test_postgres_migration_matches_model_and_refuses_data_loss(pg):
    engine, _ = pg
    with engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM application_reminders")
        run(connection, "downgrade")
        before = connection.exec_driver_sql("SELECT * FROM applications ORDER BY id").all()
        run(connection, "upgrade")
        assert connection.exec_driver_sql("SELECT * FROM applications ORDER BY id").all() == before
        assert connection.exec_driver_sql("SELECT count(*) FROM application_reminders").scalar_one() == 0
        from sqlalchemy import inspect
        columns = inspect(connection).get_columns("application_reminders")
        assert len(columns) == 11
        assert next(c for c in columns if c['name'] == 'remind_at')['type'].timezone
        connection.exec_driver_sql("INSERT INTO application_reminders (application_id,remind_at,timezone,generation,next_attempt_at) VALUES (1,now(),'UTC',gen_random_uuid(),now())")
        with pytest.raises(RuntimeError, match="reminder data"):
            run(connection, "downgrade")


def test_two_process_flows_claim_and_send_competing_rows_once(pg):
    _, sessions = pg
    sends = []
    gate = Barrier(2)
    class Sender:
        async def send(self, destination, application_id):
            sends.append(application_id)
            gate.wait(timeout=5)
            return Outcome("sent")
    sender = Sender()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: asyncio.run(process_one(sessions, sender, lambda: NOW)), range(2)))
    assert results == [True, True] and sorted(sends) == [1, 2]
    with sessions() as session:
        assert r.claim_due(session, NOW) is None
        assert [session.get(ApplicationReminder, app_id).attempt_count for app_id in (1,2)] == [1,1]


@pytest.mark.parametrize("changes", [
    {"next_action_remind_at": (NOW + timedelta(days=1)).isoformat(), "next_action_timezone": "UTC"},
    {"next_action_remind_at": None},
    {"next_action": None},
])
def test_edit_during_fake_provider_io_has_no_open_transaction_and_fences_success(pg, changes):
    _, sessions = pg
    sending, finish = Barrier(2), Barrier(2)
    class Sender:
        calls = 0
        async def send(self, destination, application_id):
            self.calls += 1
            assert application_id == 1
            sending.wait(timeout=5)
            finish.wait(timeout=5)
            return Outcome("sent")
    sender = Sender()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: asyncio.run(process_one(sessions, sender, lambda: NOW)))
        sending.wait(timeout=5)
        with sessions() as session:
            # NOWAIT proves the provider request holds neither application lock
            # nor its transaction; correctness does not depend on sleeps.
            session.scalar(select(Application).where(Application.id == 1).with_for_update(nowait=True))
            session.rollback()
            patch_application(session, 1, 1, ApplicationPatchIn(**changes))
        finish.wait(timeout=5)
        assert future.result(timeout=5)
    assert sender.calls == 1
    with sessions() as session:
        reminder = session.get(ApplicationReminder,1)
        if changes.get('next_action_remind_at'):
            assert reminder.delivery_state == 'pending' and reminder.attempt_count == 0
        else:
            assert reminder is None


def test_new_retry_lease_fences_previous_success(pg):
    _, sessions = pg
    with sessions() as session:
        old = r.claim_due(session,NOW)
        r.complete(session,old,Outcome('retry','CONNECT_FAILED'),NOW)
        # Remove the unrelated second schedule from this isolated fixture.
        session.delete(session.get(ApplicationReminder,2));session.commit()
        new = r.claim_due(session,NOW+timedelta(seconds=60))
        assert new.generation == old.generation and new.lease_token != old.lease_token
        assert not r.complete(session,old,Outcome('sent'),NOW+timedelta(seconds=61))
        assert r.preflight(session,new,NOW+timedelta(seconds=61)) is not None
        assert r.complete(session,new,Outcome('sent'),NOW+timedelta(seconds=61))
