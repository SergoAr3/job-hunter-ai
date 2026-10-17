"""Real endpoint/worker/PG boundaries, finite lock waits and cancellation races."""
import asyncio
from threading import Event, get_ident

import httpx
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

import app.services.discover_feed as feed_module
from app.database import get_session
from app.discover_feed_routes import get_feed_service
from app.main import app
from app.models import DiscoverFeedChain as Chain, DiscoverFeedState as State
from app.services.discover_feed_types import FeedError
from conftest import client
from source_deadline_helpers import run
from test_discover_feed_postgres import pg_sessions, setup


@pytest.fixture
def endpoint(pg_sessions):
    service, user, token, cache, provider = setup(pg_sessions)
    previous = app.dependency_overrides[get_session]
    def request_session():
        with pg_sessions() as session:
            yield session
    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[get_feed_service] = lambda: service
    try:
        yield service, user, token, cache, provider
    finally:
        app.dependency_overrides[get_session] = previous
        app.dependency_overrides.pop(get_feed_service, None)


@pytest.mark.parametrize("cancel", [False, True])
def test_endpoint_lock_wait_keeps_heartbeat_alive_and_cancellation_joins_worker(pg_sessions, endpoint, cancel):
    service, user, token, cache, provider = endpoint
    engine = pg_sessions.kw["bind"]
    async def scenario():
        loop, loop_thread = asyncio.get_running_loop(), get_ident()
        waiting = asyncio.Event()
        sessions = set()
        def record(connection, cursor, statement, parameters, context, many):
            # Domain SQL must never execute on the async endpoint's thread.
            assert get_ident() != loop_thread
            if "FOR UPDATE OF discover_feed_chains" in statement:
                loop.call_soon_threadsafe(waiting.set)
        def began(session, transaction):
            if not transaction.nested:
                session.info["test_owner_thread"] = get_ident()
                sessions.add(id(session))
        def ended(session, transaction):
            if not transaction.nested:
                assert session.info["test_owner_thread"] == get_ident()
        ticks = []
        two_ticks = asyncio.Event()
        async def heartbeat():
            while True:
                await asyncio.sleep(0.05)
                ticks.append(1)
                if len(ticks) >= 2:
                    two_ticks.set()
        # The blocker is a different connection; acquired before instrumentation.
        with pg_sessions() as blocker:
            blocker.scalar(select(Chain).with_for_update())
            event.listen(engine, "before_cursor_execute", record)
            event.listen(Session, "after_transaction_create", began)
            event.listen(Session, "after_transaction_end", ended)
            beat = asyncio.create_task(heartbeat())
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                             base_url="http://feed-test", headers=client.headers) as browser:
                    request = asyncio.create_task(browser.post(f"/users/{user}/discover/feed", json={"cursor": token}))
                    await waiting.wait()
                    if cancel:
                        request.cancel()
                    # Explicitly observe heartbeats while the DB row is locked.
                    await two_ticks.wait()
                    assert not request.done()
                    if cancel:
                        request.cancel()  # Repeated cancellation must still join close/rollback.
                        with pytest.raises(asyncio.CancelledError):
                            await request
                    else:
                        response = await request
                        assert response.status_code == 409
                        assert response.json()["detail"]["code"] == "FEED_DB_LOCK_TIMEOUT"
                        assert response.headers["retry-after"] == "1"
                    assert len(ticks) >= 2 and sessions
                    assert engine.pool.checkedout() == 1  # Only our intentional blocker.
            finally:
                beat.cancel()
                await asyncio.gather(beat, return_exceptions=True)
                event.remove(engine, "before_cursor_execute", record)
                event.remove(Session, "after_transaction_create", began)
                event.remove(Session, "after_transaction_end", ended)
                blocker.rollback()
        with pg_sessions() as session:
            node = session.get(State, token)
            assert node.status == "ready" and node.lease_token is None
            # LOCAL settings did not escape into the pooled connection.
            assert session.execute(text("SHOW lock_timeout")).scalar_one() == "2s"
            assert session.execute(text("SHOW statement_timeout")).scalar_one() == "5s"
        assert engine.pool.checkedout() == 0
    run(scenario())
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_cancel_after_claim_commit_before_delivery_releases_committed_claim(pg_sessions, monkeypatch):
    service, user, token, cache, provider = setup(pg_sessions)
    original = service.claim
    committed, allow_return = Event(), Event()
    async def scenario():
        loop, visible = asyncio.get_running_loop(), asyncio.Event()
        def paused(session, cursor):
            result = original(session, cursor)
            committed.set()
            loop.call_soon_threadsafe(visible.set)
            assert allow_return.wait(3)
            return result
        monkeypatch.setattr(service, "claim", paused)
        request = asyncio.create_task(service.advance(pg_sessions, token))
        try:
            await visible.wait()
            assert committed.is_set()
            request.cancel()
            tick = loop.create_future()
            loop.call_soon(tick.set_result, None)
            await tick
            assert not request.done()
        finally:
            allow_return.set()
        with pytest.raises(asyncio.CancelledError):
            await request
    run(scenario())
    with pg_sessions() as session:
        node = session.get(State, token)
        assert node.status == "ready" and node.lease_token is None
    assert pg_sessions.kw["bind"].pool.checkedout() == 0
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_cancel_before_claim_commit_rolls_back_in_worker(pg_sessions, monkeypatch):
    service, user, token, cache, provider = setup(pg_sessions)
    allow_commit = Event()
    original = service.cleanup
    async def scenario():
        loop, before_commit = asyncio.get_running_loop(), asyncio.Event()
        def paused(session, now):
            original(session, now)
            loop.call_soon_threadsafe(before_commit.set)
            assert allow_commit.wait(3)
        monkeypatch.setattr(service, "cleanup", paused)
        request = asyncio.create_task(service.advance(pg_sessions, token))
        try:
            await before_commit.wait()
            request.cancel()
            tick = loop.create_future()
            loop.call_soon(tick.set_result, None)
            await tick
            assert not request.done()
        finally:
            allow_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await request
    run(scenario())
    with pg_sessions() as session:
        node = session.get(State, token)
        assert node.status == "ready" and node.lease_token is None
    assert pg_sessions.kw["bind"].pool.checkedout() == 0
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_cancel_during_claim_flush_rolls_back_without_committing_claim(pg_sessions):
    service, user, token, cache, provider = setup(pg_sessions)
    engine = pg_sessions.kw["bind"]
    commits = []
    async def scenario():
        loop, flushing = asyncio.get_running_loop(), asyncio.Event()
        def record(connection, cursor, statement, parameters, context, many):
            if statement.startswith("UPDATE discover_feed_states"):
                loop.call_soon_threadsafe(flushing.set)
        def committed(connection):
            commits.append(True)
        with pg_sessions() as blocker:
            blocker.scalar(select(State).where(State.token == token).with_for_update())
            event.listen(engine, "before_cursor_execute", record)
            event.listen(engine, "commit", committed)
            request = asyncio.create_task(service.advance(pg_sessions, token))
            try:
                await flushing.wait()
                request.cancel()
                tick = loop.create_future()
                loop.call_soon(tick.set_result, None)
                await tick
                assert not request.done()
                blocker.rollback()  # Let the worker finish the pending UPDATE, before timeout.
                with pytest.raises(asyncio.CancelledError):
                    await request
                assert not commits  # The transaction must roll back, not commit then release.
            finally:
                event.remove(engine, "before_cursor_execute", record)
                event.remove(engine, "commit", committed)
                blocker.rollback()
    run(scenario())
    with pg_sessions() as session:
        node = session.get(State, token)
        assert node.status == "ready" and node.lease_token is None
    assert pg_sessions.kw["bind"].pool.checkedout() == 0
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_db_phase_session_lifecycle_stays_on_one_worker_thread(pg_sessions):
    service, user, token, cache, provider = setup(pg_sessions)
    closed = []
    async def scenario():
        loop_thread = get_ident()
        class OwnedSession(Session):
            def __init__(self, *args, **kwargs):
                assert get_ident() != loop_thread
                self.owner = get_ident()
                super().__init__(*args, **kwargs)
            def close(self):
                assert get_ident() == self.owner
                super().close()
                closed.append(self.owner)
        factory = sessionmaker(bind=pg_sessions.kw["bind"], class_=OwnedSession, expire_on_commit=False)
        def operation(session):
            assert get_ident() == session.owner
            with service.transaction(session):
                assert session.execute(text("SELECT 1")).scalar_one() == 1
                assert session.execute(text("SHOW lock_timeout")).scalar_one() == "1s"
                assert session.execute(text("SHOW statement_timeout")).scalar_one() == "3s"
            return "plain result"
        assert await service.db_phase(factory, operation) == "plain result"
    run(scenario())
    assert len(closed) == 1 and pg_sessions.kw["bind"].pool.checkedout() == 0
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


