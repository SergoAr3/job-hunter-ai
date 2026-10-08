# CV Import Web v1

`/profile/import` reuses the existing `CVProfileDraftAIService` and PDF/DOCX
parser. There is no OCR, additional provider, dependency or domain migration.
Uploaded bytes and extracted text are used during extraction only; no original
CV is retained. Application/import logging uses timings, error codes and exception
classes, not raw CV or AI output. Content-bearing pypdf diagnostics are suppressed
by a permanent filter on the audited dependency logger names, before handlers can
receive message arguments, exception/stack text or extra fields. The policy is
installed once, not toggled per request; dependency upgrades check logger coverage.
This describes the application's policy, not a guarantee for arbitrary external
debug instrumentation or replacement logging configuration.

## Contracts

- POST `/users/{user_id}/profile/cv-import`: multipart `file`, up to 5 MiB
  (request body up to 5 MiB + 128 KiB). Existing extension/MIME/signature and
  parser resource limits apply. The endpoint returns a structured preview only.
- PATCH `…/cv-import/preview`: token, expected revision and a discriminated typed
  edit (profile proposal, one work/fact update or deletion); JSON body <= 64 KiB.
- POST `…/cv-import/apply`: `{token, revision}` only; no editable AI payload accepted.
- POST `…/cv-import/cancel`: `{token}` only; discards the preview without writes.

These routes require a real bearer session as well as existing path ownership.
The BFF checks Origin, resolves server identity and bounds incoming bytes before
multipart parsing, including requests without Content-Length. It forwards
multipart, not base64 JSON. Upload timeout is 80 seconds; Apply/Cancel 20 seconds.
Unknown errors and extra upstream metadata are projected away.

## Editable Preview v1

Preview opens in read mode. Local editors support main profile data (roles,
locations, experience, workplace preference, complete salary block), skills,
language name/level, each work entry (company/project, position, engagement kind,
partial dates/current flag) and fact text. Work/fact entries can be deleted.
No work/fact creation, sorting, rich text or permanent CV editor is introduced.
Save sends the typed correction through the BFF to the edit endpoint; the API
reuses domain normalization/validation, enforces limits and updates only the
existing trusted temporary structured draft. Profile lists keep their union
policy with the original current profile; language aliases retain existing
display names/nonempty levels. The UI explains that retained Profile values can
reappear after normalization. Existing scalar preserve rules remain unchanged.

Every accepted edit atomically increments a revision in the temporary payload,
without issuing a new quota row or extending the original 15-minute TTL. Edit
and Apply serialize via the existing temporary SQLite transaction. An old edit
or Apply returns `cv_import_revision_stale` before changing/claiming the preview;
stale requests never apply newer, unseen values. Initial legacy Apply requests
without revision are equivalent to revision 1 and cannot bypass an edited draft.
Owner/session, expiry and terminal-state checks apply to edits as well.

Local Save/Cancel are explicit; Apply is disabled while an editor is open or a
request is pending. Cancel local changes performs no request. Import Cancel
erases the saved temporary edits; the domain Profile is untouched until explicit
Apply. Apply consumes the revision checked server-side draft and preserves the
snapshot/transaction rules below. A lost edit response can leave the browser
stale; re-upload to review a fresh preview rather than applying unseen changes.

Errors use safe field labels/static messages, not raw Pydantic input/exception
details. Work/facts have a final cap of 20; existing Profile list/string/date/enum
constraints are enforced server-side. No migration or new dependency is needed.

Extraction instructions preserve explicitly written project/freelance context
headings in the existing `company` field and each block's own `position`, including
additional/personal projects. Current/freelance signals stay local to each block.
Status/date/activity continuation lines qualify that heading rather than creating
another entry.
This is a general prompt contract, without hardcoded project names or fuzzy dedupe;
strict provider/domain schemas and the one bounded invalid-output retry remain.

## Apply policy

Roles, skills and locations are unions with conservative case/whitespace
deduplication; skills reuse the existing alias normalization. Existing languages
win when the canonical language identity is already present, including legacy
readable levels. Russian/Русский, Armenian/Армянский and English/Английский share
identity; existing display names and nonempty levels win. Empty levels may be
filled from CV. Unknown names use case/whitespace normalization.

Unknown experience, `any` workplace preference and absent salary preserve current
values. Explicit extracted scalar replacements are shown next to current values.
Salary remains a complete amount/currency/period block.

