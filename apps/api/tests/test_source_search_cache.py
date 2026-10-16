"""Finite source-cache tests: fake time, injected providers, no HTTP or sleeps."""

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock

import pytest

import app.services.source_search_cache as cache_module
from app.services.source_search_cache import (
    CachedSearchProvider,
    SourceSearchCache,
    SourceSearchWaitTimeout,
)
from app.services.vacancy_sources import DiscoverQuery, SearchPage, SourceError
from test_discover_api import vacancy


QUERY = DiscoverQuery("Backend", "RU", None, 10, 0)
WAIT = 5


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


class Provider:
    source_key = "trudvsem"

    def __init__(self, load=None):
        self.queries = []
        self.details = []
        self.lock = Lock()
        self.load = load or (lambda query: SearchPage([vacancy()], 25, query.limit, query.offset))

    def supports_market(self, market_country):
        return market_country == "RU"

    def search(self, query):
        with self.lock:
            self.queries.append(query)
        return self.load(query)

    def get_detail(self, scope, external_id):
        self.details.append((scope, external_id))
        return vacancy(external_id, source_scope=scope)


def test_hit_and_mutable_list_isolation():
    original = SearchPage([vacancy()], 25, 10, 0)
    provider = Provider(lambda _query: original)
    cache = SourceSearchCache()
    first = cache.search(provider, QUERY)
    first.items.clear()
    original.items.clear()
    second = cache.search(provider, QUERY)
    assert second == SearchPage([vacancy()], 25, 10, 0)
    third = cache.search(provider, QUERY)
    assert third == second and third.items is not second.items
    assert provider.queries == [QUERY]


@pytest.mark.parametrize("change", [
    {"keywords": "backend"},
    {"keywords": "Back  end"},
    {"keywords": "Back end"},
    {"market_country": "AM"},
    {"market_region": "77"},
    {"limit": 20},
    {"offset": 10},
    {"offset": 1},  # No key rounding even if this provider accepts the offset.
])
def test_every_query_field_has_a_separate_key(change):
    provider, cache = Provider(), SourceSearchCache()
    changed = replace(QUERY, **change)
    cache.search(provider, QUERY)
    cache.search(provider, changed)
    cache.search(provider, changed)
    assert provider.queries == [QUERY, changed]


def test_source_key_isolation_and_page_sharing_across_provider_instances():
    cache = SourceSearchCache()
    first, equivalent, other = Provider(), Provider(), Provider()
    other.source_key = "another-official-source"
    cache.search(first, QUERY)
    cache.search(equivalent, QUERY)
    cache.search(other, QUERY)
    assert first.queries == other.queries == [QUERY]
    assert equivalent.queries == []


def test_fixed_ttl_does_not_slide_and_expiry_releases_accounted_bytes():
    clock, provider = Clock(), Provider()
    cache = SourceSearchCache(clock=clock)
    cache.search(provider, QUERY)
    assert cache.info().accounted_bytes > 0
    clock.value = 119.999
    cache.search(provider, QUERY)
    assert len(provider.queries) == 1
    clock.value = 120
    assert cache.info().entries == cache.info().accounted_bytes == 0
    cache.search(provider, QUERY)
    assert len(provider.queries) == 2


def test_ttl_starts_after_successful_source_load():
    clock = Clock()

    def load(query):
        clock.value += 8
        return SearchPage([], 0, query.limit, query.offset)

    provider, cache = Provider(load), SourceSearchCache(clock=clock)
    cache.search(provider, QUERY)
    clock.value = 127.999
    cache.search(provider, QUERY)
    assert len(provider.queries) == 1
    clock.value = 128
    cache.search(provider, QUERY)
    assert len(provider.queries) == 2


def test_lru_hit_promotes_entry():
    provider, cache = Provider(), SourceSearchCache(max_entries=2)
    a, b, c = [replace(QUERY, keywords=key) for key in "ABC"]
    for query in (a, b, a, c, a, b):
        cache.search(provider, query)
    assert provider.queries == [a, b, c, b]
    assert cache.info().entries == 2


def test_expired_recent_entry_is_removed_before_evicting_live_lru():
    clock, provider = Clock(), Provider()
    cache = SourceSearchCache(max_entries=2, clock=clock)
    a, b, c = [replace(QUERY, keywords=key) for key in "ABC"]
    cache.search(provider, a)
    clock.value = 60
    cache.search(provider, b)
    clock.value = 100
    cache.search(provider, a)  # A is recent, but still expires before B.
    clock.value = 121
    cache.search(provider, c)
    cache.search(provider, b)
    assert provider.queries == [a, b, c]
    assert cache.info().entries == 2


def test_default_entry_count_bound():
    provider, cache = Provider(), SourceSearchCache()
    for offset in range(129):
        cache.search(provider, replace(QUERY, offset=offset * 10))
    assert cache.info().entries == 128
    cache.search(provider, QUERY)
    assert len(provider.queries) == 130


def test_byte_budget_evicts_and_accounting_decreases_on_expiry():
    clock, provider = Clock(), Provider()
    a, b, c = [replace(QUERY, keywords=key) for key in "ABC"]
    probe = SourceSearchCache()
    probe.search(provider, a)
    page_bytes = probe.info().accounted_bytes
    cache = SourceSearchCache(max_bytes=page_bytes * 2, clock=clock)
    for query in (a, b, c):
        cache.search(provider, query)
    assert cache.info().entries == 2
    assert cache.info().accounted_bytes == page_bytes * 2
    cache.search(provider, a)
    assert provider.queries == [a, a, b, c, a]
    assert cache.info().accounted_bytes == page_bytes * 2
    clock.value = 120
    assert cache.info().entries == cache.info().accounted_bytes == 0


def test_accounting_includes_nested_vacancy_text():
    small = SourceSearchCache()
    small.search(Provider(), QUERY)
    large = SourceSearchCache()
    item = replace(vacancy(), description="漢" * 10000)
    large.search(Provider(lambda query: SearchPage([item], 1, query.limit, query.offset)), QUERY)
    assert large.info().accounted_bytes >= small.info().accounted_bytes + 19000


def test_default_oversized_entry_is_returned_but_not_stored(caplog):
    item = replace(vacancy(), description="x" * (1024 * 1024))
    provider = Provider(lambda query: SearchPage([item], 1, query.limit, query.offset))
    cache = SourceSearchCache()
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        assert cache.search(provider, QUERY).items == [item]
        cache.search(provider, QUERY)
    assert len(provider.queries) == 2
    assert cache.info().entries == cache.info().accounted_bytes == cache.info().inflight == 0
    assert "outcome=bypass reason=oversized" in caplog.text


def test_valid_empty_page_is_cached():
    provider = Provider(lambda query: SearchPage([], 10, query.limit, query.offset))
    query, cache = replace(QUERY, offset=10), SourceSearchCache()
    assert cache.search(provider, query) == cache.search(provider, query) == SearchPage([], 10, 10, 10)
    assert provider.queries == [query]


@pytest.mark.parametrize("failure", [SourceError("unavailable"), ValueError("invalid query"), TimeoutError("socket")])
def test_errors_are_not_cached_and_owner_can_retry(failure):
    def fail(_query):
        raise failure

    provider, cache = Provider(fail), SourceSearchCache()
    for _ in range(2):
        with pytest.raises(type(failure)) as error:
            cache.search(provider, QUERY)
        assert error.value is failure
        assert cache.info().entries == cache.info().accounted_bytes == cache.info().inflight == 0
    provider.load = lambda query: SearchPage([], 0, query.limit, query.offset)
    cache.search(provider, QUERY)
    cache.search(provider, QUERY)
    assert len(provider.queries) == 3


