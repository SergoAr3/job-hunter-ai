"""Real independent PostgreSQL connections in disposable schemas; bounded waits."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, get_ident
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import event, func, insert, select
from sqlalchemy.exc import IntegrityError

from app.models import DiscoverInventoryGeneration as Generation, DiscoverInventoryKey as Key, DiscoverInventoryItem as Item
from app.services.inventory_types import InventoryGenerationConflict, InventoryGenerationExpired
from app.services.query_inventory import InventoryRepository
from test_discover_feed_postgres import pg_sessions
from test_query_inventory import (
    NOW, IDENTITY, INVALID_JSON_CASES, assert_payload_boundaries, assert_payload_rejection,
    assert_unicode_roundtrip, building, candidate, complete, publish,
)


@pytest.mark.parametrize("case", INVALID_JSON_CASES)
def test_jsonb_incompatible_payload_rejected_before_sql(pg_sessions, case):
    assert_payload_rejection(pg_sessions, case)


def test_jsonb_valid_unicode_roundtrip_and_exact_bytes(pg_sessions):
    assert_unicode_roundtrip(pg_sessions)


def test_jsonb_payload_exact_item_and_generation_boundaries(pg_sessions):
    assert_payload_boundaries(pg_sessions)


def test_concurrent_first_resolve_recovers_unique_key_insert_savepoint(pg_sessions):
    engine = pg_sessions.kw["bind"]
    barrier = Barrier(2)
    def before(conn, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO discover_inventory_keys"):
            barrier.wait(5)
    event.listen(engine, "before_cursor_execute", before)
    def resolve():
        with pg_sessions() as session:
            result = InventoryRepository(session).resolve_generation(IDENTITY, NOW)
            assert not session.in_transaction()
            return result
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(resolve) for _ in range(2)]
            results = [future.result(10) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", before)
    assert results[0].generation_id == results[1].generation_id
    assert sorted(result.outcome for result in results) == ["created_building", "reused_building"]
    with pg_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Key)) == 1
        assert session.scalar(select(func.count()).select_from(Generation)) == 1


def assert_lock_wait(observer, holder_pid, waiter_pid, *, timeout=3):
    """Only PostgreSQL's blocking relationship proves contention."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        blockers = observer.exec_driver_sql("SELECT pg_blocking_pids(%s)", (waiter_pid,)).scalar_one()
        if holder_pid in blockers:
            return
        sleep(0.01)
    pytest.fail(f"PostgreSQL waiter PID {waiter_pid} was not blocked by holder PID {holder_pid}")


def interleave(engine, holder, waiter):
    """Held key lock -> waiter issued -> DB proves blocking -> release -> finish."""
    held, release, waiter_issuing = Event(), Event(), Event()
    holder_thread = []
    pids = {}
    def is_key_lock(statement):
        return statement.startswith("SELECT") and "FROM discover_inventory_keys" in statement and "FOR UPDATE" in statement
    def after(conn, cursor, statement, params, context, many):
        if is_key_lock(statement) and holder_thread and get_ident() == holder_thread[0]:
            held.set()
            assert release.wait(12), "Holder release deadline exceeded"
    def before(conn, cursor, statement, params, context, many):
        if is_key_lock(statement):
            role = "holder" if get_ident() == holder_thread[0] else "waiter"
            pids[role] = conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            # Local to these transactions. Observer has 3s, lock wait has 6s.
            conn.exec_driver_sql("SET LOCAL lock_timeout = '6000ms'")
            conn.exec_driver_sql("SET LOCAL statement_timeout = '8000ms'")
            if role == "waiter":
                waiter_issuing.set()
    def first():
        holder_thread.append(get_ident())
        return holder()
    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        # Establish the observer before taking locks, so connection setup cannot
        # consume the waiter's lock-timeout budget on slower CI hosts.
        with engine.connect() as observer, ThreadPoolExecutor(max_workers=2) as pool:
            assert observer.exec_driver_sql("SELECT current_schema()").scalar_one().startswith("feed_test_")
            observer_pid = observer.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            a = pool.submit(first)
            try:
                assert held.wait(5)
                b = pool.submit(waiter)
                assert waiter_issuing.wait(5), "Waiter did not reach key-lock SQL"
                assert len({pids["holder"], pids["waiter"], observer_pid}) == 3
                assert_lock_wait(observer, pids["holder"], pids["waiter"])
            finally:
                release.set()
            return a.result(10), b.result(10)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)


