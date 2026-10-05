# Work Experience editing v1

Profile uses the existing `WorkExperience` domain, table and migration
`20260912_10`. No schema change or dependency is required. Work experiences are
owned by `UserProfile`; the parent FK cascades when a profile is deleted.
`ProfileExperienceFact` is a separate profile collection, with no FK to work
experience. Editing/deleting a work entry preserves those facts and all profile
preferences. Cover-letter evidence reads the current persisted work collection.

## Editable fields and limits

- `company` (company/project), `position`: nullable strings, maximum 200
  characters after API whitespace normalization; at least one must be nonblank.
  Whitespace is collapsed/trimmed; blank becomes null. Controls/surrogates rejected.
- `engagement_kind`: `employment`, `internship`, `freelance`, `unknown`.
  Transport values retain their existing contract; Web labels are localized.
- `start_year`, `start_month`, `end_year`, `end_month`: nullable strict integers.
  Years 1900–9999; months 1–12. Month requires year; year alone is supported.
  Future dates are rejected. No day precision is introduced.
- `is_current`: true/current, false/ended, null/unknown. End dates require false.
  For partial-date ordering, missing start month is January and missing end
  month is December, exactly as the existing domain validates it.
- Maximum 20 entries per profile. Existing exact normalized duplicate detection
  (case-insensitive text, all input fields) remains in effect.

IDs/timestamps are service fields, not editable. Duration is calculated only
when start and end/current month precision permit it. No description or child
facts exist on this model.

## Resource API and ownership

FastAPI paths retain `/users/{user_id}/profile/work-experiences`:
GET list, POST create (201 canonical entry), PUT replacement (Bot compatibility),
PATCH `/{experience_id}` (200 canonical entry), DELETE `/{experience_id}` (204).
PATCH merges explicitly supplied fields with the locked persisted entry, then
validates the full state. Missing means unchanged; nullable null clears;
blank text normalizes to null. Enum cannot be cleared to null. Empty PATCH is a
valid no-op. `current=true` with existing end dates is rejected unless the same
PATCH explicitly clears both end parts. The Web form sends those nulls.

Global authentication authorizes the path user using the bearer session, or
existing trusted Bot service credentials. Nested entry lookup is scoped to the
owned profile. Foreign/missing entries both return 404. Browser-supplied user
ownership is rejected. Browser → Next BFF → FastAPI remains the only Web flow.
BFF resolves identity from the HttpOnly session, checks mutation origin/JSON,
rejects extra fields/query parameters, caps actual body bytes at 8192, projects
canonical IDs/display fields, and maps errors without raw validation/SQL details.
BFF DELETE takes an empty JSON object and returns `{ok:true}` for the shared
browser request helper. Failed DB writes roll back and return a safe uncertainty
code; no automatic mutation retry.

The API locks the profile before read/merge/write, also used by CV replacement.
There is no revision guard: writes to the same field have last-write behavior.
Web PATCH sends only changed fields, preserving concurrent unrelated changes.
`updated_at` exists but is not a precondition. No revision system was added.

## Profile UX

The existing section stays in read mode with compact company/position, period
and engagement label. One inline add/edit form opens at a time. Save is disabled
without changes, while pending, or while an uncertain result needs checking.
Cancel discards unsent local state. An in-flight request cannot be undone: Cancel
requests closing after its result arrives. Errors retain the draft. A read can
check an uncertain mutation before another save; untouched fields are refreshed
without becoming accidental PATCH changes. Deleted/replaced entries require
cancel/reload. Recovery compares company/position using the API's deterministic
whitespace normalization and blank-to-null semantics; other fields remain exact.
It does not use case folding, fuzzy matching or punctuation heuristics.
Add/edit/confirm use associated labels, accessible errors, a native checkbox,
reusable `ListboxSelect`, visible focus and keyboard buttons. Save/cancel return focus to
the trigger, delete to Add. Delete uses inline confirmation with Cancel; facts
remain. Dates use a two-column minmax grid and action groups wrap on small screens.
No autosave, standalone editor route or top-level navigation is added.

## Shared dropdown controls

`ListboxSelect` is the shared controlled combobox/listbox for Work Experience
engagement, Application status, Applications status filter and sorting, Profile
experience/workplace/salary period/language level, and CV Preview engagement,
experience/workplace/salary period. CV language level uses its editable suggestion
mode and continues to allow free-form values. Labels remain localized while
callbacks and BFF requests retain the existing canonical domain values.

