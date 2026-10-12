# Frozen Matcher Alias Policy for v2.2

This defines frozen identity semantics. The default-off production candidate is
now implemented in [Job Match v2.2](job-match-v22.md): production still defaults
to `job-match-v2.1`, with explicit config opt-in for v2.2. The shadow comparator
retains its existing registry-based hybrid diagnostics.

## Two different authorities

Foundation is a mutable taxonomy and derived relation/index layer. Its resolver
can continue registering aliases and synchronizing UserSkill/JobSkill.

Matcher policy owns versioned scoring equivalence. SkillAlias is not an authority
that can silently add equivalences to a released matcher. Adding a registry alias
can change taxonomy resolution without changing frozen matcher identity.

`job-match-v2.2` is explicitly bound in code to `matcher-identity-v1`.
There is no "latest" lookup. Unsupported algorithm versions raise only
`unsupported_matcher_algorithm`; v2.1 is not redirected through this module.
Policy changes require a new matcher version and preservation of old policies.

## Pure identities

`matcher_identity_policy.py` has no database dependency. The public helpers are:

- `get_matcher_identity_policy(algorithm_version)`;
- `normalize_matcher_skill(value)`;
- `canonicalize_matcher_skill(value, algorithm_version=...)`;
- `equivalent_matcher_skills(left, right, algorithm_version=...)`.

The pure helper's explicit default is `job-match-v2.2`, not the newest registry/policy;
this does not change the production config default of `job-match-v2.1`.
Identities are canonical normalized strings, not integer Skill IDs. This keeps
them reproducible across database rebuilds and avoids a second policy-code
namespace: `postgresql`, `javascript`, `rest api`, `node.js`, `react`.

Normalization reuses the existing validated Foundation primitive: NFKC,
whitespace collapse and casefold, with punctuation preserved. Non-string, blank,
forbidden/invisible and overlong values raise InvalidSkillValue with fixed codes
only. Changes to these normalization semantics must also respect matcher
versioning; frozen regression cases protect the current behavior.

The 12-key immutable mapping contains canonical keys and these explicit aliases:

| Identity | Explicit aliases |
| --- | --- |
| postgresql | postgres |
| javascript | js |
| rest api | rest, rest-api |
| node.js | nodejs |
| react | reactjs, react.js |

All other valid skills use their normalized exact key: Django becomes `django`.
No DB access, alias inference, fuzzy matching, stemming, transliteration,
punctuation stripping or LLM is involved. Normalized alias lookup is O(1).

C/C#/C++, R/Rust, Go/Golang, .NET/NET, RESTful/REST API, Node-JS/Node.js and
React js/React.js remain distinct. REST-API normalization retains its hyphen;
only the explicit policy maps it to `rest api`.

Policy objects are frozen dataclasses with defensively copied MappingProxyType
maps containing only immutable strings. The algorithm binding is also a mapping
proxy. No caller-owned backing dict or mutable registry cache is retained.

## Read-only registry compatibility

`validate_registry_compatibility(session, algorithm_version=...)` checks whether
the existing registry can represent the frozen alias namespace. It returns an
immutable RegistryCompatibility with a `compatible` property, version identifiers,
expected/checked counts and a bounded tuple of issues.

It checks table presence, then executes one batch SELECT for the 12 policy keys:

1. Every frozen alias, including canonical aliases, must exist.
2. Its target Skill.normalized_key must equal the frozen canonical key.
3. The target Skill's canonical alias must point back to that same identity.

Issue codes are `registry_schema_missing`, `policy_alias_missing`,
`policy_alias_wrong_target` and `canonical_alias_inconsistent`. Alias diagnostics
come only from the known public policy keys, never arbitrary registry labels.
There are at most 24 alias issues or one schema issue. Extra registry aliases are
allowed and do not change policy results.

The helper disables autoflush, performs no bootstrap/resolution/sync/flush/commit
and neither owns nor closes the caller's transaction. The caller supplies its
consistent read transaction. DB execution failures propagate without logging;
the candidate execution boundary protects caller transactions with a read savepoint
and emits privacy-safe bounded fallback codes.

Compatibility is scoped to the frozen namespace, not a global taxonomy health
certificate. It does not check UserSkill/JobSkill drift or unknown exact keys.
In particular, a registry may merge Go/Golang while this audit remains compatible;
pure matcher policy still keeps them distinct. Future evaluation must use policy
identities to decide membership, not unrestricted equality of integer DB IDs.
Referenced mappings outside the frozen namespace and owner associations require
their own policy-aware consistency check before any relational optimization.

## Candidate fallback and historical semantics

The implemented default-off contract is:

`configured v2.2 -> validate policy/registry and owner consistency -> hybrid`

Missing/incompatible registry or stale associations must lead to a **full v2.1
fallback**, with `algorithm_version=job-match-v2.1`. No partially mixed result.
Semantic inputs must remain available after a DB read failure; the dispatcher
must respect transaction ownership, including existing snapshot write sessions.
These components are implemented by the default-off dispatcher and owner reader;
see [candidate transaction and fallback contract](job-match-v22.md).

Hybrid keeps semantic occurrences authoritative: original labels/order,
duplicates and required/preferred denominators remain unchanged. Only membership
uses frozen policy identity; weights, availability and all score math stay as
before. Direct distinct scoring is outside this contract.

A captured snapshot's `algorithm_version=job-match-v2.2`, immutable semantic inputs
and preserved frozen evaluator together determine identity semantics without
the current registry. Policy alone is not a complete scorer/replay implementation.
Old snapshots remain untouched. No migration or taxonomy history/revision tables
are required for this code-owned policy.

The shadow comparator can later gain an explicitly versioned policy-based hybrid
view and compatibility diagnostics. Its present hybrid uses mutable registry
identity; this change intentionally leaves its code and report semantics intact.