Work Experience is a full snapshot of the newly extracted CV. Apply replaces
**all current work entries**, including manually entered ones: this model has no
source/origin field, so source-aware preservation is not implemented. Exact
duplicates inside the incoming snapshot are removed; matches against existing
work are retained in the snapshot. Preview explicitly warns about replacement
and displays current and final counts. An empty snapshot clears work history,
with an explicit empty-list warning. Old append-only previews are rejected as
stale because they did not authorize deletion. Existing Bot draft behavior is
preserved; only the Web import requests complete work/fact snapshots.

Practical experience facts have mixed/manual lifecycle: the API/Bot support
manual add/edit/delete, and the model has no source field. The approved Web import
policy replaces **all current practical facts**, including manual facts, with
the normalized, deduplicated snapshot from the new CV. Preview explicitly warns
about this, displays current/final counts, and warns when the final list is empty.
No CV/manual classification or source-aware preservation is claimed.

Record limits are per collection, not one shared profile-record count: work has
20 entries, facts have 20, and profile lists have 30. Work and facts validation
counts only the final incoming snapshots, never existing + incoming. Existing
collections at their caps do not block a valid replacement. Limits remain in
place; profile list and scalar policies are unchanged. There are no
description/technology fields on WorkExperience or source migration. Matching
Foundation v1 derives UserSkill associations from final persisted Profile skills
during Apply, inside the same transaction as Profile/work/facts. Extraction,
preview edits and Cancel do not write taxonomy. Preview still contains strings;
the union policy and token lifecycle are unchanged. See
[Matching Foundation v1](matching-foundation-v1.md).

Profile, work deletion/insertion and facts are committed together. Failure before
commit rolls back replacement and retains old work and facts. Insert order
preserves preview order when work is read through the API. A snapshot check rejects
changed profile/history as `cv_import_stale`; upload again to review new values.
Apply locks the owning user and profile. Existing editing contracts are unchanged.

Production PostgreSQL uses row locking for this transaction. SQLite remains
supported for sequential local/test imports, but its `FOR UPDATE` does not provide
equivalent row-level serialization: concurrent Apply of **different previews** for
one user/snapshot is not guaranteed duplicate-free. Same-token concurrent Apply
and replay remain protected by the temporary-store claim on either domain database.
Tests exercise full same-token domain Apply and sequential deduplication; the
PostgreSQL distinct-preview test uses an isolated schema and an opt-in flag
`RUN_CV_IMPORT_POSTGRES=1`. No process-global domain lock or schema change is added.

## Temporary state and replay

Preview state uses a separate temporary SQLite file, **not the domain database**.
Set `CV_IMPORT_STATE_DIR` to a private directory outside the repository, shared
by all API workers on one host. Default: an OS temp directory owned by the API
OS user. Directory/file permissions are 0700/0600. Tokens are opaque 256-bit
random values; only hashes are stored, bound to user ID and authenticating session
hash. Only validated preview values and snapshot fingerprint are retained.

TTL is 15 minutes. Startup/periodic cleanup runs every 60 seconds; request-time
cleanup also removes expired records. SQLite secure_delete clears deleted payloads.
At most 5 active/terminal records per account and 500 globally are retained.
Applied/cancelled attempts count until expiry: Cancel does not immediately free
quota. `cv_import_busy` tells users to wait and try again, not to cancel a preview.
Cancel and Apply clear preview contents immediately. Navigation away or aborting
extraction never applies data; abandoned results expire.

A SQLite conditional claim serializes concurrent workers before domain writes.
The token becomes terminal before Apply and cannot be replayed, even after a
process crash. A rolled-back or failed attempt requires a new preview; an
uncertain commit result requires inspecting Profile first. There is no automatic
retry that might create duplicate work history.

The temporary store and domain database are separate transaction domains: the
domain updates are atomic, while a crash after claim may consume a token without
applying it. This deliberately favors safe replay behavior. Multi-host deployments
require host-affinity for the import lifetime or a shared state-store replacement;
they must not mount this SQLite store on a network filesystem.

## Validation and manual smoke

Automated tests exercise existing parsers/provider validation, injection-as-data,
upload bounds, ownership/session isolation, preview-only behavior, union/preserve
semantics, conservative deduplication, stale state, rollback and concurrent claim.
Web tests cover protected routing, upload/processing/preview, safe transport,
Cancel, Apply, errors and redirects.

Manual smoke must use synthetic PDF/DOCX and a safe test account. Confirm no
mutation before Apply/after Cancel, current vs proposed, applied Profile/work
history after reload, replay rejection, malformed/unsupported UX, 1440/768/390
layout and no CV contents in logs. Test artifacts belong outside the repository.

### Implementation smoke, 2026-10-04

The following implementation/review/polish entries are historical evidence from
before snapshot replacement. Their append behavior and isolated runtimes do not
describe the current Apply policy or the latest smoke below.

Used a synthetic verified account in a separate local SQLite domain database,
an isolated API/Web pair, and real PDF/DOCX files with the configured existing AI
provider (no extraction mock). Existing personal accounts were untouched.

- PDF: upload, processing, current/proposed preview, Cancel with original Profile
  and empty work history preserved; subsequent explicit Apply and Profile reload
  persisted the expected unions, scalar replacements and Acme work history.
- DOCX: preview and Apply appended Docker and Beta work history; existing salary,
  location and languages remained intact. Year-only/current dates stayed partial.
- Additional real DOCX HTTP extraction/Apply returned 200; replay returned
  409 `cv_import_used`. No raw CV markers or import token appeared in runtime logs;
  the preview exposed no internal IDs, paths or snapshot metadata.
- Corrupt PDF showed a safe readable error. Native Chrome picker rejected the
  unsupported-content sample; explicit unsupported-type error projection is
  also covered by automated API/BFF/UI tests.
- Preview screenshots and DOM widths checked at 1440, 768 and 390: document
  scroll width equalled viewport width. File/actions are native keyboard controls;
  Tab from the file input reached the recognition button.
- Fixed the error/abort selection reset found during smoke: remounting the input
  cannot leave a previously selected invisible file available for another upload.

Screenshots and synthetic sample/account artifacts are outside the repository.

### Technical-review fixes and evidence, 2026-10-04

The readable malformed-PDF reproduction previously emitted a private dictionary
marker through `pypdf.generic._data_structures`. A separate-process negative
control with the old logging policy still reproduces it. The fixed parser keeps
the expected text while dropping dependency diagnostic records before handlers.
Tests cover all LogRecord fields/direct handlers and two barrier-overlapped parses
with different markers, with unchanged logger configuration after both calls.
Application/auth/AI operational logging remains enabled. A malformed DOCX XML
test also confirms document-bearing exceptions are not logged verbatim.

Quota regression creates and cancels five previews, confirms the sixth upload is
still busy, then confirms expiry/cleanup permits a new upload. Web error tests
check the wait/retry wording. No quota or Apply production semantics changed.

Fresh isolated PostgreSQL evidence: `test_postgres_distinct_previews_serialize_domain_apply`
passed with both tokens claimed before domain locking; one Apply succeeds, the
other returns stale, and only one work-history row exists. SQLite full same-token
concurrent Apply and sequential new-preview deduplication pass; this does not
extend SQLite's guarantees to concurrent distinct previews.

Fresh validation: targeted parser/import/auth 168 passed; full API 1025 passed,
67 skipped (the new opt-in PostgreSQL Apply test passed separately above); full Web
509 passed; lint, typecheck, production build, Prettier and diff whitespace checks
passed. Bot was not changed.

Fresh manual runtime smoke used a new synthetic verified account and the actual
AI provider through the isolated Next BFF: readable malformed PDF and normal PDF
both returned 200 with validated current/proposed previews. Private marker and
import tokens were absent from API/Web runtime logs. Both previews were cancelled;
Apply and a new visual smoke were not repeated. Quota UX/state was checked through
automated regressions rather than additional live AI uploads. Samples/logs remain
outside the repository.

### Visual/UX polish evidence, 2026-10-04

Profile now has a dedicated CV Import entry card; ordinary reads do not show a
success banner. Apply still redirects to `/profile?imported=1`, with one success
message. A client-side history replace removes only `imported`, preserving other
query parameters/hash and history state. A normal refresh no longer repeats it.

The upload zone retains a labelled native file input and keyboard picker, adds
drag/drop selection, and shows selected filename/size. Processing announces its
status without a percentage. Preview groups profile, skills/languages, new work
history and practical experience; unchanged fields render once. Skills wrap as
Profile chips. Summary counts come from existing preview values, with skills
explicitly counted as the final list. Sticky actions stay within the content
column; mobile Apply spans the action bar width.

Fresh browser smoke used the isolated synthetic account/database and an actual
AI provider: Profile entry → native PDF picker → processing → preview (20 skills,
3 new work records) → sticky actions → Apply → Profile. Imported skills/work/facts
were present after reload. Exactly one success message appeared after Apply; the
URL became `/profile`, and refresh removed the banner. Personal accounts were
untouched. An initial upload was rejected by the smoke server's missing Origin
configuration; correcting the temporary server configuration allowed the flow.

Upload and preview were checked at 1440/768/390 widths, with document scroll width
equal to viewport width. Skills wrapped at 390; desktop/mobile final facts stayed
above the action bar at the bottom. Keyboard Tab from the visible file picker
reached recognition. Screenshot evidence (Profile entry, Upload, preview top,
bottom/actions, Profile success, and mobile Upload/preview/actions) is stored in
`/tmp/cv-import-v1-polish`, outside Git. The browser extension's file-URL restriction
was handled using the native picker without changing extension permissions.

Fresh Web validation: targeted CV/Profile UI 36 passed; full Web 515 passed;
lint, typecheck, production build, Prettier and diff-check passed. API files are
byte-identical to their state before polish; API suites were not repeated.

### Snapshot replacement validation, 2026-10-04

Read-only investigation of the reported import found 8 current work entries and
16 practical facts. The CV proposed 5 work entries and 8 facts; the old merge
produced 11 work entries and 24 facts. The blocking limit was therefore facts
(24 > 20), not work. Full facts replacement, including manual facts, was explicitly
approved after confirming that facts have no source field.

Fresh automated validation: targeted API 174 passed, 1 skipped; isolated
PostgreSQL concurrency 1 passed; full API 1046 passed, 67 skipped; targeted Web
55 passed; full Web 524 passed. Lint, typecheck, production build, Prettier and
diff whitespace checks passed. Regressions cover replacement at both collection
caps, repeated same-CV import, empty snapshots, Cancel, preview/persistence order,
old-preview rejection and rollback after flush/commit failures.

Live smoke reused the existing Web/API runtime and real configured AI provider
with synthetic DOCX files and a dedicated temporary verified account. At 1440,
20 work entries became 2 and 20 facts became 1. At 390, work became 3; repeating
the same CV retained 3 work entries without accumulation. Facts also matched each
preview exactly (the provider returned 4 on the repeated extraction); Cancel
preserved both collections. Replacement warnings and current/final counts were
visible, and document width matched viewport width on desktop/mobile.

No duplicate app stack or manual restart was used. API reload/Web hot reload
picked up changes. The finite browser driver and isolated PostgreSQL test schema
were closed/removed; synthetic accounts, mail, owned preview state, documents,
scripts and screenshots were cleaned up. Personal profiles were not modified.

### Extraction quality and editable preview evidence

The original reported PDF's normalized text retained both distinct project and
freelance headings/roles (4081 characters); baseline provider output lost their
context and returned two generic Backend roles. Parser normalization, domain
guards and UI did not rewrite these work fields: the loss was at extraction.
The prompt now preserves local project/context labels and roles, and distinguishes
continuation notes from separate entries. A mocked transport regression checks
this contract without depending on provider randomness or hardcoded project names.

Five live attempts on the same original PDF after the fix produced four successful
extractions with both contexts, separate Backend/Python roles and correct
current/freelance signals; one attempt timed out. A final check after the
continuation clarification also succeeded. No retry count, output budget, strict
schema, parser or privacy change was made. Structured diagnostic output was
temporary and was deleted; document contents were not added to permanent logs.

Real configured-provider smoke through existing Web/API at 1440/390 used a
synthetic DOCX/account: corrected work position/company, deleted one entry,
corrected a fact, reviewed refreshed counts and applied. Domain reads before
Apply were unchanged; persisted work/facts/Profile matched the edited preview.
Toast, URL cleanup, timed dismissal and refresh passed. A second import at each
width was edited/saved and cancelled; Profile stayed unchanged. Editors were
keyboard-labelled, Apply disabled during local edits, and no horizontal overflow
was observed. Synthetic account/Profile/session/preview state and temporary
documents/scripts/screenshots were removed. No duplicate stack or manual restart
was used; existing reload/hot reload applied code changes.

Final validation: targeted extraction 113 passed; targeted API import/privacy
174 passed, 1 skipped; targeted Web 64 passed; full API 1063 passed, 67 skipped;
isolated PostgreSQL concurrency 1 passed; full Web 533 passed. Lint, typecheck,
production build, Prettier and diff whitespace checks passed. Models, domain
migrations, dependencies and Bot production files were unchanged. The auth
coverage operation count was updated from 31 to 32 for the new protected endpoint.
