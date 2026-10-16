import asyncio
from concurrent.futures import Future
from dataclasses import replace
from threading import Event

import pytest

import app.services.source_search_cache as cache_module
from app.services.source_deadline import SourceDeadline, SourceDeadlineExceeded, SourceLoadCancelled
from app.services.source_search_cache import SourceSearchCache
from app.services.vacancy_sources import SearchPage, SourceError
from source_deadline_helpers import Clock, run
from test_source_search_cache import Provider, QUERY


class AsyncProvider:
    source_key = "trudvsem"

    def __init__(self):
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.failure = None

    async def search_with_deadline(self, query, deadline):
        self.calls += 1
        self.entered.set()
        try:
            await self.release.wait()
            if self.failure:
                raise self.failure
            return SearchPage([], 0, query.limit, query.offset)
        finally:
            self.closed = True


def observe_followers(monkeypatch):
    entered = asyncio.Event()
    original = cache_module.SourceSearchCache._shared_bridge

    def observed(cache, future):
        wrapped = original(cache, future)
        entered.set()
        return wrapped

    monkeypatch.setattr(cache_module.SourceSearchCache, "_shared_bridge", observed)
    return entered


def test_fresh_only_hit_miss_expiry_isolation_and_lru(monkeypatch):
    clock, provider = Clock(monkeypatch), Provider()
    cache = SourceSearchCache(clock=clock, max_entries=2)
    a, b, c = [replace(QUERY, keywords=value) for value in "ABC"]
    assert cache.get_if_fresh(provider, a) is None
    assert provider.queries == [] and cache.info().inflight == 0
    cache.search(provider, a)
    cache.search(provider, b)
    ready = cache.get_if_fresh(provider, a)
    assert ready is not None
    ready.items.clear()
    assert len(cache.get_if_fresh(provider, a).items) == 1
    cache.search(provider, c)
    assert cache.get_if_fresh(provider, b) is None  # A promoted, B evicted.
    assert provider.queries == [a, b, c]
    clock.value += 120
    assert cache.get_if_fresh(provider, a) is None  # HIT did not slide TTL.
    assert cache.info().accounted_bytes == cache.info().entries == 0
    assert provider.queries == [a, b, c]


def test_deadline_load_uses_legacy_hit_and_exact_key(monkeypatch):
    clock = Clock(monkeypatch)
    cache, legacy, provider = SourceSearchCache(clock=clock), Provider(), AsyncProvider()
    expected = cache.search(legacy, QUERY)

    async def scenario():
        first = await cache.get_or_load(provider, QUERY, clock.deadline())
        assert first == expected and first.items is not expected.items
        assert provider.calls == 0
        provider.release.set()
        await cache.get_or_load(provider, replace(QUERY, offset=1), clock.deadline())
        assert provider.calls == 1  # No offset rounding in the cache.

    run(scenario())


def test_expired_deadline_does_not_load_or_return_hit(monkeypatch):
    clock, provider = Clock(monkeypatch), AsyncProvider()
    cache = SourceSearchCache(clock=clock)

    async def scenario():
        for warm in (False, True):
            if warm:
                cache.search(Provider(), QUERY)
            with pytest.raises(SourceDeadlineExceeded):
                await cache.get_or_load(provider, QUERY, clock.deadline(0))
        assert provider.calls == 0 and cache.info().inflight == 0

    run(scenario())


def test_owner_and_follower_success_is_one_load_and_shares_with_legacy(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        provider, cache = AsyncProvider(), SourceSearchCache(clock=clock)
        waiting = observe_followers(monkeypatch)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
        await provider.entered.wait()
        follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(20)))
        await waiting.wait()
        assert cache.get_if_fresh(provider, QUERY) is None
        assert cache.info().inflight == 1 and provider.calls == 1
        provider.release.set()
        first, second = await asyncio.gather(owner, follower)
        assert first == second and first.items is not second.items
        legacy = Provider()
        assert cache.search(legacy, QUERY) == first and legacy.queries == []
        assert provider.closed and cache.info().inflight == 0

    run(scenario())


