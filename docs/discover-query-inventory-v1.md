# Discover short-lived query inventory v1

## PR1: schema, identity and repository foundation

This is an additive, dormant foundation. Production Discover still uses the
existing source client and continuation feed. PR1 does not add a worker, source
HTTP, routes, Web polling, matching changes, scheduling, retries/backoff, cleanup
execution, admission quotas, refresh policy, or a Discover entry-point switch.

The intended later flow is Submit -> resolve/build -> atomic publication ->
stable snapshot navigation. An unfinished generation is never a result page.

### Identity

`InventoryIdentity` in `app/services/inventory_types.py` trims query edges, rejects
empty/over-200-character queries, preserves case and internal whitespace, maps
country casing to RU, and validates an optional 1..32 ASCII-digit region without
removing leading zeroes. Only source `trudvsem` and explicit `remote_only=true`
are valid. Positive integer semantic versions are part of the identity:
query normalization, source query, predicate and snapshot schema.

Canonical JSON uses sorted keys, compact separators and UTF-8; its SHA-256 digest
is indexed. Reuse additionally compares the complete canonical string. Identity
excludes users/profiles/residence, page sizes/numbers, offsets, operational
budgets and deployment versions. Future semantic versions can resolve separate
generations; PR1 page commits support only the current version-1 semantics.
The identity digest and raw query are not logged.

### Schema

Alembic revision `20261018_22`, parent `20261016_21`, adds only:

| Table | Purpose and constraints |
| --- | --- |
| `discover_inventory_keys` | Canonical identity, unique digest, coordination row |
| `discover_inventory_generations` | Lifecycle, durable source checkpoint, counters, lease and future retry metadata |
| `discover_inventory_items` | Source-only JSON/JSONB snapshots, PK generation/ordinal, unique generation/source/scope/external ID |
| `discover_inventory_views` | Digest-only opaque handle, generation pin, page size and fixed expiry |

Generations reference keys with RESTRICT; items/views reference generations with
RESTRICT. No deletion primitive or unbounded cascade is introduced. View creator
is nullable, references users with SET NULL, and attributes quotas only; it is
not the owner/authorization boundary of a shared view. There is no inventory FK
from Jobs, Applications, notes or reminders.

The partial unique building/key index exists on both PostgreSQL and SQLite.
Other indexes support fresh-generation selection, future due/lease lookup,
ordinal/identity lookup and view expiry/pin/creator lookup. Metadata/migration
parity tests cover types, defaults, nullability, constraints, PK/unique/FK/index
definitions and the partial-index predicate.

### Transactions and concurrency

`InventoryRepository` in `app/services/query_inventory.py` owns exactly one
transaction per public operation on a caller-owned **idle, clean Session**. It
does not close that Session. Success commits; exceptions roll back. A caller
must finish authentication/other reads before calling it, and must not wrap
operations in its own transaction. Results are detached values, not live ORM
entities. Model methods have no transaction behavior.

All generation writes and retirement/pin decisions lock **key -> generation**.
Resolver inserts a missing key inside a savepoint and recovers only a real
competing digest insertion. It then checks canonical equality and, under the
key lock, prefers fresh ready, then building, then creates building. Outcomes
are `reused_ready`, `reused_building`, `created_building`.

Publication takes the same key lock, preventing the resolve/publication race.
The partial unique index independently rejects a second building generation.
Independent PostgreSQL connections test both publication/resolve orders and the
unique insertion recovery path. No process-local mutex provides correctness.

Resolve and view creation deliberately remain **separate transactions** in PR1.
`create_view` revalidates eligibility under the same locks. Expiry/failure between
the calls is a controlled domain error; a future orchestrator may resolve again.
No partial view or silent generation switch is committed.

SQLite reserves a write transaction with BEGIN IMMEDIATE before reads, including
savepoints, so rollback does not accidentally commit a top-level savepoint. It
covers basics and rollback; PostgreSQL remains authoritative for concurrency.
Caller engines should configure finite lock/statement deadlines; race fixtures
explicitly do so.

### Lifecycle and ownership foundation

Allowed transitions are building -> ready/failed and ready/failed -> expired.
Expired is terminal; no transition back to building or ready -> failed exists.
Stale is a freshness condition, not a fifth status.

`claim_generation` reserves a 60-second lease, returning a random fence. It is
only an ownership primitive, not a poller or retry implementation. Expired claims
can be reclaimed; active claims conflict. Page commit, failure and publication
require a matching unexpired fence. Page commits additionally compare the exact
expected source page; two concurrent completions cannot consume it twice.

Operations accept an explicit aware time or injected clock. Default clock samples
after locks; inputs must be timezone-aware and storage is UTC. SQLite's stripped
offsets are restored only for values read from this UTC-only storage.

### Atomic page commits and publication

`commit_inventory_page` accepts synthetic/already-fetched source evidence; it
never performs HTTP. Source page size is fixed at 100. `returned_count` counts
all source records, while `items` contains only remote candidates. The next page,
items, ordinals, payload bytes, source total, exhaustion and timestamps commit
together. An exception, including during flush, rolls back the whole page.

The first total is fixed for the traversal; a changed total or contradictory
page size is rejected. Ordinals follow the first remote occurrence of each
`(source, source_scope, external_id)`. Later duplicates neither replace evidence
nor consume another ordinal. Skipped non-remote rows are not stored.

