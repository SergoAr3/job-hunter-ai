"""One action: remainder, fresh evidence, at most one latency-bearing load."""
from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable

from pydantic import ValidationError

from app.services.discover_feed_types import (
    COLD_SECONDS, MAX_RECORDS, MAX_SOURCE_PAGES, SCAN_SECONDS, SOURCE_PAGE_SIZE,
    Checkpoint, Evidence, FeedIdentity, StoredFeed,
)
from app.services.source_deadline import DeadlineSearchProvider, SourceDeadline, SourceDeadlineExceeded
from app.services.source_search_cache import SourceSearchCache
from app.services.trudvsem import TrudvsemBadResponseError
from app.services.vacancy_sources import DiscoverQuery


@dataclass
class ScanStats:
    pages: int = 0
    records: int = 0
    hits: int = 0
    cold: bool = False


class FeedScanner:
    def __init__(self, cache: SourceSearchCache, provider: DeadlineSearchProvider, *, clock: Callable[[], float] = time.monotonic):
        self.cache, self.provider, self.clock = cache, provider, clock

    async def scan(self, identity: FeedIdentity, checkpoint: Checkpoint, stats: ScanStats) -> StoredFeed:
        deadline = SourceDeadline(self.clock() + SCAN_SECONDS, self.clock)
        position = checkpoint.model_copy(deep=True)
        visible: list[Evidence] = []
        seen: set[tuple[str, str, str]] = set()
        while len(visible) < identity.limit and stats.records < MAX_RECORDS:
            # Normal budget exhaustion between operations is a successful stop;
            # deadline expiry *inside* source access remains a whole-action error.
            try:
                deadline.remaining()
            except SourceDeadlineExceeded:
                break
            if not position.remainder:
                if position.exhausted or stats.pages >= MAX_SOURCE_PAGES:
                    break
                query = DiscoverQuery(identity.query, identity.market_country, identity.market_region, SOURCE_PAGE_SIZE, position.offset)
                page = self.cache.get_if_fresh(self.provider, query)
                if page is None:
                    if stats.cold:
                        break
                    # Reserve before get_or_load: joining another owner is cold too.
                    stats.cold = True
                    cold_deadline = SourceDeadline(min(deadline.expires_at, self.clock() + COLD_SECONDS), self.clock)
                    page = await self.cache.get_or_load(self.provider, query, cold_deadline)
                else:
                    stats.hits += 1
                stats.pages += 1
                if (page.limit != SOURCE_PAGE_SIZE or page.offset != position.offset
                        or type(page.total) is not int or page.total < 0
                        or len(page.items) > SOURCE_PAGE_SIZE or (page.offset >= page.total and page.items)):
                    raise TrudvsemBadResponseError("Invalid source pagination")
                try:
                    position.remainder = [Evidence.model_validate(item) for item in page.items]
                except ValidationError as error:
                    raise TrudvsemBadResponseError("Invalid normalized source evidence") from error
                position.offset += SOURCE_PAGE_SIZE
                position.exhausted = page.offset + page.limit >= page.total
                if not page.items and not position.exhausted:
                    raise TrudvsemBadResponseError("Empty non-terminal source page")
            while position.remainder and len(visible) < identity.limit and stats.records < MAX_RECORDS:
                item = position.remainder.pop(0)
                stats.records += 1
                key = (item.source, item.source_scope, item.external_id)
                if key in seen:
                    continue
                seen.add(key)
                if item.workplace_type == "remote":
                    visible.append(item)
        return StoredFeed(checkpoint=position, items=visible)