def test_expired_entry_does_not_reuse_completed_future_before_owner_cleanup(monkeypatch):
    clock = Clock()
    published, release_owner = Event(), Event()

    class PausedPublication(Future):
        def set_result(self, result):
            super().set_result(result)
            if not published.is_set():
                published.set()
                assert release_owner.wait(WAIT)

    monkeypatch.setattr(cache_module, "Future", PausedPublication)

    def load(query):
        item = replace(vacancy(), title=f"v{len(provider.queries)}")
        return SearchPage([item], 1, query.limit, query.offset)

    provider, cache = Provider(load), SourceSearchCache(clock=clock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            owner = pool.submit(cache.search, provider, QUERY)
            assert published.wait(WAIT)  # Entry stored, Future done, cleanup pending.
            assert cache.info().entries == cache.info().inflight == 1
            clock.value = 121
            assert cache.info().entries == cache.info().accounted_bytes == 0
            fresh = cache.search(provider, QUERY)
            assert fresh.items[0].title == "v2"
            assert provider.queries == [QUERY, QUERY]
            assert cache.info().entries == 1
        finally:
            release_owner.set()
        assert owner.result(WAIT).items[0].title == "v1"
    assert cache.info().inflight == 0
    assert cache.search(provider, QUERY) == fresh
    assert provider.queries == [QUERY, QUERY]


def test_old_owner_cleanup_preserves_replacement_pending_owner(monkeypatch):
    clock = Clock()
    published, release_old = Event(), Event()
    new_loading, release_new = Event(), Event()

    class PausedPublication(Future):
        def set_result(self, result):
            super().set_result(result)
            if not published.is_set():
                published.set()
                assert release_old.wait(WAIT)

    monkeypatch.setattr(cache_module, "Future", PausedPublication)

    def load(query):
        version = len(provider.queries)
        if version == 2:
            new_loading.set()
            assert release_new.wait(WAIT)
        return SearchPage([replace(vacancy(), title=f"v{version}")], 1, query.limit, query.offset)

    provider = Provider(load)
    cache = SourceSearchCache(clock=clock, follower_wait_seconds=0.001)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            old_owner = pool.submit(cache.search, provider, QUERY)
            assert published.wait(WAIT)
            clock.value = 121
            new_owner = pool.submit(cache.search, provider, QUERY)
            assert new_loading.wait(WAIT)  # B has replaced A's completed Future.
            release_old.set()
            assert old_owner.result(WAIT).items[0].title == "v1"
            assert cache.info().inflight == 1
            # If A removed B's registration, this call would load v3 instead
            # of coalescing with B. Timeout must leave the live B untouched.
            with pytest.raises(SourceSearchWaitTimeout):
                cache.search(provider, QUERY)
            assert provider.queries == [QUERY, QUERY]
            assert cache.info().inflight == 1
        finally:
            release_old.set()
            release_new.set()
        fresh = new_owner.result(WAIT)
        assert fresh.items[0].title == "v2"
    assert cache.info().inflight == 0
    assert cache.search(provider, QUERY) == fresh
    assert provider.queries == [QUERY, QUERY]


@pytest.mark.parametrize("failure", [None, SourceError("failed"), TimeoutError("owner timeout")])
def test_identical_concurrent_misses_share_result_or_error_and_cleanup(monkeypatch, failure):
    entered, release, follower_waiting = Event(), Event(), Event()

    class ObservedFuture(Future):
        def result(self, timeout=None):
            follower_waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(cache_module, "Future", ObservedFuture)

    def load(query):
        entered.set()
        assert release.wait(WAIT)
        if failure is not None:
            raise failure
        return SearchPage([vacancy()], 1, query.limit, query.offset)

    provider, cache = Provider(load), SourceSearchCache()
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            owner = pool.submit(cache.search, provider, QUERY)
            assert entered.wait(WAIT)
            follower = pool.submit(cache.search, provider, QUERY)
            assert follower_waiting.wait(WAIT)
            assert provider.queries == [QUERY]
            assert cache.info().inflight == 1
        finally:
            release.set()
        if failure is None:
            first, second = owner.result(WAIT), follower.result(WAIT)
            assert first == second and first.items is not second.items
            assert cache.info().entries == 1
        else:
            for task in (owner, follower):
                with pytest.raises(type(failure)) as error:
                    task.result(WAIT)
                assert error.value is failure
            assert cache.info().entries == 0
    assert cache.info().inflight == 0
    if failure is not None:
        provider.load = lambda query: SearchPage([], 0, query.limit, query.offset)
        cache.search(provider, QUERY)
        assert len(provider.queries) == 2


def test_different_keys_load_without_global_network_lock():
    entered, release = Event(), Event()

    def load(query):
        if query == QUERY:
            entered.set()
            assert release.wait(WAIT)
        return SearchPage([], 0, query.limit, query.offset)

    provider, cache = Provider(load), SourceSearchCache()
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            blocked = pool.submit(cache.search, provider, QUERY)
            assert entered.wait(WAIT)
            other = pool.submit(cache.search, provider, replace(QUERY, keywords="Other"))
            assert other.result(WAIT).items == []
            assert not blocked.done()
        finally:
            release.set()
        blocked.result(WAIT)


def test_sixteen_inflight_keys_allow_uncached_passthrough_and_existing_followers(monkeypatch, caplog):
    release, follower_waiting = Event(), Event()
    entered = [Event() for _ in range(16)]

    class ObservedFuture(Future):
        def result(self, timeout=None):
            follower_waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(cache_module, "Future", ObservedFuture)

    def load(query):
        if query.offset < 160:
            entered[query.offset // 10].set()
            assert release.wait(WAIT)
        return SearchPage([], 0, query.limit, query.offset)

    provider, cache = Provider(load), SourceSearchCache()
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), ThreadPoolExecutor(max_workers=18) as pool:
        try:
            owners = [pool.submit(cache.search, provider, replace(QUERY, offset=i * 10)) for i in range(16)]
            assert all(event.wait(WAIT) for event in entered)
            assert cache.info().inflight == 16
            follower = pool.submit(cache.search, provider, QUERY)
            assert follower_waiting.wait(WAIT)
            for _ in range(2):
                result = pool.submit(cache.search, provider, replace(QUERY, offset=160)).result(WAIT)
                assert result == SearchPage([], 0, 10, 160)
            assert cache.info().inflight == 16 and cache.info().entries == 0
            assert len(provider.queries) == 18
        finally:
            release.set()
        assert follower.result(WAIT) == owners[0].result(WAIT)
        for owner in owners:
            owner.result(WAIT)
    assert cache.info().inflight == 0 and cache.info().entries == 16
    assert "outcome=bypass reason=in-flight-limit" in caplog.text
    assert "outcome=coalesced" in caplog.text


def test_follower_timeout_keeps_live_owner_registered_and_does_not_retry():
    entered, release = Event(), Event()

    def load(query):
        entered.set()
        assert release.wait(WAIT)
        return SearchPage([], 0, query.limit, query.offset)

    provider, cache = Provider(load), SourceSearchCache(follower_wait_seconds=0.001)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            owner = pool.submit(cache.search, provider, QUERY)
            assert entered.wait(WAIT)
            for _ in range(2):
                with pytest.raises(SourceSearchWaitTimeout) as error:
                    cache.search(provider, QUERY)
                assert error.value.code == "source_timeout"
                assert cache.info().inflight == 1  # Active owner, not stale bookkeeping.
            assert provider.queries == [QUERY]
        finally:
            release.set()
        assert owner.result(WAIT).items == []
    assert cache.info().inflight == 0
    cache.search(provider, QUERY)
    assert provider.queries == [QUERY]


def test_adapter_only_caches_search_and_delegates_market_and_details():
    provider = Provider()
    wrapped = CachedSearchProvider(provider, SourceSearchCache())
    assert wrapped.source_key == provider.source_key
    assert wrapped.supports_market("RU") and not wrapped.supports_market("AM")
    wrapped.search(QUERY)
    wrapped.search(QUERY)
    assert len(provider.queries) == 1
    for _ in range(2):
        assert wrapped.get_detail("scope", "id") == vacancy("id", source_scope="scope")
    assert provider.details == [("scope", "id"), ("scope", "id")]


def test_logging_has_timings_capacity_and_age_without_query_or_payload(caplog):
    clock = Clock()

    def load(query):
        clock.value += 8
        return SearchPage([replace(vacancy(), description="private-payload-sentinel")], 1, query.limit, query.offset)

    provider, cache = Provider(load), SourceSearchCache(clock=clock)
    query = replace(QUERY, keywords="private-query-sentinel")
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        cache.search(provider, query)
        clock.value = 10
        cache.search(provider, query)
    assert "outcome=miss" in caplog.text and "outcome=hit" in caplog.text
    assert "source_duration_ms=8000.0" in caplog.text
    assert "cache_age_ms=2000.0" in caplog.text
    assert "entries=1" in caplog.text
    assert f"accounted_bytes={cache.info().accounted_bytes}" in caplog.text
    assert "private-query-sentinel" not in caplog.text
    assert "private-payload-sentinel" not in caplog.text
