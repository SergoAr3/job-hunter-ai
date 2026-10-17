"""A replay observes committed transitions without waiting for a finalizer."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import event

from app.services.discover_feed_types import Checkpoint, Evidence, StoredFeed
from source_deadline_helpers import run
from test_discover_api import vacancy
from test_discover_feed import replay
from test_discover_feed_db_boundary import endpoint
from test_discover_feed_postgres import pg_sessions
from test_discover_feed_scan import page


@pytest.mark.parametrize("result", ["paused", "page", "end"])
def test_replay_during_uncommitted_finalize_is_nonblocking_and_committed_only(pg_sessions, endpoint, monkeypatch, result):
    service, user, token, cache, provider = endpoint
    provider.pages = {0: page(remote=[0])}
    visible = run(service.advance(pg_sessions, token))
    with pg_sessions() as session:
        claimed = service.claim(session, visible.next)
    stored = StoredFeed(checkpoint=Checkpoint(offset=20, exhausted=result == "end"),
                        items=[Evidence.model_validate(vacancy("next-page", workplace_type="remote"))] if result == "page" else [])
    flushed, allow_commit = Event(), Event()
    original_load = service.load
    def gated_load(session, cursor, *, lock=False):
        loaded = original_load(session, cursor, lock=lock)
        if cursor == claimed.token and not lock and loaded[2].result == result:
            # Finalize has flushed its node and any new child, but has not committed.
            flushed.set()
            assert allow_commit.wait(4)
        return loaded
    monkeypatch.setattr(service, "load", gated_load)
    def finalize():
        with pg_sessions() as session:
            return service.finalize(session, claimed, stored)
    engine = pg_sessions.kw["bind"]
    before = list(cache.fresh_reads), list(cache.cold_loads), list(provider.calls)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(finalize)
        statements = []
        def record(connection, cursor, statement, parameters, context, many):
            statements.append(statement)
        try:
            assert flushed.wait(4)
            event.listen(engine, "before_cursor_execute", record)
            try:
                response = replay(user, visible.token)
            finally:
                event.remove(engine, "before_cursor_execute", record)
            assert response.status_code == 200
            assert response.json()["cursor"] == visible.token
            assert response.json()["next_cursor"] == claimed.token
            assert response.json()["next_action"] == "next"
            assert not future.done()  # GET did not wait for the uncommitted finalizer.
            assert all(sql.lstrip().upper().startswith("SELECT") and "FOR UPDATE" not in sql for sql in statements)
            assert engine.pool.checkedout() == 1  # Only the deliberately suspended finalizer.
        finally:
            allow_commit.set()
        outcome = future.result(5)
    committed = replay(user, visible.token).json()
    assert committed["cursor"] == visible.token
    if result == "paused":
        assert committed["next_cursor"] == outcome.next
        assert committed["next_action"] == "continue"
    elif result == "end":
        assert committed["next_cursor"] is committed["next_action"] is None
    else:
        assert committed["next_cursor"] == outcome.token
        assert committed["next_action"] == "next"
    assert run(service.advance(pg_sessions, claimed.token)).stored == stored
    assert (cache.fresh_reads, cache.cold_loads, provider.calls) == before
    assert engine.pool.checkedout() == 0
