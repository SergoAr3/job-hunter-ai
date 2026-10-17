"""Real PostgreSQL sessions, bounded interleavings, disposable schemas only."""
import asyncio
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, insert, select, text
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.database import Base
from app.models import DiscoverFeedChain as Chain, DiscoverFeedState as State, User
from app.services.discover_feed import FeedService
from app.services.discover_feed_scan import FeedScanner
from app.services.discover_feed_types import Checkpoint, Evidence, FeedError, FeedIdentity, FeedStart, StoredFeed
from app.services.vacancy_sources import SourceError
from source_deadline_helpers import run
from test_discover_api import vacancy
from test_discover_feed_scan import Cache, Provider, page

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)
START = FeedStart(query="Backend", remote_only=True)


@pytest.fixture
def pg_sessions():
    if os.getenv("RUN_FEED_POSTGRES") != "1":
        pytest.skip("set RUN_FEED_POSTGRES=1 for disposable PostgreSQL schemas")
    admin = create_engine(os.getenv("IDENTITY_TEST_DATABASE_URL", DATABASE_URL),
                          connect_args={"connect_timeout": 3}, hide_parameters=True)
    schema = f"feed_test_{uuid4().hex}"
    test_engine = None
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        test_engine = create_engine(admin.url, hide_parameters=True, connect_args={
            "connect_timeout": 3, "options": f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=5000"})
        Base.metadata.create_all(test_engine)
        yield sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    finally:
        if test_engine is not None:
            test_engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()


def setup(sessions, *, provider=None, clock=lambda: NOW):
    cache = Cache()
    provider = provider or Provider()
    service = FeedService(FeedScanner(cache, provider), clock=clock)
    with sessions() as session:
        user = User(telegram_id=902)
        session.add(user)
        session.commit()
        user_id = user.id
        token = service.start(session, user_id, START)
    return service, user_id, token, cache, provider


def test_two_workers_one_scan_and_no_row_lock_or_transaction_during_network(pg_sessions):
    entered = Event()
    release = []
    class SlowProvider(Provider):
        async def search_with_deadline(self, query, deadline):
            assert pg_sessions.kw["bind"].pool.checkedout() == 0
            gate = asyncio.Event()
            release.append((asyncio.get_running_loop(), gate))
            entered.set()
            await gate.wait()
            return await super().search_with_deadline(query, deadline)
    service, user, token, cache, provider = setup(pg_sessions, provider=SlowProvider())
    def worker():
        return run(service.advance(pg_sessions, token))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(worker)
        try:
            assert entered.wait(5)
            with pg_sessions() as session:
                with pytest.raises(FeedError) as error:
                    run(service.advance(pg_sessions, token))
                assert error.value.code == "FEED_ADVANCE_IN_PROGRESS"
                assert not session.in_transaction()
                # Real NOWAIT locks succeed while worker A is awaiting source.
                with session.begin():
                    assert session.scalar(select(State).where(State.token == token).with_for_update(nowait=True))
                    assert session.scalar(select(Chain).with_for_update(nowait=True))
        finally:
            if release:
                release[0][0].call_soon_threadsafe(release[0][1].set)
        outcome = first.result(5)
    with pg_sessions() as session:
        repeated = run(service.advance(pg_sessions, token))
        assert repeated.token == outcome.token and repeated.stored == outcome.stored
        assert session.query(State).count() == 1  # terminal full page
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


def test_expired_lease_reclaim_zombie_finalize_and_release_cannot_overwrite(pg_sessions):
    now = [NOW]
    service, user, token, cache, provider = setup(pg_sessions, clock=lambda: now[0])
    with pg_sessions() as a, pg_sessions() as b:
        old = service.claim(a, token)
        now[0] += timedelta(seconds=46)
        new = service.claim(b, token)
        assert old.fence != new.fence
        result = StoredFeed(checkpoint=Checkpoint(offset=10, exhausted=True), items=[Evidence.model_validate(vacancy("winner", workplace_type="remote"))])
        with pytest.raises(FeedError) as error:
            service.finalize(a, old, result)
        assert error.value.code == "FEED_CLAIM_FENCED" and not a.in_transaction()
        won = service.finalize(b, new, result)
        service.release(a, old)
        assert service.replay(a, token).stored == won.stored
        assert not a.in_transaction() and not b.in_transaction()
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_failed_source_releases_claim_and_retry_works(pg_sessions):
    service, user, token, cache, provider = setup(pg_sessions, provider=Provider(error=SourceError()))
    with pg_sessions() as session:
        with pytest.raises(SourceError):
            run(service.advance(pg_sessions, token))
        assert not session.in_transaction()
        with session.begin():
            node = session.get(State, token)
            assert node.status == "ready" and node.lease_token is None
        provider.error = None
        assert run(service.advance(pg_sessions, token)).result == "page"
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0, 0]


def test_finalize_collision_rolls_back_sibling_counts_and_result(pg_sessions):
    service, user, token, cache, provider = setup(pg_sessions, provider=Provider({0: page(remote=[0]), 10: page(10, total=20)}))
    service.token_factory = lambda: token
    with pg_sessions() as session:
        with pytest.raises(FeedError) as error:
            run(service.advance(pg_sessions, token))
        assert error.value.code == "FEED_TOKEN_CAPACITY" and not session.in_transaction()
        with session.begin():
            node = session.get(State, token)
            chain = session.get(Chain, node.chain_id)
            assert node.status == "ready" and node.next_token is None
            assert (chain.page_count, chain.state_count) == (0, 1)
            assert session.query(State).count() == 1
        service.token_factory = lambda: secrets.token_urlsafe(24)
        assert run(service.advance(pg_sessions, token)).result == "page"
    assert cache.fresh_reads == [0, 10, 0, 10] and cache.cold_loads == provider.calls == [0, 10]


def test_concurrent_start_enforces_per_user_capacity(pg_sessions):
    service, user, first, cache, provider = setup(pg_sessions)
    with pg_sessions() as session:
        for _ in range(18):
            service.start(session, user, START)
    gate = Barrier(2)
    def worker():
        with pg_sessions() as session:
            gate.wait(5)
            try:
                return service.start(session, user, START)
            except FeedError as error:
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(worker) for _ in range(2)]
        results = [future.result(10) for future in pending]
    assert results.count("FEED_CHAIN_CAPACITY") == 1
    with pg_sessions() as session:
        assert session.query(Chain).count() == 20
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_bounded_cleanup_cascades_old_evidence_and_does_not_evict_live_chain(pg_sessions):
    now = [NOW - timedelta(days=2)]
    service, user, token, cache, provider = setup(pg_sessions, clock=lambda: now[0])
    with pg_sessions() as session:
        for _ in range(11):
            service.start(session, user, START)
        now[0] = NOW
        fresh = service.start(session, user, START)
        assert session.query(Chain).count() == session.query(State).count() == 3
        session.rollback()
        claimed = service.claim(session, fresh)  # next bounded cleanup removes remaining old chains
        assert session.query(Chain).count() == session.query(State).count() == 1
        session.rollback()
        service.release(session, claimed)
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_fk_indexes_are_planner_eligible_and_purge_preserves_active_materialized_graphs(pg_sessions):
    cache, provider = Cache(), Provider()
    service = FeedService(FeedScanner(cache, provider), clock=lambda: NOW)
    identity = FeedIdentity(query="Backend", remote_only=True, limit=1).model_dump_json()
    def token(chain, node):
        return f"{chain:016x}{node:016x}"
    with pg_sessions() as session:
        users = [User(telegram_id=1000 + index) for index in range(4)]
        session.add_all(users)
        session.flush()
        creators = [user.id for user in users]
        chains, nodes, links = [], [], []
        for chain in range(64):
            created = NOW - timedelta(days=2) if chain == 0 else NOW
            chain_id = f"{chain:032x}"
            chains.append(dict(id=chain_id, creator_id=creators[chain // 16], version=1,
                               identity_json=identity, created_at=created,
                               expires_at=created + timedelta(minutes=30), state_count=32, page_count=31))
            for index in range(32):
                complete = index < 31
                stored = StoredFeed(checkpoint=Checkpoint(offset=(index + 1 if complete else index) * 10),
                                    items=[Evidence.model_validate(vacancy(token(chain, index), workplace_type="remote"))] if complete else [])
                nodes.append(dict(token=token(chain, index), chain_id=chain_id,
                                  status="complete" if complete else "ready", payload=stored.encoded(),
                                  result_state="page" if complete else None))
                links.append(dict(token=token(chain, index), previous=token(chain, index - 1) if index else None,
                                  following=token(chain, index + 1) if complete else None))
        session.execute(insert(Chain), chains)
        session.execute(insert(State), nodes)
        session.execute(text("UPDATE discover_feed_states SET prev_token=:previous, next_token=:following WHERE token=:token"), links)
        session.commit()
        with session.begin():
            session.execute(text("ANALYZE discover_feed_states"))
            # Diagnostic only: eligibility, not brittle planner costs or tiny-table choices.
            session.execute(text("SET LOCAL enable_seqscan=off"))
            def index_names(plan):
                return {plan.get("Index Name")} | set().union(*(index_names(child) for child in plan.get("Plans", [])))
            for column, name in (("prev_token", "ix_feed_state_prev"), ("next_token", "ix_feed_state_next")):
                plan = session.execute(text(f"EXPLAIN (FORMAT JSON) UPDATE discover_feed_states SET {column}=NULL WHERE {column}=:token"),
                                       {"token": token(1, 5)}).scalar_one()[0]["Plan"]
                assert name in index_names(plan)
        with service.transaction(session):
            active_before = list(session.execute(select(State.token, State.prev_token, State.next_token).where(State.chain_id != f"{0:032x}").order_by(State.token)))
            savepoint = session.begin_nested()
            session.execute(delete(State).where(State.token == token(0, 5)))
            assert session.scalar(select(State.next_token).where(State.token == token(0, 4))) is None
            assert session.scalar(select(State.prev_token).where(State.token == token(0, 6))) is None
            savepoint.rollback()  # Restore the full expired graph before whole-chain purge.
            service.cleanup(session, NOW)
            assert session.scalar(select(func.count()).select_from(Chain)) == 63
            assert session.scalar(select(func.count()).select_from(State)) == 63 * 32
            active_after = list(session.execute(select(State.token, State.prev_token, State.next_token).order_by(State.token)))
            assert active_after == active_before
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []
