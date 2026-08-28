from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit


class FetchError(Exception):
    code = "fetch_failed"


class BlockedUrlError(FetchError):
    code = "blocked_url"


class FetchTimeoutError(FetchError):
    code = "fetch_timeout"


class UnsupportedContentError(FetchError):
    code = "unsupported_content"


class ResponseTooLargeError(FetchError):
    code = "response_too_large"


class HttpStatusError(FetchError):
    code = "http_error"


@dataclass(frozen=True)
class FetchedPage:
    url: str
    content: str
    content_type: str


Resolver = Callable[[str, int], list[tuple[int, str]]]
FETCH_DEADLINE_SECONDS = 15.0


class SafeHttpFetcher:
    def __init__(self, resolver: Resolver | None = None, *, connect_timeout: float = 3.0, read_timeout: float = 10.0, deadline_seconds: float = FETCH_DEADLINE_SECONDS, max_redirects: int = 3, max_body_bytes: int = 2 * 1024 * 1024, clock: Callable[[], float] = time.monotonic) -> None:
        self._resolver = resolver or _resolve_public_ips
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._deadline_seconds = deadline_seconds
        self._max_redirects = max_redirects
        self._max_body_bytes = max_body_bytes
        self._clock = clock

    def fetch(self, url: str) -> FetchedPage:
        current_url = url
        deadline = self._clock() + self._deadline_seconds
        for redirect_count in range(self._max_redirects + 1):
            self._remaining(deadline)
            parsed = _validate_url(current_url)
            addresses = self._resolver(parsed.hostname or "", parsed.port or _default_port(parsed.scheme))
            self._remaining(deadline)
            if not addresses:
                raise BlockedUrlError("No public address")
            response, sock = self._request(parsed, addresses[0], deadline)
            try:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise HttpStatusError("Redirect without Location")
                    if redirect_count == self._max_redirects:
                        raise HttpStatusError("Too many redirects")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status < 200 or response.status >= 300:
                    raise HttpStatusError(f"HTTP {response.status}")
                content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].lower()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise UnsupportedContentError(content_type)
                body = _read_limited(response, self._max_body_bytes, deadline, self._clock)
                charset = response.headers.get_content_charset() or "utf-8"
                return FetchedPage(current_url, body.decode(charset, errors="replace"), content_type)
            finally:
                sock.close()
        raise HttpStatusError("Too many redirects")

    def _request(self, parsed, address: tuple[int, str], deadline: float) -> tuple[http.client.HTTPResponse, socket.socket]:
        family, ip = address
        port = parsed.port or _default_port(parsed.scheme)
        raw_sock: socket.socket | None = None
        sock: socket.socket | None = None
        try:
            raw_sock = socket.socket(family, socket.SOCK_STREAM)
            raw_sock.settimeout(min(self._connect_timeout, self._remaining(deadline)))
            raw_sock.connect((ip, port) if family == socket.AF_INET else (ip, port, 0, 0))
            raw_sock.settimeout(min(self._read_timeout, self._remaining(deadline)))
            if parsed.scheme == "https":
                context = ssl.create_default_context()
                sock = context.wrap_socket(raw_sock, server_hostname=parsed.hostname)
            else:
                sock = raw_sock
            target = parsed.path or "/"
            if parsed.query:
                target = f"{target}?{parsed.query}"
            host = parsed.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            if parsed.port and parsed.port != _default_port(parsed.scheme):
                host = f"{host}:{parsed.port}"
            request = f"GET {target} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: JobHunterAI/1.0\r\nAccept: text/html,application/xhtml+xml\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
            sock.sendall(request.encode("ascii"))
            response = http.client.HTTPResponse(sock)
            response.begin()
            return response, sock
        except socket.timeout as error:
            _close_sockets(sock, raw_sock)
            raise FetchTimeoutError("Timed out") from error
        except (OSError, ssl.SSLError, http.client.HTTPException, UnicodeError) as error:
            _close_sockets(sock, raw_sock)
            raise FetchError("Connection failed") from error

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise FetchTimeoutError("Fetch deadline exceeded")
        return remaining


def _validate_url(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise BlockedUrlError("Invalid URL")
    try:
        port = parsed.port
    except ValueError as error:
        raise BlockedUrlError("Invalid port") from error
    if port is not None and port not in {80, 443}:
        raise BlockedUrlError("Blocked port")
    return parsed


def _resolve_public_ips(hostname: str, port: int) -> list[tuple[int, str]]:
    try:
        entries = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise FetchError("DNS resolution failed") from error
    addresses: list[tuple[int, str]] = []
    for family, _, _, _, sockaddr in entries:
        ip = sockaddr[0]
        if not ipaddress.ip_address(ip).is_global:
            raise BlockedUrlError("Non-public address")
        item = (family, ip)
        if item not in addresses:
            addresses.append(item)
    return addresses


def _read_limited(response: http.client.HTTPResponse, limit: int, deadline: float, clock: Callable[[], float]) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        if clock() >= deadline:
            raise FetchTimeoutError("Fetch deadline exceeded")
        chunk = response.read(min(65536, limit + 1 - size))
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ResponseTooLargeError("Response body exceeds limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _close_sockets(sock: socket.socket | None, raw_sock: socket.socket | None) -> None:
    if sock is not None:
        sock.close()
    elif raw_sock is not None:
        raw_sock.close()


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80
