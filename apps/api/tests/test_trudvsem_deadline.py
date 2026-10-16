import asyncio
import gzip
from dataclasses import replace

import httpx
import pytest

import app.services.trudvsem_deadline as source_module
from app.services.source_deadline import SourceDeadlineExceeded
from app.services.source_search_cache import SourceSearchCache
from app.services.trudvsem import (
    MAX_RESPONSE_BYTES,
    TrudvsemBadResponseError,
    TrudvsemError,
    TrudvsemRateLimitError,
    TrudvsemTimeoutError,
)
from app.services.trudvsem_deadline import TrudvsemDeadlineProvider
from source_deadline_helpers import Clock, run
from test_source_search_cache import QUERY
from test_trudvsem_client import response, vacancy_payload


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, before_chunk=None):
        self.chunks = chunks
        self.before_chunk = before_chunk
        self.reads = 0
        self.closed = False
        self.cancelled = False

    async def __aiter__(self):
        try:
            for chunk in self.chunks:
                if self.before_chunk:
                    await self.before_chunk(self.reads)
                self.reads += 1
                yield chunk
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def aclose(self):
        self.closed = True


class Transport(httpx.MockTransport):
    closed = False

    async def aclose(self):
        self.closed = True


def reply(body=None, *, status=200, headers=None, stream=None):
    if body is None:
        body = response(vacancy_payload(), total=25)
    return httpx.Response(status, headers=headers, stream=stream or Stream([body]))


def test_deadline_provider_maps_same_query_page_and_evidence(monkeypatch):
    clock, calls, stream = Clock(monkeypatch), [], Stream([response(vacancy_payload(), total=25)])

    def handler(request):
        calls.append(request)
        return reply(stream=stream)

    transport = Transport(handler)

    async def scenario():
        provider = TrudvsemDeadlineProvider(transport=transport)
        query = replace(QUERY, offset=10, market_region="7700000000000")
        page = await provider.search_with_deadline(query, clock.deadline())
        assert (page.total, page.limit, page.offset) == (25, 10, 10)
        assert page.items[0].workplace_type == "remote"

    run(scenario())
    assert len(calls) == 1
    assert dict(calls[0].url.params) == {"text": "Backend", "limit": "10", "offset": "1"}
    assert calls[0].url.path.endswith("/region/7700000000000")
    assert calls[0].headers["accept-encoding"] == "identity"
    assert calls[0].extensions["timeout"]["read"] == 10
    assert stream.closed and transport.closed


@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_blocked_network_is_cancelled_and_closed_before_owner_returns(monkeypatch, phase, stop):
    clock = Clock(monkeypatch)

    async def scenario():
        entered, never = asyncio.Event(), asyncio.Event()
        header_cancelled = False

        async def before_chunk(_index):
            entered.set()
            await never.wait()

        stream = Stream([b"{}"], before_chunk if phase == "body" else None)

        async def handler(request):
            nonlocal header_cancelled
            if phase == "headers":
                entered.set()
                try:
                    await never.wait()
                except asyncio.CancelledError:
                    header_cancelled = True
                    raise
            return reply(stream=stream)

        transport = Transport(handler)
        provider, cache = TrudvsemDeadlineProvider(transport=transport), SourceSearchCache(clock=clock)
        owner = asyncio.create_task(cache.get_or_load(provider, QUERY, clock.deadline(2)))
        await entered.wait()
        if stop == "deadline":
            clock.advance(2)
            error = SourceDeadlineExceeded
        else:
            owner.cancel()
            error = asyncio.CancelledError
        with pytest.raises(error):
            await owner
        assert transport.closed
        assert header_cancelled if phase == "headers" else stream.cancelled and stream.closed
        assert cache.info().entries == cache.info().inflight == 0
        # No spawned task is still driving the transport after owner completion.
        assert never.is_set() is False

    run(scenario())


