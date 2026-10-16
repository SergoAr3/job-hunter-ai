import asyncio
import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.services.source_deadline import SourceDeadlineExceeded
from app.services.source_search_cache import SourceSearchCache
from app.services.vacancy_sources import SearchPage, SourceError
from source_deadline_helpers import Clock, run
from test_source_deadline_cache import AsyncProvider, observe_followers
from test_source_search_cache import Provider, QUERY


def bridge_count(cache):
    return sum(len(loops) for loops in cache._async_bridges.values())


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_fifty_finished_followers_retain_only_one_bridge_on_live_legacy_owner(monkeypatch, stop):
    clock, entered, release = Clock(monkeypatch), Event(), Event()

    def load(query):
        entered.set()
        assert release.wait(5)
        return SearchPage([], 0, query.limit, query.offset)

    cache, legacy = SourceSearchCache(clock=clock), Provider(load)
    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(cache.search, legacy, QUERY)
        assert entered.wait(5)
        shared = cache._inflight[("trudvsem", QUERY)]

        async def scenario():
            waiting = observe_followers(monkeypatch)
            original = SourceSearchCache._shared_bridge
            wrappers = weakref.WeakSet()

            def tracked(self, future):
                wrapped = original(self, future)
                if future is shared:
                    wrappers.add(wrapped)
                return wrapped

            monkeypatch.setattr(SourceSearchCache, "_shared_bridge", tracked)
            provider = AsyncProvider()
            for _ in range(50):
                waiting.clear()
                follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
                await waiting.wait()
                if stop == "timeout":
                    clock.advance(10)
                    error = SourceDeadlineExceeded
                else:
                    follower.cancel()
                    error = asyncio.CancelledError
                with pytest.raises(error):
                    await follower
            gc.collect()
            assert not owner.done() and not shared.done() and provider.calls == 0
            assert bridge_count(cache) == len(wrappers) == 1
            assert len(shared._done_callbacks) == 1
            bridge = next(iter(wrappers))
            assert len(bridge._callbacks or ()) <= 3  # No per-follower shield retention.
            release.set()
            assert await asyncio.wrap_future(owner) == (await bridge).public_page()
            assert bridge_count(cache) == cache.info().inflight == 0
            # A completed flight must not be reinstalled after owner cleanup.
            assert (await cache._shared_bridge(shared)).public_page() == SearchPage([], 0, 10, 0)
            assert bridge_count(cache) == 0

        try:
            run(scenario())
        finally:
            release.set()
        owner.result(5)


@pytest.mark.parametrize("order", ["complete_first", "timeout_first", "cancel_first"])
def test_owner_publication_races_with_follower_exit(monkeypatch, order):
    clock = Clock(monkeypatch)

    async def scenario():
        cache, provider = SourceSearchCache(clock=clock), AsyncProvider()
        joined = observe_followers(monkeypatch)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(30)))
        await provider.entered.wait()
        follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(1)))
        await joined.wait()
        bridge = next(iter(cache._async_bridges.values()))[asyncio.get_running_loop()]
        if order == "complete_first":
            provider.release.set()
            clock.advance(1)
        elif order == "timeout_first":
            clock.advance(1)
            provider.release.set()
        else:
            follower.cancel()
            provider.release.set()
        result = await owner
        outcome = (await asyncio.gather(follower, return_exceptions=True))[0]
        assert outcome == result or isinstance(outcome, (SourceDeadlineExceeded, asyncio.CancelledError))
        assert (await bridge).public_page() == result and not bridge.cancelled()
        assert bridge_count(cache) == cache.info().inflight == 0
        assert cache.get_if_fresh(provider, QUERY) == result and provider.calls == 1

    run(scenario())


def test_follower_cancel_after_publication_before_loop_delivery(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        cache, provider = SourceSearchCache(clock=clock), AsyncProvider()
        joined = observe_followers(monkeypatch)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
        await provider.entered.wait()
        follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
        await joined.wait()
        loop = asyncio.get_running_loop()
        bridge = next(iter(cache._async_bridges.values()))[loop]
        original = loop.call_soon_threadsafe
        deliveries = []

        def hold_delivery(callback, *args, context=None):
            if args and args[0] is bridge:
                deliveries.append((callback, args, context))
                return loop.call_soon(lambda: None)
            return original(callback, *args, context=context)

        monkeypatch.setattr(loop, "call_soon_threadsafe", hold_delivery)
        provider.release.set()
        result = await owner
        assert deliveries and not bridge.done() and bridge_count(cache) == 0
        follower.cancel()
        with pytest.raises(asyncio.CancelledError):
            await follower
        for callback, args, context in deliveries:
            original(callback, *args, context=context)
        assert (await bridge).public_page() == result and not bridge.cancelled()
        assert cache.get_if_fresh(provider, QUERY) == result and provider.calls == 1

    run(scenario())


def test_many_followers_mix_success_timeout_and_cancel(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        cache, provider = SourceSearchCache(clock=clock), AsyncProvider()
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(30)))
        await provider.entered.wait()
        original = SourceSearchCache._shared_bridge
        joined, count = asyncio.Event(), 0

        def observed(self, future):
            nonlocal count
            bridge = original(self, future)
            count += 1
            if count == 50:
                joined.set()
            return bridge

        monkeypatch.setattr(SourceSearchCache, "_shared_bridge", observed)
        followers = [asyncio.create_task(cache.get_or_load(
            provider, QUERY, clock.deadline(1 if i < 20 else 20),
        )) for i in range(50)]
        await joined.wait()
        for follower in followers[20:35]:
            follower.cancel()
        clock.advance(1)
        stopped = await asyncio.gather(*followers[:35], return_exceptions=True)
        assert all(isinstance(error, SourceDeadlineExceeded) for error in stopped[:20])
        assert all(isinstance(error, asyncio.CancelledError) for error in stopped[20:])
        assert bridge_count(cache) == 1 and provider.calls == 1
        provider.release.set()
        result = await owner
        assert all(page == result for page in await asyncio.gather(*followers[35:]))
        assert bridge_count(cache) == cache.info().inflight == 0

    run(scenario())


def test_owner_error_after_last_follower_cancel_is_consumed_and_registry_cleared(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        cache, provider = SourceSearchCache(clock=clock), AsyncProvider()
        joined = observe_followers(monkeypatch)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
        await provider.entered.wait()
        follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
        await joined.wait()
        loop = asyncio.get_running_loop()
        bridge = next(iter(cache._async_bridges.values()))[loop]
        follower.cancel()
        with pytest.raises(asyncio.CancelledError):
            await follower
        provider.failure = SourceError("owner failed after waiter left")
        provider.release.set()
        with pytest.raises(SourceError):
            await owner
        # Drain delivery/exception-consumer callbacks without wall-clock sleeps.
        for _ in range(2):
            tick = loop.create_future()
            loop.call_soon(tick.set_result, None)
            await tick
        assert bridge.done() and not bridge._log_traceback
        assert bridge_count(cache) == cache.info().inflight == cache.info().entries == 0

    run(scenario())