def test_short_follower_times_out_without_cancelling_or_replacing_owner(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        provider, cache = AsyncProvider(), SourceSearchCache(clock=clock)
        waiting = observe_followers(monkeypatch)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(20)))
        await provider.entered.wait()
        follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(2)))
        await waiting.wait()
        clock.advance(2)
        with pytest.raises(SourceDeadlineExceeded):
            await follower
        assert not owner.done() and not provider.closed
        assert provider.calls == 1 and cache.info().inflight == 1
        assert cache.get_if_fresh(provider, QUERY) is None
        provider.release.set()
        await owner
        assert cache.get_if_fresh(provider, QUERY) is not None
        assert provider.calls == 1 and cache.info().inflight == 0

    run(scenario())


@pytest.mark.parametrize("failure", ["deadline", "cancel", "error"])
def test_owner_failure_closes_operation_notifies_followers_and_allows_new_owner(monkeypatch, failure):
    clock = Clock(monkeypatch)

    async def scenario():
        provider, cache = AsyncProvider(), SourceSearchCache(clock=clock)
        waiting = observe_followers(monkeypatch)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(2)))
        await provider.entered.wait()
        follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(20)))
        await waiting.wait()
        if failure == "deadline":
            clock.advance(2)
            owner_error = follower_error = SourceDeadlineExceeded
        elif failure == "cancel":
            owner.cancel()
            owner_error, follower_error = asyncio.CancelledError, SourceLoadCancelled
        else:
            provider.failure = SourceError("failed")
            provider.release.set()
            owner_error = follower_error = SourceError
        with pytest.raises(owner_error):
            await owner
        with pytest.raises(follower_error):
            await follower
        assert provider.closed and provider.calls == 1
        assert cache.info().inflight == cache.info().entries == 0
        replacement = AsyncProvider()
        replacement.release.set()
        await cache.get_or_load(replacement, QUERY, clock.deadline())
        assert replacement.calls == 1 and cache.info().entries == 1

    run(scenario())