def test_lock_wait_proof_fails_for_unblocked_backend(pg_sessions):
    engine = pg_sessions.kw["bind"]
    with engine.connect() as holder, engine.connect() as waiter, engine.connect() as observer:
        holder_pid = holder.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        waiter_pid = waiter.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        with pytest.raises(pytest.fail.Exception, match="was not blocked by holder PID"):
            assert_lock_wait(observer, holder_pid, waiter_pid, timeout=0.05)


def test_interleave_rejects_waiter_delayed_before_actual_sql(pg_sessions, monkeypatch):
    """A Python issuing event cannot replace an observed PostgreSQL lock wait."""
    engine = pg_sessions.kw["bind"]
    generation_id, _ = building(pg_sessions)
    holder_thread = []
    sql_gate, sql_delayed = Event(), Event()
    holder_finished, waiter_started, waiter_finished = Event(), Event(), Event()
    execute = engine.dialect.do_execute

    def resolve():
        with pg_sessions() as session:
            return InventoryRepository(session).resolve_generation(IDENTITY, NOW)

    def holder():
        holder_thread.append(get_ident())
        try:
            return resolve()
        finally:
            # resolve() has committed/rolled back and closed its session. Let the
            # waiter finish only after the holder no longer owns the target lock.
            holder_finished.set()
            sql_gate.set()

    def waiter():
        waiter_started.set()
        try:
            return resolve()
        finally:
            waiter_finished.set()

    def delay_execution(cursor, statement, parameters, context=None):
        if (statement.startswith("SELECT") and "FROM discover_inventory_keys" in statement
                and "FOR UPDATE" in statement and holder_thread and get_ident() != holder_thread[0]):
            # SQLAlchemy's before_cursor_execute listeners (including the helper's
            # issuing event) ran, but the DBAPI cursor has not sent this SQL yet.
            sql_delayed.set()
            assert sql_gate.wait(8), "Artificial waiter SQL gate deadline exceeded"
            assert holder_finished.is_set()
        return execute(cursor, statement, parameters, context)

    with monkeypatch.context() as patch:
        patch.setattr(engine.dialect, "do_execute", delay_execution)
        try:
            with pytest.raises(pytest.fail.Exception, match="was not blocked by holder PID"):
                interleave(engine, holder, waiter)
        finally:
            sql_gate.set()

    assert waiter_started.is_set() and sql_delayed.is_set()
    assert holder_finished.is_set() and waiter_finished.is_set()
    assert engine.pool.checkedout() == 0  # Includes the independent observer.
    # A subsequent independent transaction must work after the expected failure.
    assert resolve().generation_id == generation_id
    assert engine.pool.checkedout() == 0


def test_concurrent_resolve_existing_identity_observes_database_lock_wait(pg_sessions):
    generation_id, _ = building(pg_sessions)
    def resolve():
        with pg_sessions() as session:
            return InventoryRepository(session).resolve_generation(IDENTITY, NOW)
    first, second = interleave(pg_sessions.kw["bind"], resolve, resolve)
    assert first.generation_id == second.generation_id == generation_id
    assert first.outcome == second.outcome == "reused_building"


@pytest.mark.parametrize("publisher_first", [True, False])
def test_resolve_and_publication_use_same_key_lock(pg_sessions, publisher_first):
    generation_id, fence, counters = complete(pg_sessions)
    def publication():
        with pg_sessions() as session:
            InventoryRepository(session).publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
            assert not session.in_transaction()
    def resolution():
        with pg_sessions() as session:
            result = InventoryRepository(session).resolve_generation(IDENTITY, NOW)
            assert not session.in_transaction()
            return result
    results = interleave(pg_sessions.kw["bind"], publication if publisher_first else resolution,
                         resolution if publisher_first else publication)
    resolution_result = results[1 if publisher_first else 0]
    assert resolution_result.generation_id == generation_id
    assert resolution_result.outcome == ("reused_ready" if publisher_first else "reused_building")
    with pg_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Generation)) == 1