@pytest.mark.parametrize("exception,code", [(OperationalError, "08006"), (OperationalError, "40001"),
                                          (IntegrityError, "23505"), (ProgrammingError, "42P01")])
def test_other_db_failures_keep_their_type_and_do_not_become_contention(pg_sessions, exception, code):
    service, user, token, cache, provider = setup(pg_sessions)
    class DriverError(Exception):
        sqlstate = code
    failure = exception("SELECT :cursor", {"cursor": token}, DriverError("injected DB failure"))
    def operation(session):
        with service.transaction(session):
            raise failure
    with pytest.raises(exception) as caught:
        run(service.db_phase(pg_sessions, operation))
    assert caught.value is failure and failure.hide_parameters
    assert token not in str(failure)
    assert pg_sessions.kw["bind"].pool.checkedout() == 0
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_generic_db_failure_is_http_500_not_logical_conflict(pg_sessions, endpoint, monkeypatch):
    service, user, token, cache, provider = endpoint
    def fail(session, token):
        with service.transaction(session):
            raise OperationalError("SELECT :cursor", {"cursor": token}, RuntimeError("DB unavailable"))
    monkeypatch.setattr(service, "claim", fail)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                     base_url="http://feed-test", headers=client.headers) as browser:
            response = await browser.post(f"/users/{user}/discover/feed", json={"cursor": token})
            assert response.status_code == 500
    run(scenario())
    assert pg_sessions.kw["bind"].pool.checkedout() == 0
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_statement_timeout_is_separate_retryable_db_error(pg_sessions, monkeypatch):
    service, user, token, cache, provider = setup(pg_sessions)
    monkeypatch.setattr(feed_module, "DB_STATEMENT_TIMEOUT_MS", 20)
    def operation(session):
        with service.transaction(session):
            session.execute(text("SELECT pg_sleep(0.2)"))
    with pytest.raises(FeedError) as caught:
        run(service.db_phase(pg_sessions, operation))
    assert caught.value.code == "FEED_DB_QUERY_TIMEOUT" and caught.value.status == 503
    assert pg_sessions.kw["bind"].pool.checkedout() == 0
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []
