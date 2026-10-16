"""Bounded process-local cache of normalized, public source search evidence."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, fields
from threading import Lock

from app.services.source_deadline import DeadlineSearchProvider, SourceDeadline, SourceLoadCancelled
from app.services.vacancy_sources import (
    DiscoverQuery,
    ExternalVacancy,
    SearchPage,
    SourceError,
    VacancySourceProvider,
)


SOURCE_CACHE_TTL_SECONDS = 120.0
SOURCE_CACHE_MAX_ENTRIES = 128
SOURCE_CACHE_MAX_BYTES = 16 * 1024 * 1024
SOURCE_CACHE_MAX_ENTRY_BYTES = 1024 * 1024
SOURCE_CACHE_MAX_INFLIGHT = 16
SOURCE_CACHE_FOLLOWER_WAIT_SECONDS = 25.0

logger = logging.getLogger("uvicorn.error")
CacheKey = tuple[str, DiscoverQuery]


class SourceSearchWaitTimeout(SourceError):
    code = "source_timeout"


@dataclass(frozen=True, slots=True)
class _Page:
    items: tuple[ExternalVacancy, ...]
    total: int
    limit: int
    offset: int

    def public_page(self) -> SearchPage:
        return SearchPage(list(self.items), self.total, self.limit, self.offset)


@dataclass(frozen=True, slots=True)
class _Entry:
    page: _Page
    loaded_at: float
    expires_at: float
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CacheInfo:
    entries: int
    accounted_bytes: int
    inflight: int


def _page_bytes(key: CacheKey, page: _Page) -> int:
    """Conservative accounting for the current flat, immutable DTO fields.

    Count scalar allocations at every occurrence, even when shared. Reserve
    2 KiB per entry and per vacancy for object/dict/container overhead, timezone
    references and allocator padding. This is a retained-data budget, not RSS or
    transient HTTP/JSON memory. Revisit if DTOs gain nested mutable fields.
    """
    source, query = key
    size = 2048 + sys.getsizeof(source) + sys.getsizeof(page.items)
    size += sum(sys.getsizeof(getattr(query, field.name)) for field in fields(DiscoverQuery))
    size += sum(sys.getsizeof(value) for value in (page.total, page.limit, page.offset))
    for vacancy in page.items:
        size += 2048 + sum(
            sys.getsizeof(getattr(vacancy, field.name)) for field in fields(ExternalVacancy)
        )
    return size


class SourceSearchCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = SOURCE_CACHE_TTL_SECONDS,
        max_entries: int = SOURCE_CACHE_MAX_ENTRIES,
        max_bytes: int = SOURCE_CACHE_MAX_BYTES,
        max_entry_bytes: int = SOURCE_CACHE_MAX_ENTRY_BYTES,
        max_inflight: int = SOURCE_CACHE_MAX_INFLIGHT,
        follower_wait_seconds: float = SOURCE_CACHE_FOLLOWER_WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(ttl_seconds, max_entries, max_bytes, max_entry_bytes, max_inflight, follower_wait_seconds) <= 0:
            raise ValueError("Source cache bounds must be positive")
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_entry_bytes = max_entry_bytes
        self._max_inflight = max_inflight
        self._follower_wait = follower_wait_seconds
        self._clock = clock
        self._lock = Lock()
        self._entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self._bytes = 0
        self._inflight: dict[CacheKey, Future[_Page]] = {}
        # One bridge per flight/loop, not per follower. It may outlive its last
        # waiter, but cannot grow with timed-out/cancelled requests on that loop.
        self._async_bridges: dict[Future[_Page], dict[asyncio.AbstractEventLoop, asyncio.Future[_Page]]] = {}

    def info(self) -> CacheInfo:
        with self._lock:
            self._expire(self._clock())
            return CacheInfo(len(self._entries), self._bytes, len(self._inflight))

    def _expire(self, now: float) -> None:
        # Called under lock. TTL order differs from LRU order after hits.
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                self._bytes -= self._entries.pop(key).size_bytes

    def _log(
        self,
        source: str,
        outcome: str,
        *,
        reason: str = "none",
        source_seconds: float | None = None,
        age: float | None = None,
    ) -> None:
        # Logging and handlers run outside the metadata lock. Never log keys,
        # query text, payloads, user IDs or exception messages.
        info = self.info()
        logger.info(
            "Discover source cache source=%s outcome=%s reason=%s "
            "source_duration_ms=%s cache_age_ms=%s entries=%d accounted_bytes=%d",
            source,
            outcome,
            reason,
            "na" if source_seconds is None else f"{source_seconds * 1000:.1f}",
            "na" if age is None else f"{age * 1000:.1f}",
            info.entries,
            info.accounted_bytes,
        )

    def search(self, provider: VacancySourceProvider, query: DiscoverQuery) -> SearchPage:
        # Discover already supplies trimmed keywords and uppercase country.
        # Keep every field exactly as supplied: no casefold, whitespace folding
        # or offset rounding that could hide provider validation errors.
        key = (provider.source_key, query)
        entry, future, owner = self._lookup(key)

        if entry is not None:
            self._log(provider.source_key, "hit", age=self._clock() - entry.loaded_at)
            return entry.page.public_page()
        if future is not None and not owner:
            self._log(provider.source_key, "coalesced")
            try:
                page = future.result(timeout=self._follower_wait)
            except TimeoutError:
                # Future.result also re-raises an owner's TimeoutError. Reading
                # a completed future again preserves that original exception.
                if future.done():
                    return future.result().public_page()
                self._log(provider.source_key, "coalesced", reason="wait-timeout")
                # Keep the live owner's registration until its finally block;
                # removing it here would permit a duplicate source request.
                raise SourceSearchWaitTimeout("Source search wait timed out") from None
            return page.public_page()

        started_at = self._clock()
        try:
            result = provider.search(query)  # HTTP and normalization outside lock.
            loaded_at = self._clock()
            page = _Page(tuple(result.items), result.total, result.limit, result.offset)
            reason = self._store(key, page, loaded_at, future, owner)
            if future is not None:
                future.set_result(page)
            self._log(
                provider.source_key,
                "miss" if reason == "none" else "bypass",
                reason=reason,
                source_seconds=loaded_at - started_at,
            )
            return page.public_page()
        except BaseException as error:
            if future is not None and not future.done():
                future.set_exception(error)
            self._log(
                provider.source_key,
                "miss" if owner else "bypass",
                reason="source-error" if owner else "in-flight-limit",
                source_seconds=self._clock() - started_at,
            )
            raise
        finally:
            if owner:
                self._release(key, future)

    def get_if_fresh(
        self, provider: VacancySourceProvider | DeadlineSearchProvider, query: DiscoverQuery,
    ) -> SearchPage | None:
        """Read only ready evidence; promote a HIT in LRU without sliding TTL."""
        key = (provider.source_key, query)
        with self._lock:
            self._expire(self._clock())
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
        return entry.page.public_page() if entry is not None else None

    async def get_or_load(
        self, provider: DeadlineSearchProvider, query: DiscoverQuery, deadline: SourceDeadline,
    ) -> SearchPage:
        """Opt-in cancellable load, sharing flights and evidence with legacy search."""
        deadline.remaining()
        key = (provider.source_key, query)
        entry, future, owner = self._lookup(key)
        if entry is not None:
            deadline.remaining()
            self._log(provider.source_key, "hit", age=self._clock() - entry.loaded_at)
            deadline.remaining()
            return entry.page.public_page()
        if future is not None and not owner:
            self._log(provider.source_key, "coalesced")
            # Shield the reusable bridge, never the owner's network operation.
            # A timed-out follower must not cancel the shared concurrent Future.
            wrapped = self._shared_bridge(future)
            async with deadline.enforce():
                page = await asyncio.shield(wrapped)
            return page.public_page()

        started_at = self._clock()
        try:
            async with deadline.enforce():
                result = await provider.search_with_deadline(query, deadline)
                deadline.remaining()
                loaded_at = self._clock()
                page = _Page(tuple(result.items), result.total, result.limit, result.offset)
            reason = self._store(key, page, loaded_at, future, owner, deadline)
            if future is not None:
                future.set_result(page)
            self._log(
                provider.source_key, "miss" if reason == "none" else "bypass",
                reason=reason, source_seconds=loaded_at - started_at,
            )
            return page.public_page()
        except BaseException as error:
            if future is not None and not future.done():
                shared_error = SourceLoadCancelled("Source owner cancelled") if isinstance(
                    error, asyncio.CancelledError
                ) else error
                future.set_exception(shared_error)
            self._log(
                provider.source_key, "miss" if owner else "bypass",
                reason="source-error", source_seconds=self._clock() - started_at,
            )
            raise
        finally:
            if owner:
                self._release(key, future)

    def _lookup(self, key: CacheKey) -> tuple[_Entry | None, Future[_Page] | None, bool]:
        with self._lock:
            now = self._clock()
            self._expire(now)
            entry = self._entries.get(key)
            future = self._inflight.get(key)
            if entry is None and future is not None and future.done():
                # A completed flight is not a second cache beyond entry TTL.
                # Existing followers still hold their own reference to it.
                del self._inflight[key]
                future = None
            owner = False
            if entry is not None:
                self._entries.move_to_end(key)
            elif future is None and len(self._inflight) < self._max_inflight:
                future = Future()
                self._inflight[key] = future
                owner = True
        return entry, future, owner

    def _store(
        self, key: CacheKey, page: _Page, loaded_at: float,
        future: Future[_Page] | None, owner: bool, deadline: SourceDeadline | None = None,
    ) -> str:
        if deadline is not None:
            deadline.remaining()
        reason = "in-flight-limit" if future is None else "none"
        if owner:
            size = _page_bytes(key, page)
            if deadline is not None:
                deadline.remaining()
            if size > min(self._max_entry_bytes, self._max_bytes):
                reason = "oversized"
            else:
                with self._lock:
                    if self._inflight.get(key) is future:
                        self._expire(self._clock())
                        if deadline is not None:
                            deadline.remaining()
                        self._entries[key] = _Entry(page, loaded_at, loaded_at + self._ttl, size)
                        self._bytes += size
                        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
                            _, evicted = self._entries.popitem(last=False)
                            self._bytes -= evicted.size_bytes
        return reason

    def _release(self, key: CacheKey, future: Future[_Page] | None) -> None:
        with self._lock:
            if future is not None:
                self._async_bridges.pop(future, None)
            # A newer owner may have replaced this completed flight.
            if self._inflight.get(key) is future:
                del self._inflight[key]

    def _shared_bridge(self, future: Future[_Page]) -> asyncio.Future[_Page]:
        loop = asyncio.get_running_loop()
        with self._lock:
            bridges = self._async_bridges.get(future)
            if bridges is not None and loop in bridges:
                return bridges[loop]
            wrapped: asyncio.Future[_Page] = loop.create_future()
            wrapped.add_done_callback(_consume_future_exception)
            # Owner publication/release may precede this subscription. Never
            # install a completed flight back into the cache's bridge registry.
            if not future.done():
                self._async_bridges.setdefault(future, {})[loop] = wrapped
        # Registration may invoke the callback immediately for an already-done
        # Future. Keep it outside the metadata lock. Explicit delivery also
        # preserves the original TimeoutError (wrap_future copies that type).
        future.add_done_callback(lambda done: _schedule_bridge(loop, wrapped, done))
        return wrapped


def _schedule_bridge(
    loop: asyncio.AbstractEventLoop, bridge: asyncio.Future[_Page], completed: Future[_Page],
) -> None:
    if loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(_deliver_bridge, bridge, completed)
    except RuntimeError:
        if not loop.is_closed():
            raise


def _deliver_bridge(bridge: asyncio.Future[_Page], completed: Future[_Page]) -> None:
    # Called only after the concurrent Future completes, on the bridge's loop.
    # No blocking wait, cross-loop mutation, or cancellation of the owner.
    if bridge.done():
        return
    if completed.cancelled():
        bridge.cancel()
        return
    error = completed.exception()
    if error is not None:
        bridge.set_exception(error)
    else:
        bridge.set_result(completed.result())


def _consume_future_exception(future: asyncio.Future[_Page]) -> None:
    if not future.cancelled():
        future.exception()


class CachedSearchProvider:
    """Cache search only; provider semantics and detail requests pass through."""

    def __init__(self, provider: VacancySourceProvider, cache: SourceSearchCache) -> None:
        self._provider = provider
        self._cache = cache

    @property
    def source_key(self) -> str:
        return self._provider.source_key

    def supports_market(self, market_country: str) -> bool:
        return self._provider.supports_market(market_country)

    def search(self, query: DiscoverQuery) -> SearchPage:
        return self._cache.search(self._provider, query)

    def get_detail(self, source_scope: str, external_id: str) -> ExternalVacancy:
        return self._provider.get_detail(source_scope, external_id)
