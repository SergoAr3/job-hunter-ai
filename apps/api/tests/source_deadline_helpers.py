"""Deterministic clock-driven asyncio cancellation; no wall-clock sleeps."""

import asyncio
from contextlib import asynccontextmanager

import app.services.source_deadline as deadline_module
from app.services.source_deadline import SourceDeadline


class Clock:
    def __init__(self, monkeypatch):
        self.value = 100.0
        self.timers = []
        monkeypatch.setattr(deadline_module, "timeout", self.timeout)

    def __call__(self):
        return self.value

    def deadline(self, seconds=10):
        return SourceDeadline(self.value + seconds, self)

    @asynccontextmanager
    async def timeout(self, seconds):
        async with asyncio.timeout(None) as timer:
            registration = (self.value + seconds, timer)
            self.timers.append(registration)
            try:
                yield timer
            finally:
                self.timers.remove(registration)

    def advance(self, seconds):
        self.value += seconds
        for expires_at, timer in self.timers:
            if expires_at <= self.value:
                timer.reschedule(asyncio.get_running_loop().time() - 1)


def run(coroutine):
    async def bounded():
        return await asyncio.wait_for(coroutine, timeout=5)

    return asyncio.run(bounded())
