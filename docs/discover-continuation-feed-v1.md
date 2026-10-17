# Discover Continuation State + Bounded Feed API v1

This is an opt-in API foundation. Existing `/discover/jobs`, its offset DTO,
production synchronous provider wiring, Web, Bot, matcher defaults and Save/detail
authority are unchanged. No client migration, feed URL or PostgreSQL worker is added.

## API and authentication

All routes inherit the existing authoritative user-path authorization: authenticated
Bearer sessions can access their own user path; the trusted Bot service can act for
its target user. A token alone does not authorize anything. A valid cursor is
shareable between authenticated users through each user's own path. The creator ID
is quota attribution, not an access restriction. Deleting that account cascades its
short-lived chains. No arbitrary provider or provider URL is accepted.

Start:

```http
POST /users/{user_id}/discover/feed
{"query":"Backend","market_country":"RU","market_region":null,"remote_only":true,"limit":10}
```

Advance (only this body; filter overrides and unknown fields are rejected):

```http
POST /users/{user_id}/discover/feed
{"cursor":"<next_cursor>"}
```

Replay (no creation, scan, provider or source-cache access):

```http
GET /users/{user_id}/discover/feed?cursor=<cursor>
```

Responses contain `items`, `returned_count`, `cursor`, `prev_cursor`, `next_cursor`,
`next_action`, `state`, `expires_at`. They contain no source total, offsets, remote total or page
number. Each item retains `(source, source_scope, external_id)` for future selection
and existing Save handoff. Responses use `Cache-Control: no-store` because enrichment
is user-specific even though stored source evidence is shareable.

## State and idempotency

Two dedicated tables hold public source/search evidence:

- `discover_feed_chains`: internal random ID, version, immutable search identity,
  quota creator, creation/expiry and bounded state/page counters.
- `discover_feed_states`: 192-bit random URL-safe token as unique primary key,
  versioned JSON payload, lifecycle, materialized outcome, previous/next links and
  lease/fence metadata. Tokens have exactly 32 ASCII base64url characters; generation
  uses `secrets.token_urlsafe(24)` with at most four collision attempts/savepoints.

Search identity fixes provider `trudvsem`, trimmed query, RU market/region,
`remote_only=true`, user limit 1..10 and `remote-feed-v1` algorithm identity.
Evidence JSON explicitly serializes normalized ExternalVacancy fields, including
Decimal salary values and timestamps; no pickle or arbitrary Python objects.
Both chain and payload versions are checked. There is no persisted Profile,
matching result, Application ID/status, saved flag or other user enrichment.

One token starts `ready`, becomes `claimed`, then atomically becomes `complete` with
one outcome. Its response cursor is that same token. A repeated POST of a completed
token returns that committed composition without scanning. Enrichment is recomputed,
so user flags can differ on repeat. GET of a ready/claimed token is a controlled
`FEED_CURSOR_NOT_MATERIALIZED` conflict, not an implicit advance.

- `page`: 1..limit ordered remote items, including sparse pages. A page may already
  confirm exhaustion and then has no next cursor.
- `paused`: no new visible items, budget/cold-slot stop without confirmed exhaustion.
  The outcome is replayable/idempotent and has a *different* next token for explicit
  continuation. Repeating its own cursor never scans again.
- `end`: no items and confirmed source exhaustion; no next cursor. Repeat is stable.
- Errors use normal HTTP errors, never successful paused/end responses.

Each successful non-terminal action creates exactly one pending child in its finalize
transaction. A child's Previous points to its parent's visible page, or skips a
paused parent to the last visible page. Previous is always GET replay, never a scan.
Materialized ordered composition is stable until expiry, independently of source
cache TTL or later source mutations.

### Replay navigation and refresh recovery

GET replay keeps the requested `cursor`, its materialized items, state and Previous,
but resolves current navigation from committed DB evidence. `next_action` is a
strict `"next" | "continue" | null` enum paired with `next_cursor`:

