import asyncio
from dataclasses import replace

import pytest

from app.services.discover_feed_scan import FeedScanner, ScanStats
from app.services.discover_feed_types import Checkpoint, Evidence, FeedIdentity
from app.services.source_deadline import SourceDeadlineExceeded
from app.services.source_search_cache import SourceSearchCache
from app.services.vacancy_sources import DiscoverQuery, SearchPage, SourceError
from source_deadline_helpers import Clock, run
from test_discover_api import vacancy


IDENTITY = FeedIdentity(query="Backend", remote_only=True)


def page(offset=0, *, remote=(), total=100, count=10):
    return SearchPage([vacancy(str(offset + i), workplace_type="remote" if i in remote else "unknown")
                       for i in range(count)], total, 10, offset)


class Provider:
    source_key = "trudvsem"

    def __init__(self, pages=None, *, error=None):
        self.pages = pages or {0: page(remote=range(10), total=10)}
        self.calls = []
        self.error = error
        self.entered = asyncio.Event()
        self.release = None
        self.deadlines = []

    async def search_with_deadline(self, query, deadline):
        self.calls.append(query.offset)
        self.deadlines.append(deadline)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error:
            raise self.error
        return self.pages[query.offset]


class Cache(SourceSearchCache):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fresh_reads, self.cold_loads = [], []

    def get_if_fresh(self, provider, query):
        self.fresh_reads.append(query.offset)
        return super().get_if_fresh(provider, query)

    async def get_or_load(self, provider, query, deadline):
        self.cold_loads.append(query.offset)
        return await super().get_or_load(provider, query, deadline)

    def seed(self, source_page):
        class Legacy:
            source_key = "trudvsem"
            def search(self, query):
                return source_page
        self.search(Legacy(), DiscoverQuery("Backend", "RU", None, 10, source_page.offset))


def scan(cache, provider, *, identity=IDENTITY, checkpoint=None, clock=None):
    stats = ScanStats()
    scanner = FeedScanner(cache, provider, **({"clock": clock} if clock else {}))
    result = run(scanner.scan(identity, checkpoint or Checkpoint(), stats))
    assert len(cache.cold_loads) <= 1
    return result, stats


@pytest.mark.parametrize("cached", [True, False])
def test_first_full_page_from_cache_or_one_cold(cached):
    cache, provider = Cache(), Provider()
    if cached:
        cache.seed(provider.pages[0])
    result, stats = scan(cache, provider)
    assert len(result.items) == 10 and result.checkpoint.exhausted
    assert cache.fresh_reads == [0] and cache.cold_loads == ([] if cached else [0])
    assert provider.calls == ([] if cached else [0]) and stats.pages == 1


def test_cached_before_and_after_cold_then_stop_at_second_miss():
    cache, provider = Cache(), Provider({10: page(10, remote=[0])})
    cache.seed(page(0, remote=[0]))
    cache.seed(page(20, remote=[0]))
    result, stats = scan(cache, provider)
    assert [item.external_id for item in result.items] == ["0", "10", "20"]
    assert result.checkpoint.offset == 30 and not result.checkpoint.exhausted
    assert cache.fresh_reads == [0, 10, 20, 30]
    assert cache.cold_loads == provider.calls == [10]
    assert (stats.pages, stats.records, stats.hits, stats.cold) == (3, 30, 2, True)


def test_four_page_record_budget_stops_before_fifth_even_when_fresh():
    cache, provider = Cache(), Provider()
    for offset in range(0, 50, 10):
        cache.seed(page(offset))
    result, stats = scan(cache, provider)
    assert result.items == [] and result.checkpoint.offset == 40
    assert (stats.pages, stats.records) == (4, 40)
    assert cache.fresh_reads == [0, 10, 20, 30] and cache.cold_loads == provider.calls == []


def test_record_budget_counts_remainder_and_keeps_unexamined_source_tail():
    cache, provider = Cache(), Provider()
    for offset in range(10, 50, 10):
        cache.seed(page(offset))
    remainder = [Evidence.model_validate(vacancy(f"old-{i}")) for i in range(5)]
    result, stats = scan(cache, provider, checkpoint=Checkpoint(offset=10, remainder=remainder))
    assert stats.records == 40 and stats.pages == 4
    assert result.checkpoint.offset == 50 and len(result.checkpoint.remainder) == 5
    assert result.checkpoint.remainder[0].external_id == "45"
    assert cache.fresh_reads == [10, 20, 30, 40] and cache.cold_loads == provider.calls == []