Menus use visible keyboard focus, Enter/Space, arrows, Home/End, Escape, Tab and
outside-click dismissal. They open above when needed, cap their height and scroll.
No native select/datalist remains in these controls. CV edits still go through
the trusted preview endpoint with the existing token/revision checks.

Request validation errors on the new Work Experience PATCH return bounded
`WORK_EXPERIENCE_INVALID` without rejected input or Pydantic details, using the
Application PATCH pattern. Legacy POST/PUT validation contracts are unchanged.

## CV Import contract

CV import still replaces the complete work and facts collections. Manual entries
and edits are not preserved by a later import. The Profile UI states this.
An import preview fingerprints profile, work and facts. Manual create/PATCH/delete
after preview makes apply stale; a newly prepared preview can replace the current
manual snapshot. Apply remains atomic; previews retain their existing session,
revision, expiry and replay protections. No import contract changes.

Provenance/history, source migration, CV builder/versioning, reorder, AI editing,
attachments and similar-job merging remain outside v1. Bot retains its existing
editing flow and PUT contract.

## Verification (2026-10-06)

Fix-round targeted API: 70 passed. Full API: 1116 passed, 69 skipped
(opt-in/external integration checks). The preceding review separately enabled
and passed all three Work Experience PostgreSQL checks, including concurrent
PATCH of different fields. No DB write logic changed in this fix round.
Targeted Web recovery/Listbox/CV/transport: 53 passed. Full Web: 631 passed;
lint, typecheck, production build and Prettier check passed. The preceding review
passed 90 Bot Profile/Work/CV compatibility tests; Bot production code is unchanged.

Existing make-dev stack was reused (API 8000, Web 3100, PostgreSQL 5432).
No duplicate runtime or explicit restart; API/Web hot reload applied changes.
Temporary test/build workers and smoke clients exited. The preceding browser CRUD smoke used
a synthetic account, with create/edit/current/delete/reload and unsent cancel.
1440/768/390 were checked; mobile scrollWidth equaled 390, including long
company/position cards. Focus and date controls were checked.

The final CV dropdown browser smoke used a temporary Playwright Core client with
the installed Chrome in an isolated headless profile. Direct `setInputFiles`
avoided the OS picker and browser extension; no extension settings or project
dependencies changed. One real provider extraction passed through the existing
BFF/API. Keyboard engagement selection saved `freelance`, revision 1→2; workplace
selection saved `hybrid`, revision 2→3, retaining the same token. The 390px menu
fit within the viewport without horizontal overflow. Cancel left persisted
Profile/Work unchanged. Work engagement, Application status, Applications
filter/sort and Profile enum sanity also passed. The temporary browser closed.
The synthetic account, its domain data,
captured verification mail and temporary credentials were cleaned up.
Screenshots are retained outside Git for visual review.

## Files changed

- `apps/api/app/services/work_experiences.py`
- `apps/api/app/main.py`
- `apps/api/app/work_experience_routes.py`
- `apps/api/app/work_experience_schema.py`
- `apps/api/tests/test_auth_security.py`
- `apps/api/tests/test_work_experiences_postgres.py`
- `apps/web/app/api/profile/work-experiences/route.ts`
- `apps/web/app/globals.css`
- `apps/web/components/profile-workspace.tsx`
- `apps/web/components/applications-list.tsx`
- `apps/web/components/cv-preview-editor.tsx`
- `apps/web/components/listbox-select.tsx`
- `apps/web/lib/client.ts`
- `apps/web/lib/errors.ts`
- `apps/web/lib/profile.ts`
- `apps/web/lib/server/profile.ts`
- `apps/web/tests/profile-transport.test.ts`
- `apps/web/tests/profile-workspace.test.tsx`
- `apps/web/tests/applications-list.test.tsx`
- `apps/web/tests/cv-selects.test.tsx`
- `apps/web/tests/listbox-select.test.tsx`
- `docs/auth-slice2.md`
- `apps/api/tests/test_work_experience_editing.py`
- `apps/web/app/api/profile/work-experiences/[experienceId]/route.ts`
- `apps/web/components/work-experience-editor.tsx`
- `apps/web/lib/server/work-experiences.ts`
- `apps/web/lib/work-experiences.ts`
- `apps/web/tests/work-experience-editor.test.tsx`
- `apps/web/tests/work-experience-transport.test.ts`
- `docs/work-experience-editing-v1.md`