- `next`: the original next transition is ready/claimed, or already completed with
  a visible page. POST the returned token; a completed page is recovered
  idempotently. GET never replaces the current visible page with its successor.
- `continue`: one or more committed paused transitions have been skipped for
  navigation. POST the token after the latest paused transition. If that token has
  already completed with a page, the same POST recovers that page without a scan.
- `null`: no continuation, including a terminal current page/end or a committed
  end reached through paused transitions. `next_cursor` is also null.

For `P -> S paused -> C paused -> D`, one GET of visible page P returns D and
`next_action=continue`. Its URL can keep addressing P while Web restores Continue
after refresh, Back or Application return, without another request or client-only
navigation storage. The same resolution applies when replaying an initial paused
outcome with no visible page yet.

Resolution is read-only: no scan, source/cache lookup, claim, lease mutation or
stored-link rewrite. It follows only paused descendants and stops at a pending/
claimed token, a visible page, or end. The origin plus visited descendants are
bounded by 64 states; cycles, missing/wrong-chain nodes, inconsistent Previous,
unsupported payload versions and invalid evidence fail with controlled invariant/
version errors rather than being reported as end. Each visited descendant requires
one primary-key read and uses the same evidence validation as a direct load. The
normal replay path uses four graph SELECTs when a next token exists, or fewer for
terminal pages; Application enrichment remains a single batch query.

Concurrent GET/finalize needs no row lock. PostgreSQL READ COMMITTED can observe
the pre-finalize ready/claimed token or a committed outcome; finalization publishes
the outcome and child atomically. Completed transitions are immutable, so following
committed paused links cannot observe a partially published child. Metadata may
become outdated after the read; the next explicit POST retains conflict/fencing
protection. Navigation is public evidence and remains identical across viewers,
while matching and Application enrichment are freshly computed per viewer.

POST retains the original input-token-to-outcome mapping and original next token;
it adds `next_action=next` for a page with a child, `continue` for paused, or null
for a terminal outcome. Repeating a successful POST remains idempotent. GET's
navigation metadata may evolve as descendants complete; its visible composition
does not. This distinction preserves response-lost recovery and closes the Web
single-replay paused-refresh blocker without a schema migration.

## Scan and remainder

Constants are centralized in `discover_feed_types.py`: target at most 10 items,
source page size 10, at most four source pages and 40 records per action, 15-second
absolute monotonic scan budget and a cold deadline capped at 13 seconds and the
remaining total budget. Remainder records count toward the 40-record cap.

The scan first consumes the stored unexamined tail, then calls PR1's fresh-only
cache lookup. On the first miss it reserves the cold slot **before** calling
deadline-aware `get_or_load`; joining an existing flight consumes that slot too.
After that, fresh pages can still be read, but a second miss stops the action.
No direct provider calls, second cache, retries, prefetch or lookahead loads exist.
Capacity passthrough in PR1 still counts as this action's sole cold access.

Remote filtering uses normalized `workplace_type == "remote"`; source evidence
and cache keys remain unfiltered. Dedupe within the action, including remainder,
uses `(source, source_scope, external_id)`. There is no global seen-history.

When a visible page fills mid-source-page, the exact unexamined normalized records
are persisted in order (up to one source page), together with the next source offset
and confirmed exhaustion flag. Remainder includes non-remote records if not examined
yet; it never depends on re-reading an old source page or saved indices. If a record
budget stops mid-page, the same tail preservation applies.

An ordinary budget stop between operations can return a sparse page or paused. A
source deadline/timeout, malformed source or provider failure fails the *whole*
advance: no partial page or advanced checkpoint is committed. Existing pages remain
replayable. The original action becomes retryable; fresh cache evidence accumulated
before failure may help a retry. Failed Start returns `detail.retry_cursor` so the
client can retry that action without silently starting another chain.

## Transactions, multi-worker coordination and recovery