def test_partial_unique_index_rejects_competing_build_and_loser_can_resolve(pg_sessions):
    with pg_sessions() as session:
        InventoryRepository(session).resolve_generation(IDENTITY, NOW)
        with session.begin():
            key_id = session.scalar(select(Key.id))
            session.execute(Generation.__table__.delete())
    gate = Barrier(2)
    def attempt():
        with pg_sessions() as session:
            gate.wait(5)
            try:
                with session.begin():
                    session.execute(insert(Generation).values(id=uuid4().hex, key_id=key_id, created_at=NOW))
                outcome = "created"
            except IntegrityError:
                assert not session.in_transaction()
                outcome = "unique_rejected"
            result = InventoryRepository(session).resolve_generation(IDENTITY, NOW)
            return outcome, result
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        results = [future.result(10) for future in futures]
    assert sorted(outcome for outcome, _ in results) == ["created", "unique_rejected"]
    assert results[0][1].generation_id == results[1][1].generation_id
    assert all(result.outcome == "reused_building" for _, result in results)


def test_competing_page_commits_cannot_consume_same_checkpoint_twice(pg_sessions):
    generation_id, fence = building(pg_sessions)
    gate = Barrier(2)
    def commit():
        with pg_sessions() as session:
            gate.wait(5)
            repo = InventoryRepository(session)
            try:
                return repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
                    source_total=1, returned_count=1, items=[candidate()], now=NOW)
            except InventoryGenerationConflict:
                assert not session.in_transaction()
                return "checkpoint_changed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(commit) for _ in range(2)]
        results = [future.result(10) for future in futures]
    assert results.count("checkpoint_changed") == 1
    winner = next(result for result in results if result != "checkpoint_changed")
    assert (winner.next_source_page, winner.item_count, winner.records_examined) == (1, 1, 1)


def test_publication_visibility_is_atomic_for_independent_reader(pg_sessions):
    generation_id, fence, counters = complete(pg_sessions, items=[candidate()])
    entered, release = Event(), Event()
    def publisher():
        with pg_sessions() as session:
            def before_commit(session):
                entered.set()
                assert release.wait(5)
            event.listen(session, "before_commit", before_commit)
            InventoryRepository(session).publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publisher)
        try:
            assert entered.wait(5)
            with pg_sessions() as reader:
                assert reader.scalar(select(Generation.status).where(Generation.id == generation_id)) == "building"
                assert reader.scalar(select(func.count()).select_from(Item).join(Generation)
                    .where(Generation.id == generation_id, Generation.status == "ready")) == 0
        finally:
            release.set()
        future.result(10)
    with pg_sessions() as reader:
        assert len(InventoryRepository(reader).read_items(generation_id)) == 1


@pytest.mark.parametrize("pin_first", [True, False])
def test_pin_and_retirement_serialize_on_key(pg_sessions, pin_first):
    generation_id = publish(pg_sessions)
    def pin():
        with pg_sessions() as session:
            try:
                return InventoryRepository(session).create_view(generation_id, now=NOW + timedelta(minutes=14))
            except InventoryGenerationExpired:
                return "expired"
    def retire():
        with pg_sessions() as session:
            try:
                InventoryRepository(session).expire_generation(generation_id, NOW + timedelta(minutes=16))
                return "retired"
            except InventoryGenerationConflict:
                return "pinned"
    a, b = interleave(pg_sessions.kw["bind"], pin if pin_first else retire, retire if pin_first else pin)
    if pin_first:
        assert b == "pinned" and a.generation_id == generation_id
    else:
        assert a == "retired" and b == "expired"
