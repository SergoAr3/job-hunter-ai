"""Real HTTPX/httpcore/AnyIO TLS control flow with TCP I/O replaced, no HTTP."""

import asyncio
import ssl

import pytest
from httpcore import AsyncNetworkBackend, AsyncNetworkStream
from httpcore._backends.anyio import AnyIOStream

import app.services.trudvsem_deadline as source_module
from app.services.source_deadline import SourceDeadlineExceeded, SourceLoadCancelled
from app.services.source_search_cache import SourceSearchCache
from app.services.trudvsem_deadline import TrudvsemDeadlineProvider, _TLSCleanupStream, _TLSCleanupTransport
from source_deadline_helpers import Clock, run
from test_source_deadline_cache import observe_followers
from test_source_search_cache import QUERY


class Wire:
    def __init__(self):
        self.read_entered = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.never = asyncio.Event()
        self.sent_bytes = 0
        self.read_cancelled = False
        self.closed = False
        self.close_calls = 0

    async def send(self, data):
        self.sent_bytes += len(data)

    async def receive(self, *args):
        self.read_entered.set()
        try:
            await self.never.wait()
        except asyncio.CancelledError:
            self.read_cancelled = True
            raise
        return b""

    async def aclose(self):
        self.close_calls += 1
        self.close_entered.set()
        await self.allow_close.wait()
        self.closed = True


class Backend(AsyncNetworkBackend):
    def __init__(self, wire):
        self.wire = wire
        self.connects = 0

    async def connect_tcp(self, *args, **kwargs):
        self.connects += 1
        # This is the installed AnyIOStream.start_tls, including TLSStream.wrap
        # and the real SSL handshake. Only the underlying byte I/O is fake.
        return AnyIOStream(self.wire)


@pytest.mark.parametrize("mode", ["deadline", "cancel", "cancel_twice"])
def test_tls_cancellation_closes_tcp_before_owner_and_follower_finish(monkeypatch, mode):
    clock = Clock(monkeypatch)

    async def scenario():
        initial_tasks = asyncio.all_tasks()
        wire = Wire()
        backend = Backend(wire)
        transport = _TLSCleanupTransport(network_backend=backend)
        # Exercise the production default selection, not an alternative provider.
        monkeypatch.setattr(source_module, "_TLSCleanupTransport", lambda: transport)
        provider, cache = TrudvsemDeadlineProvider(), SourceSearchCache(clock=clock)
        joined = observe_followers(monkeypatch)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(2)))
        await wire.read_entered.wait()
        follower = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(20)))
        await joined.wait()
        if mode == "deadline":
            clock.advance(2)
            owner_error = follower_error = SourceDeadlineExceeded
        else:
            owner.cancel()
            owner_error, follower_error = asyncio.CancelledError, SourceLoadCancelled
        await wire.close_entered.wait()
        assert not owner.done() and not follower.done() and not wire.closed
        if mode == "cancel_twice":
            owner.cancel()  # Must not detach or cancel the owned close task.
        wire.allow_close.set()
        with pytest.raises(owner_error):
            await owner
        with pytest.raises(follower_error):
            await follower
        assert wire.sent_bytes > 0  # A genuine TLS ClientHello was generated.
        assert wire.read_cancelled and wire.closed and wire.close_calls == 1
        assert backend.connects == 1 and not transport._pool.connections
        assert cache.info().entries == cache.info().inflight == 0
        assert cache._async_bridges == {}
        assert asyncio.all_tasks() == initial_tasks

    run(scenario())


def test_successful_tls_upgrade_transfers_ownership_without_early_close():
    class TLS(AsyncNetworkStream):
        closes = 0

        async def aclose(self):
            self.closes += 1

    class TCP(AsyncNetworkStream):
        closes = 0

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            return tls

        async def aclose(self):
            self.closes += 1

    tcp, tls = TCP(), TLS()

    async def scenario():
        upgraded = await _TLSCleanupStream(tcp).start_tls(ssl.create_default_context(), "opendata.trudvsem.ru", 1)
        assert upgraded is tls and tcp.closes == tls.closes == 0
        await upgraded.aclose()
        assert tls.closes == 1 and tcp.closes == 0

    run(scenario())


def test_failed_upgrade_preserves_original_error_and_closes_tcp():
    error = ssl.SSLError("handshake failed")

    class TCP(AsyncNetworkStream):
        closed = False

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            raise error

        async def aclose(self):
            self.closed = True

    tcp = TCP()

    async def scenario():
        with pytest.raises(ssl.SSLError) as caught:
            await _TLSCleanupStream(tcp).start_tls(ssl.create_default_context())
        assert caught.value is error and tcp.closed

    run(scenario())
