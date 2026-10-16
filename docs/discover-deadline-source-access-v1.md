# Discover Deadline-aware Source Access v1

Internal foundation only. Production Discover still calls synchronous
`CachedSearchProvider.search()` / `TrudvsemClient` with the existing urllib
transport and 25-second socket timeout. Public DTOs, pagination, remote filtering,
Web, Bot, matching, Application state and database schema are unchanged.
No continuation/feed endpoint or filled remote page is implemented.

## Opt-in API

`SourceSearchCache.get_if_fresh(provider, query)` returns a fresh `SearchPage` or
`None`. It never calls a provider, waits for a flight, creates a flight, or reuses
a completed Future instead of fresh evidence. A HIT promotes LRU recency, exactly
like `search()`, but never extends TTL. Every result has its own items list.

`await cache.get_or_load(provider, query, deadline)` accepts a
`DeadlineSearchProvider`, currently `TrudvsemDeadlineProvider`, and a
`SourceDeadline(expires_at, clock=time.monotonic)` with an absolute monotonic
expiry. The same deadline can be passed through several operations without
resetting its budget. An already expired deadline fails even on a cache HIT.
Clock injection uses the same monotonic time domain as `expires_at`.
Only expiration of this timeout scope is translated to `SourceDeadlineExceeded`;
an inner/shared owner's `TimeoutError` is propagated unchanged, even just before
the caller's deadline. External `CancelledError` is not reclassified.

The future scan service can read several HITs, then explicitly permit one
`get_or_load`. This PR does **not** implement a scan or count cold accesses.
Callers must reserve their cold-access slot before calling `get_or_load`, also
counting a potentially slow coalesced wait. Do not repeatedly call it after MISS.

## Single-flight and cancellation

Both APIs share Source Cache v1's exact `(source_key, DiscoverQuery)` keys, entries,
concurrent Futures and capacity. There is no `remote_only` key field. All TTL,
LRU, retained-byte, oversized-entry, empty-page and list-isolation rules remain.
At 16 in-flight keys a new key uses bounded uncached passthrough; existing keys
still coalesce. No global network lock, retry or prefetch is added.

An async owner awaits the provider directly in its own task. Its deadline covers
the whole operation and never grows for followers. Deadline expiration cancels
that task's network await, unwinds the response/client contexts, publishes
`SourceDeadlineExceeded` (`source_timeout`) and releases its flight. External
task cancellation propagates `asyncio.CancelledError` to the owner; followers get
the distinct internal `SourceLoadCancelled` type with `source_unavailable` code.
No successful cache entry is published for either failure. A later caller can
become a new owner. Publication and cleanup compare Future identity (ABA guard).

Async followers on the same event loop reuse one bridge per concurrent Future,
shielding their own waits for at most their remaining budget. Finished waiters do
not add retained owner callbacks/bridges; one bridge per flight/loop can remain
until the owner completes. Thread-safe loop delivery preserves the original
exception identity. Timeout/cancellation never cancels the owner or starts a
replacement. Longer-lived followers receive the owner's failure if its earlier
deadline expires. Late bridge exceptions are consumed even without live waiters.
Owner cleanup removes its bridge registry by Future identity, including when an
old owner finishes after a replacement has started. Already-completed flights
are not reinstalled into that registry.

Legacy owners remain legacy: joining an existing urllib flight does not make
that operation cancellable. A new async follower can leave at its deadline, but
cannot cancel another caller's live legacy owner. New async owners do not run
blocking urllib or offload HTTP to threads/background tasks.

## Transport safety

The opt-in provider uses the existing httpx dependency, per-operation async client
and transport ownership, TLS verification and zero connection retries. An outer
asyncio timeout surrounds headers, redirects, streamed body and client cleanup;
each request's socket timeouts also receive only `deadline.remaining()`.
The local network backend wraps TCP streams before TLS upgrade: failure or
cancellation closes the original stream before propagating the original error.
This covers the installed AnyIO/httpcore handshake cancellation gap, before the
pool has registered an HTTP connection. A successful upgrade transfers ownership
to the returned TLS stream without early close. Failed-handshake close is joined
under shield, including repeated caller cancellation; no detached cleanup or HTTP
task survives owner completion. Cleanup can finish after budget expiration.
HTTPX has no public backend constructor argument, so the transport's one local
pool/backend integration point is covered by real-stack TLS regression tests.
Installed dependencies and their global behavior are not patched or changed.
Synchronous normalization is bounded by source size and followed by deadline
checks; cooperative cancellation does not preempt Python CPU work or OS scheduling.

Request construction, JSON/logical-status validation, pagination validation and
vacancy normalization are shared with the legacy client, not reimplemented.
HTTP 429, other HTTP/network errors, socket timeouts and malformed responses keep
their existing source error types. Absolute expiry has its own internal type.

The new path follows GET redirects 301/302/303/307/308, supports Location/URI,
and closes redirect bodies without draining them. It preserves urllib's loop
counters (10 distinct destinations and 4 visits per destination), not a newly
reset budget per hop; no redirect resets the deadline. Unlike urllib's
permissive cross-origin behavior, this opt-in path only permits HTTPS on
`opendata.trudvsem.ru:443` within `/api/v1/vacancies`, without credentials,
fragments or traversal. It does not use environment proxies. This narrows allowed
destinations; legacy redirect/proxy behavior is untouched. Revisit explicit
allowlists if the official source introduces a different endpoint.

Content-Length is checked before reading, and actual raw identity bytes are
counted while streaming in 64 KiB chunks, up to 8 MiB. The request explicitly
asks for identity encoding, as urllib does. Unexpected compressed responses are
rejected before body reading, rather than allowing HTTPX to allocate an unbounded
decompressed chunk; gzip support is not added. Invalid/false/missing length never
bypasses the actual-byte bound. Errors and cancellation close active responses
and the owned connection pool before the operation returns.

## Validation and limits

Tests use injected HTTPX transports/streams, Events, fake monotonic time and
clock-controlled asyncio timeout scopes. TLS regressions run the installed
HTTPX/httpcore/AnyIO handshake with only TCP byte I/O replaced. Tests assert closure
before owner/follower completion, including repeated cancellation, and successful
ownership transfer. Deadline tests distinguish foreign timeouts from own expiry.
Waiter tests cover 50 exited followers on a live legacy owner, publication/delivery
races, mixed waiter outcomes and late error consumption. Owner/follower isolation,
retry by a new caller after failure, cross-path sharing, ABA guards, redirect
safety, body bounds and legacy regressions remain covered.
No real Trudvsem request or sleep-based deadline measurement is needed.

The store and coalescing remain per process, not cross-worker. This adds source
budget control, not faster Trudvsem performance or an end-to-end Discover SLA.
Future continuation still needs its own scan budget, state, navigation and UX.