def test_redirects_and_body_share_one_absolute_deadline(monkeypatch):
    clock, timeouts, streams = Clock(monkeypatch), [], []

    async def scenario():
        entered, never = asyncio.Event(), asyncio.Event()

        async def block(_index):
            entered.set()
            await never.wait()

        def handler(request):
            timeouts.append(request.extensions["timeout"]["read"])
            if len(timeouts) <= 2:
                clock.advance(3)
                stream = Stream([b"redirect body must not be drained"])
                streams.append(stream)
                return reply(status=302, headers={"location": f"?step={len(timeouts)}"}, stream=stream)
            stream = Stream([b"{}"], block)
            streams.append(stream)
            return reply(stream=stream)

        provider = TrudvsemDeadlineProvider(transport=Transport(handler))
        task = asyncio.create_task(provider.search_with_deadline(QUERY, clock.deadline(10)))
        await entered.wait()
        assert timeouts == [10, 7, 4]
        clock.advance(4)
        with pytest.raises(SourceDeadlineExceeded):
            await task

    run(scenario())
    assert all(stream.closed for stream in streams)
    assert [stream.reads for stream in streams[:2]] == [0, 0]


@pytest.mark.parametrize("destination", [
    "http://opendata.trudvsem.ru/api/v1/vacancies",
    "https://evil.test/api/v1/vacancies",
    "https://127.0.0.1/api/v1/vacancies",
    "https://opendata.trudvsem.ru:444/api/v1/vacancies",
    "https://user:password@opendata.trudvsem.ru/api/v1/vacancies",
    "https://opendata.trudvsem.ru/unrelated",
    "https://opendata.trudvsem.ru/api/v1/vacancies/%2e%2e/unrelated",
])
def test_redirect_destination_is_validated_before_following(monkeypatch, destination):
    clock, calls = Clock(monkeypatch), []

    def handler(request):
        calls.append(request)
        return reply(status=302, headers={"location": destination})

    async def scenario():
        with pytest.raises(TrudvsemError):
            await TrudvsemDeadlineProvider(transport=Transport(handler)).search_with_deadline(QUERY, clock.deadline())

    run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("unique", [False, True])
def test_redirect_loops_are_bounded(monkeypatch, unique):
    clock, calls = Clock(monkeypatch), []

    def handler(request):
        calls.append(request)
        return reply(status=302, headers={"location": f"?n={len(calls) if unique else 1}"})

    async def scenario():
        with pytest.raises(TrudvsemError, match="limit"):
            await TrudvsemDeadlineProvider(transport=Transport(handler)).search_with_deadline(QUERY, clock.deadline())

    run(scenario())
    assert len(calls) == (11 if unique else 5)


@pytest.mark.parametrize("headers", [
    {"content-length": str(MAX_RESPONSE_BYTES + 1)},
    {"content-encoding": "gzip"},
])
def test_oversized_declared_or_compressed_body_is_rejected_before_read(monkeypatch, headers):
    clock = Clock(monkeypatch)
    stream = Stream([gzip.compress(b"x" * (MAX_RESPONSE_BYTES + 1))])

    async def scenario():
        with pytest.raises(TrudvsemBadResponseError):
            provider = TrudvsemDeadlineProvider(transport=Transport(lambda _: reply(headers=headers, stream=stream)))
            await provider.search_with_deadline(QUERY, clock.deadline())

    run(scenario())
    assert stream.reads == 0 and stream.closed


@pytest.mark.parametrize("length", [None, "1", "not-an-int"])
def test_streamed_body_limit_does_not_trust_content_length(monkeypatch, length):
    clock = Clock(monkeypatch)
    monkeypatch.setattr(source_module, "MAX_RESPONSE_BYTES", 2 * source_module.READ_CHUNK_BYTES)
    stream = Stream([b"x" * source_module.READ_CHUNK_BYTES] * 5)
    headers = {} if length is None else {"content-length": length}

    async def scenario():
        with pytest.raises(TrudvsemBadResponseError, match="too large"):
            provider = TrudvsemDeadlineProvider(transport=Transport(lambda _: reply(headers=headers, stream=stream)))
            await provider.search_with_deadline(QUERY, clock.deadline())

    run(scenario())
    assert stream.reads == 3 and stream.closed


def test_body_exactly_eight_mib_is_accepted(monkeypatch):
    clock = Clock(monkeypatch)
    body = response(total=0)
    stream = Stream([body, b" " * (MAX_RESPONSE_BYTES - len(body))])

    async def scenario():
        provider = TrudvsemDeadlineProvider(transport=Transport(lambda _: reply(stream=stream)))
        page = await provider.search_with_deadline(QUERY, clock.deadline())
        assert page.items == [] and page.total == 0

    run(scenario())
    assert stream.closed