The service explicitly owns transactions; low-level load/insert/cleanup helpers do
not commit. A synchronous dependency ends/closes authentication's read session and
returns an engine-bound session factory. Every continuation DB phase runs through
the existing Starlette threadpool helper: its worker creates, uses, commits/rolls
back and closes its own Session. Only plain snapshots/DTOs cross back to the event
loop. Start, claim, finalize, release, replay and enrichment all use this boundary.
No live Session is passed through the async scan or between domain workers. Then:

1. A short transaction locks the chain, validates the state and atomically records
   a random fencing token plus a 45-second lease, or returns its committed outcome.
2. Scan/network await runs with no open DB transaction or continuation row lock.
3. A short finalize transaction locks the same chain first, verifies token, active
   lease and chain expiry, creates a child if needed and commits the entire outcome.

Another worker observing a live claim gets HTTP 409 `FEED_ADVANCE_IN_PROGRESS` with
`Retry-After: 1`; it performs no scan or hidden polling. Lease expiry permits reclaim
with a different fence. Old finalize/release cannot change the new owner's state.
Normal errors and caller cancellation conditionally release only their own claim;
failed release/process crash falls back to lease recovery. No row lock spans source
I/O. SQLite uses explicit short `BEGIN IMMEDIATE` write transactions, including
savepoints; PostgreSQL row locks provide production coordination.

PostgreSQL phases set transaction-local `lock_timeout=1000ms` and
`statement_timeout=3000ms`; commit/rollback restores the pooled connection's prior
settings. No global engine/database/auth/legacy configuration changes. Lock wait
SQLSTATE 55P03 maps to HTTP 409 `FEED_DB_LOCK_TIMEOUT`, distinct from logical
`FEED_ADVANCE_IN_PROGRESS`; both have `Retry-After: 1`. Query cancellation SQLSTATE
57014 maps to retryable 503 `FEED_DB_QUERY_TIMEOUT`. Other DB failures retain their
original exception type/status handling, including connection/serialization,
unique and programming errors; they are not labelled contention. SQLAlchemy bound
parameters are hidden on propagated DB errors. Claim lease time is sampled after
the row lock has been acquired.

Request cancellation cannot interrupt synchronous SQL mid-call. The caller sets a
worker cancellation flag and joins the shielded worker, including repeated task
cancellation, before leaving. The worker checks that flag before SQL and at its
last pre-COMMIT boundary, rolls back when possible and always closes its Session.
If cancellation races an already committed claim/result delivery, the claim is
conditionally released in a separately joined cleanup phase. If it races COMMIT
itself, rollback cannot be promised: an already committed Start/outcome remains
subject to the existing lost-response/idempotency/TTL semantics. No worker keeps
mutating after the cancellation handler finishes. Cleanup release is joined even
under repeated cancellation; a DB failure falls back to the claim lease. LOCAL
timeouts bound database lock/statement waits, not arbitrary OS/network/process
stalls or a hard wall-clock cancellation time.

The lease exceeds normal scan/source budgets. As with any expiring lease, a worker
suspended beyond it may overlap a reclaimer after resuming; fencing guarantees one
committed outcome, not exactly-once external I/O across crashed/suspended processes.
PR1 cache remains process-local and is not a distributed network-operation lock.

## Expiry, retention and caps

All states inherit a fixed 30-minute chain lifetime from creation. Replay, HIT,
advance and activity never extend it. Expired retained tokens return HTTP 410;
there is no silent restart. Unknown tokens return 404, malformed tokens 422,
incompatible versions and corrupt/wrong-type states controlled 409 errors.

Bounds: 20 active chains per creator, 32 visible pages per chain, 64 total states
(including paused outcomes and the pending child), 256 KiB UTF-8 JSON per state,
at most 10 remainder records, and 32-character client tokens. Capacity errors are
controlled (429 or 413), never eviction of live cursors. All writes of evidence
check the byte bound; the DB also enforces payload length, lifecycle/lease/result
consistency, counter bounds, foreign keys and token uniqueness.

