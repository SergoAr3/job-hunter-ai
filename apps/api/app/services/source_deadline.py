"""Absolute source budget; cancellation stays on the caller's async task."""

from __future__ import annotations

import math
import time
from asyncio import timeout
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from app.services.vacancy_sources import DiscoverQuery, SearchPage, SourceError


class SourceDeadlineExceeded(SourceError):
    code = "source_timeout"


class SourceLoadCancelled(SourceError):
    """An owning caller cancelled the shared load (not a follower timeout)."""


@dataclass(frozen=True)
class SourceDeadline:
    expires_at: float
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.expires_at):
            raise ValueError("Source deadline must be finite")

    def remaining(self) -> float:
        remaining = self.expires_at - self.clock()
        if remaining <= 0:
            raise SourceDeadlineExceeded("Source deadline exceeded")
        return remaining

    @asynccontextmanager
    async def enforce(self) -> AsyncIterator[None]:
        # One timer surrounds all awaits, including headers, redirects and body.
        # Check the injected clock as well, including after synchronous parsing.
        scope = None
        try:
            async with timeout(self.remaining()) as scope:
                yield
                self.remaining()
        except TimeoutError as error:
            if scope is None or not scope.expired():
                raise
            raise SourceDeadlineExceeded("Source deadline exceeded") from error


class DeadlineSearchProvider(Protocol):
    @property
    def source_key(self) -> str: ...

    async def search_with_deadline(self, query: DiscoverQuery, deadline: SourceDeadline) -> SearchPage: ...
