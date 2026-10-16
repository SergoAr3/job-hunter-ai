# Discover Source Cache v1

Discover caches only successful, normalized `SearchPage` evidence around
`VacancySourceProvider.search(DiscoverQuery)`. A small provider-neutral wrapper is
wired only to Trudvsem search. There is no registry or speculative source logic.
`get_detail()` and Save continue fetching detail from the provider directly.
Transport, redirects, the 25-second socket timeout and retry behavior are unchanged.

## Identity and fresh user context

The key is `(source_key, DiscoverQuery)`: keywords, market country, administrative
region, page size and **public item offset**. Discover already trims keywords and
uppercases market country. The cache does not casefold keywords, change internal
spaces, normalize region or round offsets. Provider validation and the Trudvsem
item-offset-to-page translation remain authoritative on misses.

`remote_only` is excluded because filtering still happens locally after source
pagination. User ID, auth, Profile, Application IDs/status and matching results
are never cached. Every request still authorizes the caller, checks the user,
reads Profile, runs the Application-state batch lookup, applies local filtering
and constructs fresh DTOs/match previews. Profile remains a per-request snapshot
read before source search, including on a cache miss. Preview stays job-match-v2.1.

The cache retains vacancies as a tuple of frozen, flat `ExternalVacancy` DTOs,
plus total/limit/offset. Every caller gets a new `SearchPage` and items list.

## Freshness and capacity

- Fixed TTL: **120 seconds**, from successful source-load completion, using
  `time.monotonic()`. Hits update LRU position but never extend expiry.
- At most **128 entries** and **16 MiB of accounted retained data** per process.
- A single entry larger than **1 MiB** is returned but not stored.
- On lookup/insertion, remove expired entries first, then evict oldest LRU
  entries until both bounds hold. No background cleanup worker is needed.
- Accounting counts each flat DTO scalar with `sys.getsizeof`, including text
  and Decimal values, even if objects are shared. A conservative 2 KiB envelope
  per entry and per vacancy covers object/dict/container overhead, timezone
  references and allocation padding. Revisit accounting if DTOs gain nested
  fields. This budget is not a hard RSS bound and excludes transient HTTP/JSON
  allocations and active request results.
- Successful empty/exhausted pages can be cached. Errors, invalid JSON,
  malformed pages and failed pagination validation cannot.
- TTL/LRU are the only invalidation mechanisms; errors do not serve expired
  evidence. Save or status/Profile updates do not invalidate public source pages.

A source update/removal and source total may remain unseen for up to the cache
TTL after a load; the source itself may also lag. Save still verifies detail.
After a removal error, refresh may still show the cached card until expiry.
Back/Forward, refresh, remount and explicit repeat search still call the API,
but may reuse source evidence. Different pages are not a consistent feed snapshot;
their totals/contents may have different ages. Remote post-filter pagination is
unchanged and remains a separate problem.

## Concurrency and process scope

One store lives for the API process. Short `Lock` sections protect metadata;
provider calls, Future waits and logging run outside that lock. Identical misses
share a per-key Future. Every caller receives its own list. Owner exceptions
reach followers and are not cached; owner `finally` removes in-flight bookkeeping.

At most **16 keys** are coalesced concurrently. A new key at capacity performs
ordinary **uncached passthrough**, without globally waiting. Existing keys still
coalesce. Followers wait at most **25 seconds** and then use the existing
`source_timeout` error mapping, with no automatic retry. A timed-out follower
does not remove a still-running owner: doing so would allow duplicate loads.
That live registration remains bounded and is removed when the owner finishes.
This does not add a total deadline to urllib or cancel an active source call.
Followers still occupy sync threadpool slots.

Restart clears the cache. Multiple workers/instances have independent stores,
memory budgets and in-flight maps, so duplicate misses across processes remain
possible. No Redis, Celery, DB changes or new dependencies are introduced. Future
providers require explicit opt-in and a review of their source identity, filters
and cache/access terms before reuse of the abstraction.

## Observability and validation

Existing `uvicorn.error` INFO logging reports source, hit/miss/coalesced/bypass,
bypass reason (oversized/in-flight-limit), source duration, cache age on hits,
entry count and accounted bytes. Query text, payload, user IDs and exception
messages are not logged. First unseen pages remain slow; only hits skip upstream.

Targeted tests use injected providers/transports, fake clocks, Events and bounded
Future waits. They cover capacity/expiry/accounting, concurrent success/error/
timeout, saturation passthrough, fresh Application/Profile/matching behavior,
local remote filtering and pagination/error regressions, without external HTTP.
