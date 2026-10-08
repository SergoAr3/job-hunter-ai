# Matching Foundation v1

The persisted semantic fields remain authoritative snapshots:

```text
UserProfile.skills / Job.required_skills / Job.nice_to_have_skills
  → conservative identity keys + explicit aliases
  → derived UserSkill / JobSkill
```

Profile/Job DTOs, extraction and CV preview still contain strings. The existing
`job-match-v2.1` scorer, availability, weights, aliases and denominators are
unchanged. Historical ApplicationMatchSnapshot inputs/results are not rebuilt.
Web/Bot production code and public DTOs are unchanged. No Match UI is added.

## Schema

| Table | Identity and constraints |
| --- | --- |
| skills | Integer PK; canonical_name (100); unique normalized_key (512); created_at |
| skill_aliases | Global PK normalized_alias (512); alias (100); skill_id FK/index |
| job_skills | PK (job_id, skill_id, requirement_kind); required/preferred CHECK |
| user_skills | PK (user_profile_id, skill_id) |

Named constraints enforce nonblank names/keys, lengths and FK integrity. Keys use
PostgreSQL deterministic C collation / SQLite binary comparison, never ILIKE.
Every Skill has a canonical alias with normalized_alias == normalized_key;
canonical names and synonyms reserve the same global namespace. This is an
atomic service invariant, also audited by reconciliation, without DB triggers.

Owner deletion cascades associations; referenced Skill deletion is RESTRICT.
Deleting an unreferenced Skill cascades its aliases. Sync never deletes dictionary
entries. Existing User/Profile lifecycle is unchanged. UserSkill belongs to the
Profile containing the source snapshot; no duplicate user_id. Same skill in both
job buckets retains two rows; duplicates collapse only inside each bucket.
No proficiency/years/evidence/confidence/source fields are introduced: merged
profile lists cannot accurately attribute manual versus CV provenance.

## Normalization, resolver and bootstrap

`skill_normalization.py` is independent of scoring. Only strings are accepted.
Forbidden controls/invisible characters, blank and overlong values are rejected.
Character validation runs before and after Unicode normalization, including an
explicit bounded rejection of Hangul fillers and Mongolian variation selectors.
Keys use NFKC, Unicode whitespace collapse/trim and casefold. Punctuation remains.
Readable display spelling only collapses whitespace and preserves casing. Values
are never truncated, title-cased, split into tokens or inferred from other fields.

C, C#, C++ stay distinct; .NET differs from NET; R differs from Rust; Go is exact.
RESTful does not become REST API. Postgres/PostgreSQL, NodeJS/Node.js and
REST-API/REST API require explicit aliases.

Bootstrap version 1 has 11 canonical entries: API, Git, HTML, JavaScript, Node.js,
PostgreSQL, Python, React, Redis, REST API, SQL. Additional aliases: JS, NodeJS,
Postgres, ReactJS, React.js, REST, REST-API. Tests freeze this small set. REST is
an explicit current-matcher compatibility alias, not a linguistic normalization.

Every valid live resolution invokes the deterministic idempotent bootstrap before
unknown creation. There is no process-wide cache, startup task or public endpoint;
completion cannot survive a transaction rollback accidentally. Bootstrap uses one
savepoint for the entire operation. An alias belonging to another Skill produces
an explicit conflict and rolls back that seed, never silent reassignment.
`bootstrap_skills(session)` is also available for explicit internal/test use.

Unknown keys create incremental Skills using the first readable input label.
Skill and canonical alias are inserted in one savepoint. Only expected named
unique races are recovered by rereading the winner. Other IntegrityErrors
propagate. Resolver/sync do not commit or roll back the outer transaction. Owner
updates flush before dictionary savepoints. With sqlite3 legacy transaction mode,
an explicit BEGIN precedes SAVEPOINT when the driver has no active transaction;
RELEASE therefore cannot escape outer rollback.

## Sync boundaries

`skill_sync.py` diffs final snapshots, adds missing links and removes stale ones.
Empty lists clear links; repeated sync is idempotent; semantic arrays are unchanged.
Dictionary keys resolve in stable order, including the union of both job buckets.

* Profile PUT: User → Profile locks, refreshed state, validated replacement,
  flush, UserSkill sync, commit. PostgreSQL User locking also serializes first
  profile creation; the old blanket IntegrityError creation retry is removed.
