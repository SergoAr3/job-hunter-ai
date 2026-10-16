"""Opt-in async Trudvsem search; legacy urllib search/detail stay synchronous."""

from __future__ import annotations

import asyncio
import ssl

import httpx
from httpcore import AsyncNetworkBackend, AsyncNetworkStream

from app.services.source_deadline import SourceDeadline
from app.services.trudvsem import (
    BASE_URL,
    MAX_RESPONSE_BYTES,
    TrudvsemBadResponseError,
    TrudvsemError,
    TrudvsemRateLimitError,
    TrudvsemTimeoutError,
    _decode_response,
    _search_page,
    _search_url,
)
from app.services.vacancy_sources import DiscoverQuery, SearchPage


REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECT_DESTINATIONS = 10
MAX_REDIRECT_REPEATS = 4
READ_CHUNK_BYTES = 64 * 1024


class TrudvsemDeadlineProvider:
    source_key = "trudvsem"

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def search_with_deadline(self, query: DiscoverQuery, deadline: SourceDeadline) -> SearchPage:
        deadline.remaining()
        if query.market_country != "RU":
            raise ValueError("Unsupported search market")
        url = _source_endpoint(_search_url(
            query.keywords, limit=query.limit, offset=query.offset, region_code=query.market_region,
        ))
        try:
            async with deadline.enforce():
                # Per-operation ownership: cancellation closes the response and
                # connection pool before returning. No worker thread/background
                # task owns an HTTP operation after the caller has left.
                transport = self._transport if self._transport is not None else _TLSCleanupTransport()
                async with httpx.AsyncClient(
                    transport=transport, follow_redirects=False, trust_env=False,
                    headers={"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "Veylinq/1.0"},
                ) as client:
                    payload = await self._search_json(client, url, deadline)
                page = _search_page(payload, limit=query.limit, offset=query.offset)
                deadline.remaining()
                return page
        except httpx.TimeoutException as error:
            raise TrudvsemTimeoutError("Source request timed out") from error
        except httpx.DecodingError as error:
            raise TrudvsemBadResponseError("Source returned invalid encoding") from error
        except httpx.HTTPError as error:
            raise TrudvsemError("Source request failed") from error

    async def _search_json(
        self, client: httpx.AsyncClient, url: httpx.URL, deadline: SourceDeadline,
    ) -> dict[str, object]:
        visited: dict[httpx.URL, int] = {}
        while True:
            async with client.stream("GET", url, timeout=deadline.remaining()) as response:
                deadline.remaining()
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location") or response.headers.get("uri")
                    if location is None:
                        raise TrudvsemError("Source redirect has no destination")
                    try:
                        destination = _source_endpoint(str(url.join(location)))
                    except httpx.InvalidURL as error:
                        raise TrudvsemError("Source redirect is invalid") from error
                    # Match urllib's counters: distinct destinations, not hops.
                    if len(visited) >= MAX_REDIRECT_DESTINATIONS or visited.get(destination, 0) >= MAX_REDIRECT_REPEATS:
                        raise TrudvsemError("Source redirect limit exceeded")
                    visited[destination] = visited.get(destination, 0) + 1
                    url = destination
                    # Close redirect bodies without unbounded draining. The same
                    # deadline includes closing and every subsequent request.
                    continue
                if response.status_code == 429:
                    raise TrudvsemRateLimitError("Source rate limited the request")
                if not 200 <= response.status_code < 300:
                    raise TrudvsemError(f"Source returned HTTP {response.status_code}")
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        declared_size = int(length)
                    except ValueError:
                        declared_size = None
                    if declared_size is not None and declared_size > MAX_RESPONSE_BYTES:
                        raise TrudvsemBadResponseError("Source response is too large")
                # urllib requests identity and does not decode compressed bodies.
                # Preserve that contract and read raw bytes: HTTPX's automatic
                # decompression must not allocate an unbounded decoded chunk.
                if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                    raise TrudvsemBadResponseError("Unexpected source content encoding")
                body = bytearray()
                async for chunk in response.aiter_raw(chunk_size=READ_CHUNK_BYTES):
                    deadline.remaining()
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise TrudvsemBadResponseError("Source response is too large")
                    body.extend(chunk)
                deadline.remaining()
                return _decode_response(response.status_code, bytes(body))


class _TLSCleanupTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, network_backend: AsyncNetworkBackend | None = None) -> None:
        super().__init__(retries=0, trust_env=False)
        # HTTPX exposes no backend constructor argument. Keep this integration
        # with its httpcore pool local; do not alter installed/global backends.
        backend = network_backend if network_backend is not None else self._pool._network_backend
        self._pool._network_backend = _TLSCleanupBackend(backend)


class _TLSCleanupBackend(AsyncNetworkBackend):
    def __init__(self, backend: AsyncNetworkBackend) -> None:
        self._backend = backend

    async def connect_tcp(self, *args, **kwargs) -> AsyncNetworkStream:
        return _TLSCleanupStream(await self._backend.connect_tcp(*args, **kwargs))

    async def connect_unix_socket(self, *args, **kwargs) -> AsyncNetworkStream:
        return _TLSCleanupStream(await self._backend.connect_unix_socket(*args, **kwargs))

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _TLSCleanupStream(AsyncNetworkStream):
    def __init__(self, stream: AsyncNetworkStream) -> None:
        self._stream = stream

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self._stream.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._stream.write(buffer, timeout)

    async def aclose(self) -> None:
        await self._stream.aclose()

    def get_extra_info(self, info: str):
        return self._stream.get_extra_info(info)

    async def start_tls(
        self, ssl_context: ssl.SSLContext, server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> AsyncNetworkStream:
        try:
            # Success transfers ownership to the returned TLS stream/httpcore.
            return await self._stream.start_tls(ssl_context, server_hostname, timeout)
        except BaseException as error:
            # The pool does not yet own an HTTP connection during handshake.
            # Installed AnyIO/httpcore misses CancelledError cleanup here.
            try:
                await _close_failed_tls(self._stream)
            except BaseException as close_error:
                error.add_note(f"TLS stream cleanup failed ({type(close_error).__name__})")
            raise


async def _close_failed_tls(stream: AsyncNetworkStream) -> None:
    # Join close even if another cancellation arrives while it yields. This is
    # an owned cleanup task, never a detached HTTP operation or follower task.
    close = asyncio.create_task(stream.aclose())
    while True:
        try:
            await asyncio.shield(close)
            return
        except asyncio.CancelledError:
            if close.done():
                close.result()
                return


def _source_endpoint(value: str) -> httpx.URL:
    url = httpx.URL(value)
    base = httpx.URL(BASE_URL)
    if (
        url.scheme != "https" or url.host != base.host or url.port not in (None, 443)
        or url.username or url.password or url.fragment
        or not (url.path == base.path or url.path.startswith(base.path + "/"))
        or "\\" in url.path or any(part in {".", ".."} for part in url.path.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in url.path)
    ):
        raise TrudvsemError("Source redirect destination is not allowed")
    return url