Creation/claim opportunistically deletes at most 10 chains expired for over one day,
using the expiry index and cascade deletion. No background worker or full-table
request scan is added. Retained expired states are inaccessible throughout. After
physical purge, a previously expired token is indistinguishable from unknown and
returns 404; indefinite tombstones are deliberately not kept. Cleanup depends on
future mutations; inactive deployments can retain expired rows longer physically.
Per-creator active counting uses the creator/expiry index and a short User-row lock
to serialize concurrent starts. State claim is a token-primary-key lookup, so a
separate lease index is unnecessary.
The self-FK referencing columns `prev_token` and `next_token` each have their own
btree index (`ix_feed_state_prev`, `ix_feed_state_next`) so `ON DELETE SET NULL`
lookups during chain purge do not require scanning unrelated states.

## Enrichment, logging and consistency limits

Every response reads the current user's Profile and batches current Application
state through existing Discover enrichment. No N+1 and no continuation row lock
are involved. Preview matching keeps the existing v2.1 contract/default behavior.
Save/detail paths remain authoritative/fresh; replay never refreshes source evidence.

Application logs include action/result, page/record counts, cache hits, cold-slot
use, returned count, elapsed time and claim/reclaim outcomes. Correlation uses only
a short SHA-256 token hash. No raw query, token, vacancy payload or user private data
is logged. Before downstream processing, feed-only middleware copies the request
scope and removes the query string from the original logging-visible scope.
Router/auth receive the copy with real query params. Uvicorn and the outer
ServerErrorMiddleware retain the redacted original, including 307 slash redirects
and unhandled 500 responses. Method/path/status/client information and unrelated
routes' logging are unchanged; no response-send wrapper is involved.
Upstream proxies must likewise redact cursor query strings in their access logs.

This is not a source snapshot: unseen source pages may change and cause rare skips
or duplicates between visible pages. TLS close is awaited and may finish after a
deadline; cooperative cancellation does not preempt Python CPU or scheduling. No
Trudvsem performance or end-to-end SLA is promised. Web continuation/URL migration
and Bot changes remain later work.

## Validation

Tests inject source evidence and finite event/clock controls; none require live
Trudvsem. Scanner tests assert fresh reads, cold loads and provider calls, including
coalesced waits. API/service tests exercise replay, navigation, fresh user enrichment,
idempotency, errors, tokens, caps and logging. PostgreSQL tests use independent
sessions in disposable schemas for competing claims, NOWAIT row locks during source
await, lease recovery/fencing, quota races and rollback. Migration tests compare
models/indexes/defaults/constraints and upgrade/downgrade on SQLite and PostgreSQL.
DB-boundary regressions exercise the real async endpoint with a held PG row lock
and a 50ms heartbeat, controlled timeout mapping, session/thread ownership and
cancellation before/after claim commit. Access-log tests use the real app stack
and Uvicorn target formatter for normal GET/POST, redirects, outer 500 and an
unrelated route. An indexed cleanup regression builds 2048 graph states, checks
planner eligibility (seqscan disabled only diagnostically), FK SET NULL/cascade
and preservation of unrelated active chains.
Replay-navigation regressions cover one/multiple paused outcomes, terminal recovery,
response-lost page recovery, claimed transitions, the 64-state bound, graph corruption,
fresh per-user enrichment and exact zero source/cache calls. Independent PostgreSQL
sessions also replay while a finalizer has flushed but not committed, proving
nonblocking pre-commit reads and correct navigation after commit.

Run PostgreSQL checks with `RUN_FEED_POSTGRES=1` and an existing local PostgreSQL;
`IDENTITY_TEST_DATABASE_URL` can select another test database. No runtime restart or
second database stack is needed. Migration revision: `20261016_21`, parent
`20261009_20`; the migration is additive, downgrade removes only continuation data.
