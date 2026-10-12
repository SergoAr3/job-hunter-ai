# Job Match v2.2: default-off hybrid candidate

Production defaults to **job-match-v2.1**. Adding this feature does not enable
v2.2. The API reads `MATCH_ALGORITHM_VERSION` once at config import/startup:

| Configuration | Current saved matching |
| --- | --- |
| Unset | job-match-v2.1 |
| job-match-v2.1 | Exact legacy scorer; no Foundation reads |
| job-match-v2.2 | Attempt validated hybrid; full v2.1 fallback if unsafe |
| Any other value, including empty/latest/auto | Startup ValueError: unsupported_match_algorithm_version |

`calculate_current_match(session, profile, job, application)` is the shared
entry point for saved Match GET, first-applied snapshot capture and cover-letter
matching context. A keyword version override is available for bounded tests and
comparisons; consumers use the config default. Web accepts exactly v2.1/v2.2;
Bot keeps the same DTO and handlers. No public debug/taxonomy fields are added.

## Scoring and semantic occurrences

`calculate_match()` and Discover's `calculate_match_preview()` remain explicit
v2.1 entry points. The legacy aliases and skill scorer are untouched. Both saved
versions share the existing component math, aggregation and reason generation:
role 20, required skills 30, preferred 5, seniority 15, languages 10, workplace 5,
location 5, salary 10. Both rounding stages, coverage/confidence, availability,
insufficient-data behavior, verdict thresholds and recommendations remain the
same. Only validated skill membership changes for v2.2.

Semantic `Job.required_skills` / `nice_to_have_skills` define each bucket's
occurrence count, positions, original labels and order. Runtime occurrences carry
bucket, position, label, frozen identity and resolved Skill ID; nothing is stored
in a new table. Matched/missing arrays preserve original labels and repetitions.
Required and preferred are independent, even with the same identity in both.

For required `[Postgres, PostgreSQL, Docker]` and profile `[PostgreSQL]`, there
are two matched occurrences out of three, giving rounded skill score 65. The
distinct relational set is used only for consistency, never the denominator.
Absent profile skills, empty requirements or unavailable AI skill evidence keep
the legacy unknown state. Relations never manufacture unavailable evidence.

## Frozen identity and safe DB mapping

The immutable binding is **job-match-v2.2 → matcher-identity-v1**. See
[frozen policy](matcher-frozen-policy-v22.md). NFKC, whitespace collapse and
casefold preserve punctuation. Explicit equivalences are Postgres/PostgreSQL,
JS/JavaScript, REST/REST API/REST-API, NodeJS/Node.js and ReactJS/React.js/React.
All other skills use their exact normalized identity.

C/C#/C++, R/Rust, Go/Golang, .NET/NET, RESTful/REST API, Node-JS/Node.js and
React js/React.js stay distinct. Mutable aliases cannot extend this policy.

Before evaluation, the existing registry compatibility checker must pass for
every frozen alias, including aliases not present in this pair. The read layer
also validates every referenced raw and canonical key. An unknown exact key
must point to a Skill with that same normalized key and its own canonical alias.
For example, `Django → django` is safe; an added DB alias `Django → Python` is
incompatible. Go/Golang merged by mutable registry therefore produce full legacy
fallback rather than a new matcher equivalence. Unused extra aliases are allowed
and leave v2.2 results unchanged.

Profile-derived Skill ID sets must equal UserSkill exactly. Job-derived
`(Skill ID, bucket)` sets must equal JobSkill exactly. Missing/extra links, wrong
kind and equal counts with different identities all fail. Only after all these
checks does membership use `(frozen identity, validated Skill ID)` pairs.

## Consistent read and transaction ownership

Production write ordering was inspected: Profile PUT and CV Apply acquire
`User → Profile`; Job enrichment acquires `Job`; first-applied capture already
holds `Application`. Candidate reads take PostgreSQL SHARE row locks in order
`Profile → Job → registry aliases/Skills`, with aliases sorted by normalized key.
The reader never acquires User or Application afterwards. Existing write flows
do not acquire Profile after Job. Registry bootstrap/resolution reads existing
entries and inserts missing ones; the matcher does not invoke either service.

Owners are reloaded with `populate_existing` under their locks, before reading
associations. A concurrent semantic replacement and relation sync must finish
before the reader proceeds, so preloaded ORM values cannot cause false drift.
Registry alias and Skill SHARE locks protect the mappings used by the checker
and evaluator. Missing required frozen aliases fail before a concurrent insertion
could make an unlocked mapping appear compatible. No isolation setting changes.

