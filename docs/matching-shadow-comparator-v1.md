# Matching Shadow Comparator v1

Offline, finite, read-only diagnostics for saved Applications with current Profile
and Job inputs. Production `job-match-v2.1`, endpoints, MatchResult, Web/Bot,
Discover, historical snapshots and Foundation sync are unchanged. No migrations,
new dependencies, startup tasks, workers or production activation are involved.

The separate [frozen matcher policy for future v2.2](matcher-frozen-policy-v22.md)
does not change this comparator. Its current hybrid remains a registry-based
diagnostic view, not an activated frozen-policy scorer.

## Views

* **Legacy** uses the actual pure v2.1 core. Each semantic job occurrence is one
  denominator item; its original label/order/duplicates remain intact. Profile
  skills are membership, with only v2.1 aliases.
* **Hybrid** resolves each original occurrence through existing SkillAlias rows
  and checks UserSkill membership. It keeps semantic positions, labels and the
  full semantic denominator. It never creates an identity or guesses a seed alias
  absent from the database.
* **Direct** checks UserSkill against distinct JobSkill IDs separately for required
  and preferred. Its denominator is the distinct association count. One original
  label per identity is retained internally for reasons, never printed.

All views retain the baseline skill availability gate, including empty profile,
empty bucket and unsuccessful AI enrichment. Unknown remains null, never zero.
Non-skill components and salary conflicts come from one legacy evaluation.
Counterfactual aggregation preserves the exact legacy operation order, weights,
component/final rounding, coverage, insufficient-data rule, verdict and
recommendation helpers. None of these estimates is a new production algorithm.

`legacy_to_hybrid` measures identity policy effects; `hybrid_to_direct` measures
distinct reweighting. Direct can increase **or decrease** score and is not a
candidate production scorer. Distinct counts and occurrence counts have different
units; always compare their denominators too. Score changes do not establish skill
proficiency or improved hiring outcomes.

## Consistency and reads

Each bounded batch uses PostgreSQL REPEATABLE READ, READ ONLY. SQLite explicitly
begins a read transaction, enables connection-local query_only and restores it on
exit. Sessions disable autoflush and close with rollback, never commit. The
identity reader executes SELECTs only; it does not call resolve_skills, bootstrap,
sync, enrichment or snapshot writers. The CLI has no `--apply` or write mode.

Expected identity **sets** from semantic Profile and both Job buckets are compared
to actual associations. Same counts with different identities are drift. Referenced
canonical aliases and known seed ownership are checked too. All semantic keys must
exist in the actual registry; seed-based estimates cannot certify consistency.

Primary status precedence:

| Status | Meaning |
| --- | --- |
| quarantined | Missing Profile/Job or a non-list skill snapshot |
| invalid | Invalid individual skill value or malformed legacy comparison input |
| schema_missing | Any Foundation table absent |
| unresolved | A valid semantic key has no existing identity |
| drift | Association sets differ or a referenced alias invariant is broken |
| evidence_unavailable | Clean pair, but legacy cannot evaluate either skill bucket, or AI evidence is unavailable |
| comparable | Clean, resolved pair with at least one evaluated skill bucket |

Dirty pairs have no hybrid/direct evaluation or trusted deltas. Clean unavailable
pairs retain null skill components and counterfactual views for diagnostics, but
their deltas are null and they do not enter candidate statistics. Comparable pairs
can still have insufficient total coverage; null totals/deltas use the `null`
histogram bucket. Within a comparable pair an empty bucket is not counted as an
evaluated skill bucket.

Statuses are a partition, not independent error counts. For example unresolved
takes precedence over association drift; per-pair consistency counts retain the
available detail. No JSON repair, taxonomy creation or reconciliation is performed.

## CLI

Run from `apps/api`, using the existing environment/DATABASE_URL:

```sh
.venv/bin/python -m app.commands.compare_matching --limit 10 --batch-size 10
.venv/bin/python -m app.commands.compare_matching --limit 100 --batch-size 25
.venv/bin/python -m app.commands.compare_matching --limit 10 --per-pair --pair-limit 10
.venv/bin/python -m app.commands.compare_matching --after-id 123 --limit 100
```

Default output is one aggregate JSON object. Limits: batch-size 1..1000 (100),
total limit 1..10000 (1000), nonnegative Application after-id (0). Per-pair output
requires explicit `--per-pair`; pair-limit 1..1000 (100) bounds retained diagnostics
independently of the processing limit. `last_processed_id` is the resume cursor.
Selected IDs consume the limit and advance the cursor even if owners are missing.
The first empty batch ends the run. Applications are the counting unit; profiles,
jobs and identities are reused within batches. A new batch gets a new DB snapshot,
so this is not a transactionally frozen report across the entire dataset.

Exit codes: 0 for a completed run, including ordinary dirty data; 1 for missing
Foundation schema or internal/DB failure; 2 for invalid options. Missing schema
still emits an aggregate report when the application tables can be read. Failures
emit only a fixed error code and, for internal errors, exception class. No SQL,
parameters, database URL or exception message is printed. There is no daemon mode.

## Report and privacy

Aggregate output contains the status partition, cursor, legacy/hybrid/direct
matched counts and denominators per bucket, component/total score-delta histograms,
total-score histograms and verdict/recommendation transitions. All candidate
statistics use **comparable pairs only**, including the legacy side of each paired
comparison. They are not statistics about all selected applications.

Duplicate counters distinguish same-normalized-key collapse, alias collapse and
required/preferred identity overlap. Public alias codes are limited to `postgresql`,
`javascript`, `rest`, `rest_api_dash`, `node_js`, `react`. They count equivalent
spellings encountered, not necessarily score changes; REST-API can contribute both
REST codes. No new alias is inferred, including Go/Golang, C/C#/C++, .NET/NET,
R/Rust and RESTful/REST API.

Per-pair projection contains only IDs, fixed status/reason/alias codes, bounded
counts, scores, coverage, verdicts/recommendations, duplicate counters and set-diff
counts. Private alias usage is a count, without labels or key hashes. It never
serializes internal dataclasses or raw arrays. Raw CV, notes, descriptions,
private taxonomy labels and historical payloads are excluded. Only columns needed
by the scorer are loaded; descriptions, notes and historical snapshots are not read.
Reports are operator diagnostics and must not be exposed as a public taxonomy API.

## Validation and interpretation

Start with a small read-only sample, then increase the bounded limit. Diagnose
unresolved, invalid/quarantined and drift pairs separately before interpreting
candidate deltas. The comparator does not authorize or run reconciliation --apply.
An empty or small dev sample cannot establish production readiness.

SQLite regression tests cover aliases, negative identities, occurrences, buckets,
availability, dirty-data exclusion, exact math, zero writes, privacy and CLI bounds.
Optional PostgreSQL tests reuse RUN_MATCHING_POSTGRES=1 and existing disposable
schema infrastructure; they validate read-only enforcement and a concurrent owner
commit between semantic and relational reads. They do not start PostgreSQL.

No results are stored and no historical captures are recomputed. Rollback is simply
to stop running the CLI. Any future v2.2 activation, alias-policy versioning or
snapshot-replay design requires a separate reviewed implementation.
