import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.services.source_deadline import SourceDeadlineExceeded
from app.services.source_search_cache import SourceSearchCache
from source_deadline_helpers import Clock, run
from test_source_deadline_cache import AsyncProvider, observe_followers
from test_source_search_cache import Provider, QUERY


@pytest.mark.parametrize("elapsed", [0, 29.999])
def test_foreign_timeout_is_preserved_before_own_expiry(monkeypatch, elapsed):
    clock = Clock(monkeypatch)
    error = TimeoutError("inner operation timeout")

    async def scenario():
        deadline = clock.deadline(30)
        with pytest.raises(TimeoutError) as caught:
            async with deadline.enforce():
                clock.advance(elapsed)
                raise error
        assert caught.value is error
        assert deadline.remaining() > 0

    run(scenario())


def test_own_scope_expiry_is_a_source_deadline(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        entered, never = asyncio.Event(), asyncio.Event()

        async def operation():
            async with clock.deadline(2).enforce():
                entered.set()
                await never.wait()

        task = asyncio.create_task(operation())
        await entered.wait()
        clock.advance(2)
        with pytest.raises(SourceDeadlineExceeded):
            await task

    run(scenario())


@pytest.mark.parametrize("error", [ValueError("bad value"), RuntimeError("operation error")])
def test_other_errors_are_not_reclassified(monkeypatch, error):
    clock = Clock(monkeypatch)

    async def scenario():
        with pytest.raises(type(error)) as caught:
            async with clock.deadline().enforce():
                raise error
        assert caught.value is error

    run(scenario())


def test_caller_cancel_is_not_reclassified(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        entered, never = asyncio.Event(), asyncio.Event()

        async def operation():
            async with clock.deadline().enforce():
                entered.set()
                await never.wait()

        task = asyncio.create_task(operation())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())


def test_legacy_owner_timeout_identity_reaches_async_follower(monkeypatch):
    clock, entered, release = Clock(monkeypatch), Event(), Event()
    error = TimeoutError("legacy operation timeout")

    def load(_query):
        entered.set()
        assert release.wait(5)
        raise error

    cache = SourceSearchCache(clock=clock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(cache.search, Provider(load), QUERY)
        assert entered.wait(5)

        async def scenario():
            waiting = observe_followers(monkeypatch)
            deadline = clock.deadline(30)
            follower = asyncio.create_task(cache.get_or_load(AsyncProvider(), QUERY, deadline))
            await waiting.wait()
            release.set()
            with pytest.raises(TimeoutError) as caught:
                await follower
            assert caught.value is error and deadline.remaining() > 0

        try:
            run(scenario())
        finally:
            release.set()
        with pytest.raises(TimeoutError) as caught:
            owner.result(5)
        assert caught.value is error
