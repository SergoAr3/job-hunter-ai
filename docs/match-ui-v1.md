# Match UI v1

Application detail loads the current match automatically through
`GET /api/applications/{applicationId}/match`, a thin authenticated BFF adapter to
`GET /users/{user_id}/applications/{application_id}/match`. A compact summary sits
after vacancy metadata/source, followed by vacancy description and requirements.
Full match details follow the vacancy content, then CRM notes, next action and
history. One loader supplies both match sections; loading or failure never blocks
the vacancy or CRM controls, and retry is shown only in the compact summary.
The compact summary starts with a closed local disclosure. It shows at most four
short facts derived from the same presentation logic as "Что проверить": required
gaps, real mismatches/conflicts, aggregated unknowns, and factual positives when no
important gaps/conflicts exist. It does not repeat skill labels or criterion details.
Expanding does not navigate or change the URL; state resets on application changes.
The secondary native `#match-details` link inside the preview preserves the return
query and focuses the full section heading. Methodology lives in the details,
without a second large score card.

## Source of truth and compatibility

`job-match-v2.1` remains authoritative. Scoring, weights, denominators, thresholds,
coverage/confidence/recommendation semantics, aliases and algorithm version are
unchanged. Match UI does not perform matching, text inference or AI advice, and
has no auto-apply action. Skill/SkillAlias/UserSkill/JobSkill are intentionally
unused for both scoring and display grouping, canonicalization or deduplication.
Original job labels, duplicate occurrences and required/preferred buckets remain.

The existing MatchResult fields are preserved. The match endpoint adds optional
`explainability` with `schema_version: 1`, leaving the pure scorer and snapshot
writer untouched. For each of the eight components it contains `notices` and
`unknown_items`. Items have bounded stable `code`, `source` (`profile`, `job`,
`comparison`) and optional `value`. See `MatchExplanationCode` in API schemas for
the complete allowlist; all current components are emitted, even with empty lists.

Read-side explanations use the same factual inputs, language parser/level helper
and seniority vocabulary as the scorer. They never recompute matched/missing or
influence any legacy field. They cover unavailable/empty skill inputs, partially
unparseable languages, unknown profile language levels, salary currency/period
limitations, and location not compared for remote/unknown workplace. Legacy flat
reasons are not repaired: an unknown language level can still appear in legacy
gaps, but structured Web rendering only shows scorer `components.missing` as gaps.
Bot production handlers remain unchanged and may ignore the additive field.

## Presentation

Score is `N / 100`, labelled "Оценка по сравнимым критериям". Coverage is the sum
of weights of components with a non-null score, not a percentage of known fields
or an unweighted count of criteria. Copy:

> Для оценки удалось сравнить критерии с суммарным весом N%.

> Критерии имеют разный вес. Неизвестные данные исключаются из оценки.

Low confidence keeps a valid score visible with an explicit limited-data caveat.
Insufficient data displays no score, retains coverage and available component
facts. Recommendation is preserved in transport but not displayed as an apply
instruction. Discover remains a limited preview, sharing summary copy and using
Web profile navigation. It does not render the full skills UI.

Required skills use matched / not found in profile / unable to determine lists.
Unknown is not mismatch, and not found does not establish lack of ability.
Preferred gaps remain visually neutral. Other criteria compare role, seniority
level (not years of experience), languages, workplace, location and salary.
Remote location is not evaluated and implies no international eligibility.
Salary comparison does not convert currencies.

Each fact has one owner: components own matched/missing; explainability owns
unknowns. Flat arrays are not repeated as a second full list; "Что проверить"
provides short focus/anchor links. The source link already present in the header
and profile navigation supply ordinary actions, without duplicating source CTA.

## Current versus historical

Every match GET reads current Profile and Job. No Web polling or persistent
match cache is introduced. Remount/application change requests fresh data;
BFCache restore refreshes it. Aborted/late requests cannot overwrite another
application, and failed refresh does not retain a confirmed old score.
ApplicationMatchSnapshot captures immutable historical applied context for
learning. This UI does not read, expose or alter it.

## States, layout and verification

Loading is local (`aria-busy`, `role=status`). `409 PROFILE_REQUIRED` offers
`/profile` and `/profile/import`; it is never zero. Infrastructure/malformed
responses display an alert/retry, never a fabricated score. Older responses
without explainability retain component facts and a generic unknown fallback.
BFF validates IDs and allowlist-projects nested DTOs, uses session principal and
no-store, and never forwards internal backend errors.

Desktop has full-width summary, wider required and narrower preferred skills;
768/390 use one column. Long labels wrap. No gradients, bright green, progress
bar grid or sticky mobile match header. Semantic headings/lists/dl, textual
status labels, focus targets, accessible disclosures, 44px controls and existing
focus tokens support keyboard use. New UI has no animation.

API regressions cover compatibility, aliases/duplicates, unknowns, auth,
relational isolation and unchanged snapshots. Web regressions cover BFF projection,
loading/retry/abort, zero/null, low coverage, skill categories, focus and fallback;
existing CRM/Discover and Bot suites remain applicable. Browser fixture smoke
checks 1440/768/390, long labels and recovery states without modifying user data.
No dependencies, persistence or DB migration are needed.