@pytest.mark.parametrize(("status", "body", "error"), [
    (429, b"{}", TrudvsemRateLimitError),
    (503, b"{}", TrudvsemError),
    (200, b"invalid-json", TrudvsemBadResponseError),
    (200, b"\xff", TrudvsemBadResponseError),
    (200, b"[]", TrudvsemBadResponseError),
    (200, b'{"status":"500"}', TrudvsemBadResponseError),
    (200, b'{"status":"200","meta":{"total":12},"results":{}}', TrudvsemBadResponseError),
])
def test_source_errors_are_not_cached_or_retried(monkeypatch, status, body, error):
    clock, calls = Clock(monkeypatch), []

    def handler(request):
        calls.append(request)
        return reply(body, status=status)

    async def scenario():
        provider, cache = TrudvsemDeadlineProvider(transport=Transport(handler)), SourceSearchCache(clock=clock)
        with pytest.raises(error):
            await cache.get_or_load(provider, QUERY, clock.deadline())
        assert cache.info().entries == cache.info().inflight == 0

    run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize(("failure", "error"), [
    (httpx.ConnectTimeout("connect"), TrudvsemTimeoutError),
    (httpx.ReadTimeout("read"), TrudvsemTimeoutError),
    (httpx.ConnectError("network"), TrudvsemError),
    (httpx.RemoteProtocolError("invalid response"), TrudvsemError),
])
def test_transport_errors_close_client_and_keep_source_mapping(monkeypatch, failure, error):
    clock = Clock(monkeypatch)

    def handler(_request):
        raise failure

    transport = Transport(handler)

    async def scenario():
        with pytest.raises(error):
            await TrudvsemDeadlineProvider(transport=transport).search_with_deadline(QUERY, clock.deadline())

    run(scenario())
    assert transport.closed


@pytest.mark.parametrize("query", [replace(QUERY, offset=1), replace(QUERY, market_country="AM")])
def test_invalid_search_never_starts_transport(monkeypatch, query):
    clock, calls = Clock(monkeypatch), []

    def handler(request):
        calls.append(request)
        return reply()

    async def scenario():
        with pytest.raises(ValueError):
            await TrudvsemDeadlineProvider(transport=Transport(handler)).search_with_deadline(query, clock.deadline())

    run(scenario())
    assert calls == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_supported_redirect_codes_and_uri_header(monkeypatch, status):
    clock, calls = Clock(monkeypatch), []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return reply(status=status, headers={"uri": "?redirected=1"})
        return reply()

    async def scenario():
        page = await TrudvsemDeadlineProvider(transport=Transport(handler)).search_with_deadline(QUERY, clock.deadline())
        assert len(page.items) == 1

    run(scenario())
    assert len(calls) == 2 and all(call.method == "GET" for call in calls)


def test_absolute_deadline_is_checked_across_body_chunks(monkeypatch):
    clock = Clock(monkeypatch)

    async def before_chunk(_index):
        clock.advance(3)

    stream = Stream([b" " * source_module.READ_CHUNK_BYTES] * 4, before_chunk)

    async def scenario():
        provider = TrudvsemDeadlineProvider(transport=Transport(lambda _: reply(stream=stream)))
        with pytest.raises(SourceDeadlineExceeded):
            await provider.search_with_deadline(QUERY, clock.deadline(5))

    run(scenario())
    assert stream.closed and stream.reads == 2


def test_redirect_limit_counts_distinct_destinations_like_urllib(monkeypatch):
    clock, calls = Clock(monkeypatch), []

    def handler(request):
        calls.append(request)
        if len(calls) <= 12:
            return reply(status=302, headers={"location": f"?n={len(calls) % 4}"})
        return reply()

    async def scenario():
        provider = TrudvsemDeadlineProvider(transport=Transport(handler))
        assert len((await provider.search_with_deadline(QUERY, clock.deadline())).items) == 1

    run(scenario())
    assert len(calls) == 13  # Four destinations, three visits each: urllib permits this.