The last nonempty page does not prove exhaustion in this implementation. A
following valid empty source page at/after total is committed as confirmation.
Zero source total needs only the initial valid empty page. Thus total 2001 needs
21 nonempty pages plus one confirming empty page. This is a repository contract,
not a scan implemented in PR1.

`publish_generation` verifies the caller's expected counters, building status,
fence, exhaustion, source counts/page position, fetched timestamp, actual item
count, contiguous 0..N-1 ordinals, snapshot/identity/timestamp agreement and exact
payload accounting. Publication sets ready/completed/freshness atomically and
clears the claim. A crash before publication leaves building; a later owner can
publish an exhausted checkpoint without refetching its source pages.

Read helpers reject building/failed/expired item pages. Ready items, including
stale-ready items, are readable. Returned snapshots are detached copies. All
repository item writes require building ownership, so published composition and
order cannot be changed through this boundary. Direct SQL is not an alternative
supported writer; cross-row invariants are deliberately not implemented as
triggers.

Limits: 10,000 examined source records, 1,000 inventory items, 16 MiB generation
payload and 128 KiB item payload. Payload bytes are UTF-8 bytes of canonical JSON
containing snapshot and remote evidence; they exclude SQL/index overhead. A cap
violation rolls back; evidence is not truncated or silently skipped. Validation
reads at most 1,001 items; page reads at most 10; pin checks use EXISTS.

Source evidence reuses the existing bounded normalized Evidence DTO and rejects
extra viewer-specific fields. Remote evidence records the current exact
`employment` value and predicate version. No raw source response is persisted.
Serialized snapshot/evidence strings (including object keys and nested values)
reject U+0000 with a generic domain error before any page SQL, consistently on
SQLite and PostgreSQL JSONB. An iterative compatibility check permits at most
64 container levels and 128 Ki nodes; DTO validation and canonical UTF-8 byte
accounting remain authoritative. Arbitrary nested objects are not added to the DTOs.

### Views, freshness and pinning

`create_view` returns a 32-character URL-safe random token (192 bits); only its
SHA-256 digest is stored. Token collisions are retried in bounded savepoints.
Building and fresh-ready generations accept new views. Failed/expired/stale-ready
generations do not; an existing building view can replay a later failure.

Page size is 1..10, default 10, independent of generation identity. Views expire
after at most 30 minutes from creation, including preparation. Freshness defaults
to 15 minutes from publication. Replay never extends expiry. `read_view` is an
internal handle/status lookup, not an authenticated public API; a future route
must authenticate its current viewer without requiring creator ownership.

`has_live_views` uses EXISTS. `expire_generation` is a bounded transition helper,
not cleanup: it refuses fresh-ready or live-pinned generations. Pin creation and
retirement use the same locks; PostgreSQL tests cover both race orders. Existing
stale views remain readable until their own expiry.

User-specific matching, Application status and profile enrichment are not stored
in inventory. A later page API must recompute them per viewer. Inventory expiry
must never remove saved CRM data.

### Errors and validation

Internal exceptions distinguish identity, missing generation, conflict, expiry,
invalid transition/publication/view and capacity errors. They are not HTTP
responses. Error text does not include raw queries, tokens or evidence. Propagated
SQL errors hide bound parameters. PR1 adds no identity/token logging and no new
secret/HMAC infrastructure.

Run from `apps/api`:

```sh
.venv/bin/python -m pytest tests/test_query_inventory.py tests/test_query_inventory_migration.py
RUN_FEED_POSTGRES=1 .venv/bin/python -m pytest tests/test_query_inventory_postgres.py tests/test_query_inventory_migration.py
.venv/bin/pyright app/services/inventory_types.py app/services/query_inventory.py
```

PostgreSQL fixtures reuse the existing DB server with unique disposable
`feed_test_*` schemas, finite connection/lock/statement timeouts and independent
sessions. The migration helper refuses an unscoped PostgreSQL URL and verifies
current_schema before migrations. No public/dev schema should be migrated by
tests. PostgreSQL exercises real historical migrations and revision-22 roundtrip;
SQLite establishes/stamps the parent schema because historical revision 03 uses
a PostgreSQL-only regexp operator, then runs the actual new Alembic revision.
Key-lock race tests release the holder only after an independent observer sees
its backend PID in `pg_blocking_pids(waiter_pid)`. Observer polling has a 3-second
deadline; participating transactions use local 6-second lock / 8-second statement
timeouts. Holder, waiter and observer backend PIDs must be distinct.

### Remaining work

PR2 must implement supervised build execution, source fetching, bounded retry and
admission policies, source-wide cooldown handling and recovery/cleanup execution.
Later slices add authenticated APIs, viewer enrichment and Web preparation/read
UX. No ready generation here is a guaranteed atomic Trudvsem snapshot: it is an
observed completed traversal, immutable only after publication in Veylinq.

Legacy `discover_feed_chains`, `discover_feed_states`, source cache, routes,
matching and clients remain unchanged. The new Alembic head requires updating
head assertions in existing migration tests; their historical revision checks
remain intact. Downgrade drops only the four new tables and must not be used on
a deployed inventory with live views without first draining that flow.