* CV Apply: existing token/revision/fingerprint/merge policy, refreshed locked
  User/Profile, final proposed profile, UserSkill sync, work/facts replacement,
  one domain commit. Failure rolls back Profile/links/work/facts. Token stays
  terminal as before. CV previews/edits/cancel do not touch taxonomy; Bot CV
  confirmation is covered by ordinary Profile PUT. CV is not taxonomy authority.
* Manual and Discover Save: common persisted Job AI service. AI/network work is
  outside Job locks/transactions. Afterwards refresh/lock Job, apply current
  merge policy, sync final arrays, commit. Retained arrays win over unused AI
  candidates. AI failure preserves existing arrays/links.

No sync for reads, the pure `/profile/skills/normalize` endpoint, raw Job create,
source mapping, transient Discover previews, work/fact-only CRUD, status/reminder
writes or historical snapshots. JobSkill presence does not override matcher AI
evidence availability. Existing source-refresh/AI freshness semantics remain.
No infinite deadlock/serialization retries are introduced; failures roll back.

SQLite FK enforcement follows the existing engine. SQLite tests validate schema
and savepoints, not PostgreSQL concurrency. Real tests use independent sessions,
barriers and disposable schemas for seed/identity races, aliases, same-owner
Profile writes, CV versus PUT and final Job updates.

## Finite reconciliation and rollout

From apps/api: `python -m app.commands.reconcile_skills`. Explicit `--dry-run` or
`--apply` is required. Options: `--entity profiles|jobs|all` (all), `--batch-size`
1..1000 (100), positive total `--limit` (1000), nonnegative `--after-id` (0).
Keyset batches are bounded; apply locks/reloads each owner and uses the same sync
service in a short transaction. The command never runs at normal server startup.

Resume profiles/jobs separately using that scope's last_processed_id. `all`
processes profiles then jobs under one total limit; nonzero after-id with all is
rejected to avoid skipping unrelated IDs. Concurrently deleted owners are skipped
while the cursor advances and still consume the selection limit, keeping the
command bounded even when owners disappear concurrently.

Dry-run does not INSERT/UPDATE/DELETE or bootstrap, even for unknown keys. It
reports only aggregate counts, invalid/quarantined owners, canonical alias
violations and per-entity cursors. Seed synonyms can be estimated without writes.
If foundation tables are absent it still reads snapshots and returns
`schema_ready=false`: current associations are absent and counts are estimates.
Apply refuses a missing schema. Unknown keys are reported as counts, not labels.

Legacy policy: duplicates/aliases collapse; blank/non-string/forbidden/overlong
elements are skipped and counted. A non-list field quarantines the entire owner,
preserving all existing links, including both job buckets. JSON is never repaired
automatically. Invalid/quarantined records require explicit review; a clean link
diff alone does not prove evidence completeness.

Rollout: DDL-only migration `20261009_20` after `20261006_19` before sync deployment;
live sync establishes seed; bounded dry-run; separately approved bounded backfill;
drift/input audit; only then future Match UI consumers. No migration-time backfill.
Early migrations contain PostgreSQL-only JSONB, so tests use SQLite parent-schema
pattern and the full PostgreSQL chain. Unrelated historical target_roles CHECK
discrepancy is untouched.

Application rollback leaves semantic JSON usable; relations may drift until
reconciliation. Future relational readers must stay disabled during that period.
Downgrade drops only foundation associations/aliases/Skills, preserving semantic
data and snapshots. A full rebuild after drop need not preserve dictionary IDs.

## Future queries, maintenance and privacy

Skill-ID joins support matched/missing required/preferred and distinct overlap;
NOT EXISTS supports user-only skills. Authorize Profile.user_id and owned
Application first. "Missing" means not listed, not lack of ability. Preserve
unavailable/incomplete input semantics. Original labels remain in semantic arrays.
Relational dedupe can change score denominators: scorer adoption needs a separate
versioned change. Historical snapshots remain frozen.

Controlled rename can preserve Skill ID, reserve a new canonical key and retain
the old alias. Duplicate merge requires moving/deduplicating associations and
transferring aliases before loser deletion, coordinated with writers and audited.
Simply reassigning an alias leaves stale links. No merge/rename UI/API is added.

Global dictionary labels can include private internal tools. No enumeration,
autocomplete, CRUD API or global-counts endpoint. Report/log IDs/counts/outcome
codes/exception classes, never names, CV/profile/description text or SQL parameters.
Profile DB/taxonomy failures return safe 503; Job persistence logs only exception
class and Job ID. No new dependencies, embeddings/fuzzy matching, categories,
inference, external taxonomy, queue, daemon or search-time extraction.