The caller retains its outer transaction and locks until its own transaction
ends. GET session cleanup releases them; cover-letter's existing endpoint rollback
releases the read transaction before requesting external generation. Snapshot
captures retain the same domain transaction and commit status/history/snapshot
together. Snapshot inputs are serialized **after** the consistent evaluation so
refreshed semantic inputs and captured result agree.

The dispatcher uses a connection-level savepoint, because Session.begin_nested
would flush pending mutations even inside no_autoflush. SQL read failures roll
back only this savepoint, preserving the PostgreSQL caller transaction for full
legacy fallback and subsequent snapshot writes. It never calls Session flush,
commit, rollback or close. No dictionary creation, sync, repair or bootstrap runs.

SQLite starts an explicit outer BEGIN if sqlite3 legacy mode has not started one.
This supplies a consistent snapshot and prevents SAVEPOINT release from ending
the caller transaction; SQLite does not emulate PostgreSQL row locks. Only
PostgreSQL and SQLite are supported by this candidate reader. Detached owners,
owners belonging to another Session, or pending new/dirty/deleted caller state
fall back without flushing or refreshing caller changes. Flushed changes already
inside the caller transaction remain visible and preserve caller rollback.

## Full fallback and diagnostics

Unsafe registry, unresolved identities, missing Foundation tables, owner drift,
invalid frozen input or SQL consistency errors cause one complete v2.1 execution.
No role/skills/salary mixture is returned. Successful hybrid reports
`algorithm_version=job-match-v2.2`; any legacy fallback reports **job-match-v2.1**.
Configured version must never be counted as successful candidate version.

Internal logger `app.services.match_dispatch` emits only:
`match_v22_fallback reason_code=<bounded code>` at INFO. Enable that logger for
operator diagnostics. Codes: registry_incompatible, owner_relation_drift,
unresolved_identity, invalid_semantic_input, schema_unavailable,
db_consistency_failure. No skill labels, arrays, CV, notes, vacancy text, SQL
parameters or exception traceback are emitted by the fallback path.

Invalid frozen strings can fall back if the original arrays still contain only
strings accepted by legacy. Malformed non-list/non-string semantic skills raise
bounded `invalid_match_semantic_input` rather than a zero/unknown score. Existing
snapshot handling captures an immutable `matcher_error` unavailable marker if
legacy evaluation is impossible; ordinary current evaluation propagates the
existing server-error behavior. No input repair happens.

## Consumers, history and rollback

Explainability uses the selected result's actual components/reasons. Clients
never canonicalize skills and the Match UI layout is unchanged. Discover remains
an independent **legacy transient preview**, with no relational requirements.

New snapshot captures use the same dispatcher and store the actual algorithm
version. Existing captures are immutable: no replay, recomputation or backfill.
The frozen code-owned policy determines v2.2 identity; snapshots do not store
mutable registry dumps. Snapshot schema version stays 1. The historical Shadow
Comparator remains its registry-based diagnostic, with unchanged report semantics;
seed-compatible parity tests cover the separate candidate evaluator. No large
comparator refactor is included because its mutable equivalence contract differs.

Rollback: set `MATCH_ALGORITHM_VERSION=job-match-v2.1` (or remove the variable)
and restart the existing API through its normal lifecycle. Current matching
returns to the legacy path. No DB rollback, migration rollback, Foundation removal
or snapshot rewriting is needed. This implementation adds **no migrations,
dependencies, daemons or startup tasks**.

## Verification

From apps/api:

```bash
.venv/bin/python -m pytest tests/test_match_v22.py -q
RUN_MATCHING_POSTGRES=1 .venv/bin/python -m pytest tests/test_match_v22_postgres.py tests/test_skill_postgres.py tests/test_matching_shadow_postgres.py -q
.venv/bin/python -m pytest tests -q
```

The PostgreSQL fixture reuses an existing server (optional
IDENTITY_TEST_DATABASE_URL), creates a disposable schema with lock/statement
timeouts, and removes it afterwards. It never starts PostgreSQL. Tests cover
real Profile PUT/CV Apply/Job enrichment concurrency against a preloaded reader,
transaction recovery after a real SQL error, snapshot input refresh and no domain
writes. SQLite tests cover aliases/negatives, denominator/positions/labels,
drift, malformed input, config/default-off, snapshots, cover-letter and Discover.
Web runs its full tests/typecheck/lint; Bot runs DTO/handler compatibility tests.

Before any runtime checks, inventory existing Web/API/Bot/reminder worker,
PostgreSQL and make dev. Reuse existing services and run only finite commands;
keep persisted configuration at v2.1. This feature does not need a duplicate stack.