def test_remainder_keeps_order_and_next_consumes_without_source():
    cache, provider = Cache(), Provider()
    cache.seed(page(remote=range(10), total=10))
    result, _ = scan(cache, provider, identity=IDENTITY.model_copy(update={"limit": 2}))
    assert [x.external_id for x in result.items] == ["0", "1"]
    assert [x.external_id for x in result.checkpoint.remainder] == list(map(str, range(2, 10)))
    cache.fresh_reads.clear()
    second, _ = scan(cache, provider, identity=IDENTITY.model_copy(update={"limit": 2}), checkpoint=result.checkpoint)
    assert [x.external_id for x in second.items] == ["2", "3"]
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []
    assert [x.external_id for x in result.checkpoint.remainder] == list(map(str, range(2, 10)))


def test_dedupe_includes_remainder_and_new_source_page():
    duplicate = vacancy("same", workplace_type="remote")
    first = page(remote=[], count=1)
    cache, provider = Cache(), Provider({10: replace(first, offset=10, total=20, items=[duplicate, duplicate])})
    checkpoint = Checkpoint(offset=10, remainder=[Evidence.model_validate(duplicate)])
    result, stats = scan(cache, provider, checkpoint=checkpoint)
    assert [x.external_id for x in result.items] == ["same"]
    assert stats.records == 3 and cache.fresh_reads == cache.cold_loads == provider.calls == [10]


@pytest.mark.parametrize("remote", [[], [2, 5]])
def test_terminal_page_can_be_empty_remote_or_sparse(remote):
    cache, provider = Cache(), Provider({0: page(remote=remote, total=10)})
    result, _ = scan(cache, provider)
    assert len(result.items) == len(remote) and result.checkpoint.exhausted
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


@pytest.mark.parametrize("error", [SourceDeadlineExceeded(), SourceError(), TimeoutError()])
def test_cold_failure_discards_partial_result_and_never_retries(error):
    cache, provider = Cache(), Provider(error=error)
    cache.seed(page(remote=[0]))
    stats = ScanStats()
    with pytest.raises(type(error)):
        run(FeedScanner(cache, provider).scan(IDENTITY, Checkpoint(), stats))
    assert stats.records == 10 and cache.fresh_reads == [0, 10]
    assert cache.cold_loads == provider.calls == [10]


@pytest.mark.parametrize("bad", [page(count=0), replace(page(), limit=20), replace(page(), offset=5), page(count=11)])
def test_malformed_source_is_error(bad):
    cache, provider = Cache(), Provider({0: bad})
    with pytest.raises(SourceError):
        scan(cache, provider)
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


def test_coalesced_wait_consumes_only_cold_slot(monkeypatch):
    clock = Clock(monkeypatch)
    async def scenario():
        cache, provider = Cache(clock=clock), Provider({0: page(remote=[0])})
        provider.release = asyncio.Event()
        query = DiscoverQuery("Backend", "RU", None, 10, 0)
        owner = asyncio.create_task(SourceSearchCache.get_or_load(cache, provider, query, clock.deadline(30)))
        await provider.entered.wait()
        joined = asyncio.Event()
        original = cache._shared_bridge
        def observed(future):
            result = original(future)
            joined.set()
            return result
        monkeypatch.setattr(cache, "_shared_bridge", observed)
        stats = ScanStats()
        task = asyncio.create_task(FeedScanner(cache, provider, clock=clock).scan(IDENTITY, Checkpoint(), stats))
        await joined.wait()
        provider.release.set()
        result = await task
        await owner
        assert len(result.items) == 1 and stats.cold
        assert cache.fresh_reads == [0, 10] and cache.cold_loads == provider.calls == [0]
    run(scenario())


def test_cold_deadline_is_capped_by_remaining_total_budget(monkeypatch):
    clock = Clock(monkeypatch)
    cache, provider = Cache(clock=clock), Provider({10: page(10, total=20)})
    cache.seed(page())
    original = cache.get_if_fresh
    def advancing(source, query):
        result = original(source, query)
        if query.offset == 0:
            clock.advance(5)
        return result
    monkeypatch.setattr(cache, "get_if_fresh", advancing)
    result, _ = scan(cache, provider, clock=clock)
    assert result.checkpoint.exhausted
    assert provider.deadlines[0].expires_at == 115
    assert cache.fresh_reads == [0, 10] and cache.cold_loads == provider.calls == [10]


def test_total_budget_stop_between_cached_pages_is_not_timeout(monkeypatch):
    clock = Clock(monkeypatch)
    cache, provider = Cache(clock=clock), Provider()
    cache.seed(page())
    original = cache.get_if_fresh
    def advancing(source, query):
        result = original(source, query)
        clock.advance(15)
        return result
    monkeypatch.setattr(cache, "get_if_fresh", advancing)
    result, _ = scan(cache, provider, clock=clock)
    assert result.items == [] and not result.checkpoint.exhausted
    assert cache.fresh_reads == [0] and cache.cold_loads == provider.calls == []