def test_cancelled_follower_does_not_cancel_shared_owner(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        provider, cache = AsyncProvider(), SourceSearchCache(clock=clock)
        waiting = observe_followers(monkeypatch)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
        await provider.entered.wait()
        follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
        await waiting.wait()
        follower.cancel()
        with pytest.raises(asyncio.CancelledError):
            await follower
        assert not provider.closed and cache.info().inflight == 1
        provider.release.set()
        await owner
        assert provider.calls == 1 and cache.info().entries == 1

    run(scenario())


def test_late_old_flight_cannot_overwrite_or_remove_new_owner(monkeypatch):
    clock = Clock(monkeypatch)
    cache, key = SourceSearchCache(clock=clock), ("trudvsem", QUERY)
    old = Future()
    new = Future()
    cache._inflight[key] = new
    page = cache_module._Page((), 0, 10, 0)
    # Both publication and finally cleanup must compare identity (ABA guard).
    cache._store(key, page, clock(), old, True, clock.deadline())
    cache._release(key, old)
    assert cache._inflight[key] is new
    assert cache.info().entries == 0


def test_async_follower_can_join_legacy_thread_owner(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    clock = Clock(monkeypatch)
    entered, release = Event(), Event()

    def load(query):
        entered.set()
        assert release.wait(5)
        return SearchPage([], 0, query.limit, query.offset)

    cache, legacy = SourceSearchCache(clock=clock), Provider(load)
    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(cache.search, legacy, QUERY)
        assert entered.wait(5)

        async def scenario():
            waiting = observe_followers(monkeypatch)
            provider = AsyncProvider()
            follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
            await waiting.wait()
            release.set()
            assert await follower == SearchPage([], 0, 10, 0)
            assert provider.calls == 0

        try:
            run(scenario())
        finally:
            release.set()
        owner.result(5)


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_deadline_must_be_finite(value):
    with pytest.raises(ValueError):
        SourceDeadline(value)


def test_async_success_uses_completion_ttl_and_isolated_values(monkeypatch):
    clock = Clock(monkeypatch)
    expected = Provider().search(QUERY)

    class LoadingProvider:
        source_key = "trudvsem"

        async def search_with_deadline(self, query, deadline):
            clock.advance(8)
            return expected

    async def scenario():
        cache, provider = SourceSearchCache(clock=clock), LoadingProvider()
        result = await cache.get_or_load(provider, QUERY, clock.deadline(20))
        expected.items.clear()
        result.items.clear()
        clock.value = 227.999  # 120 seconds from completion at 108.
        assert len(cache.get_if_fresh(provider, QUERY).items) == 1
        clock.value = 228
        assert cache.get_if_fresh(provider, QUERY) is None

    run(scenario())


def test_late_provider_success_after_deadline_is_not_published(monkeypatch):
    clock = Clock(monkeypatch)

    class LateProvider:
        source_key = "trudvsem"

        async def search_with_deadline(self, query, deadline):
            clock.advance(11)
            return SearchPage([], 0, query.limit, query.offset)

    async def scenario():
        cache = SourceSearchCache(clock=clock)
        with pytest.raises(SourceDeadlineExceeded):
            await cache.get_or_load(LateProvider(), QUERY, clock.deadline(10))
        assert cache.info().inflight == cache.info().entries == 0

    run(scenario())


def test_async_inflight_overflow_is_bounded_uncached_passthrough(monkeypatch):
    clock = Clock(monkeypatch)

    async def scenario():
        cache = SourceSearchCache(clock=clock, max_inflight=1)
        owner_provider, other_provider = AsyncProvider(), AsyncProvider()
        owner = asyncio.create_task(cache.get_or_load(owner_provider, QUERY, clock.deadline()))
        await owner_provider.entered.wait()
        other_provider.release.set()
        for _ in range(2):
            await cache.get_or_load(other_provider, replace(QUERY, offset=10), clock.deadline())
        assert other_provider.calls == 2
        assert cache.info().inflight == 1 and cache.info().entries == 0
        owner_provider.release.set()
        await owner
        assert cache.info().inflight == 0 and cache.info().entries == 1

    run(scenario())


def test_async_replacement_survives_late_legacy_owner_cleanup(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    clock = Clock(monkeypatch)
    published, release_old = Event(), Event()

    class PausedPublication(Future):
        def set_result(self, result):
            super().set_result(result)
            if not published.is_set():
                published.set()
                assert release_old.wait(5)

    monkeypatch.setattr(cache_module, "Future", PausedPublication)
    cache, legacy = SourceSearchCache(clock=clock), Provider()
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(cache.search, legacy, QUERY)
        assert published.wait(5)
        clock.advance(120)

        async def scenario():
            provider = AsyncProvider()
            new = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
            await provider.entered.wait()
            release_old.set()
            await asyncio.wrap_future(old)
            assert cache.info().inflight == 1
            assert cache.get_if_fresh(provider, QUERY) is None
            waiting = observe_followers(monkeypatch)
            follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
            await waiting.wait()
            assert provider.calls == 1
            provider.release.set()
            first, second = await asyncio.gather(new, follower)
            assert first == second == SearchPage([], 0, 10, 0)
            assert cache.get_if_fresh(provider, QUERY) == first

        try:
            run(scenario())
        finally:
            release_old.set()
        assert cache.info().inflight == 0


def test_legacy_follower_joins_async_owner_without_second_load(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    clock, waiting = Clock(monkeypatch), Event()

    class ObservedFuture(Future):
        def result(self, timeout=None):
            waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(cache_module, "Future", ObservedFuture)

    async def scenario():
        cache, provider, legacy = SourceSearchCache(clock=clock), AsyncProvider(), Provider()
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline()))
        await provider.entered.wait()
        with ThreadPoolExecutor(max_workers=1) as pool:
            follower = pool.submit(cache.search, legacy, QUERY)
            try:
                assert waiting.wait(5)
                provider.release.set()
                result = await owner
                other = follower.result(5)
                assert result == other and result.items is not other.items
                assert provider.calls == 1 and legacy.queries == []
            finally:
                provider.release.set()

    run(scenario())
