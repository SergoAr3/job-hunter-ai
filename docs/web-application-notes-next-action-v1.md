# Application Notes / Next Action v1

The Applications detail workspace edits the existing canonical `note` and
`next_action` fields. Notes remain limited to 1000 Unicode characters; actions
to 500. Both are optional user-entered text. There is no `notes` alias.

`PATCH /users/{user_id}/applications/{application_id}` accepts only `note` and
`next_action`. Missing fields remain unchanged; explicit `null` or whitespace-only
text clears the field. Leading/trailing whitespace is trimmed before length
validation. Types are strict; NUL and unknown fields are rejected. Empty PATCH
is an owned, idempotent no-op. Both supplied fields commit together, with rollback
on failure. Status, status history, matching and job data are unchanged.

An action can exist without a date. An orphan date is still forbidden. Editing
an action preserves an existing `next_action_due_on`; clearing it also clears
the date. Legacy Bot PUT/DELETE endpoints remain available and keep their
existing validation. Existing dated actions and notes are preserved by migration
`20261005_18`. Downgrade refuses while undated actions exist rather than deleting
text or inventing dates.

Bearer authorization and application ownership follow existing API policy. Web
BFF derives user identity from the server session, checks mutation origin and
projects responses; browser-supplied identity/date/status fields are rejected.
Validation errors become safe, plain-language Web messages.
The direct PATCH returns a bounded 422 `detail.code = APPLICATION_INVALID`
without rejected input or Pydantic error details. Legacy validation responses
remain unchanged.

The editor uses explicit Save/Cancel, local dirty state, a submission lock and
accessible labels/hints/status/errors. Only edited fields are submitted; success
uses confirmed API values. A lost response blocks another mutation until a
read-only recovery loads current values. Existing dates are displayed without
a date editor. Next action is shown in list rows, clamped to two lines; full notes
remain detail-only. Dashboard summary is unchanged.
Status confirmation refreshes only status and cannot overwrite locally confirmed
CRM fields. Compact save/recovery feedback expires after four seconds; errors
remain until user action. Each section owns and cleans up its feedback timer.
Bot detail and current-action edit prompts also display undated actions, while
the existing Bot date-entry and PUT/DELETE flow remain unchanged.

Reminders, dates editing, notifications, scheduling, activity feeds, multiple
notes, AI suggestions and new Telegram commands are out of scope.

## Initial implementation validation — 2026-10-05

- Targeted Application checks: 155 passed; targeted Auth: 79 passed, 3 skipped.
- Final full API: 1092 passed, 68 skipped (existing opt-in/environment checks,
  including the separately executed new PostgreSQL concurrency check).
- Isolated PostgreSQL PATCH/concurrency and relevant migration regressions:
  59 passed. Full Alembic upgrade from an empty PostgreSQL schema, legacy-data
  preservation, latest downgrade/upgrade and safe undated-action downgrade
  refusal passed. Exactly one head: `20261005_18`. Disposable schemas removed.
- Targeted Web Applications/BFF/editor: 57 passed; full Web: 562 passed.
- Bot compatibility: 66 passed; Bot production code unchanged.
- Web lint, typecheck, production build, full Prettier and diff whitespace check
  passed. No dependencies added.
- Browser smoke through real Web/BFF/API/PostgreSQL at 1440 and 390: add both
  fields, save/reload, note-only PATCH, preserve action, clear/reload and intact
  status/history/job. Existing legacy date survives action text edits and clears
  with the action. 768 viewport also checked. Focus outline, wrapping, list
clamping and no horizontal overflow verified; browser errors absent.
- Screenshots visually inspected; no screenshot test snapshots. An initial
  repeat attempted input before React was ready after navigation. Final browser
  checks waited for network readiness and exercised the interactive status
  control before filling; submitted payloads and reloaded values matched.
- No runtime was active initially. Started one repository API/Web runtime and
  the existing Compose PostgreSQL container. API required one restart for a
  temporary server-side Bot service credential; `.env` unchanged. No Bot or
  duplicate app runtime started. Finite browser drivers closed. Synthetic
  account/application/job/history/session data and temporary credentials/scripts
  cleaned up. The API/Web processes were stopped after validation; PostgreSQL
  was returned to its initial stopped state. Screenshots remain in `/private/tmp/notes-{smoke,list}-*.png` for
  visual review.

## Focused review fixes validation — 2026-10-05

- API targeted Application/security/auth: 157 passed, 8 existing opt-in skips;
  full API: 1095 passed, 68 existing opt-in/environment skips.
- Bot targeted rendering/action/client checks: 220 passed; full Bot: 523 passed.
- Web targeted Applications: 83 passed; full Web: 578 passed. Deterministic
  deferred GET tests cover note/action saves and action/date clearing. Fake-clock
  tests cover transient feedback, repeat Save, StrictMode cleanup and persistent
  errors. Lint, typecheck, production build, Prettier and diff checks passed.
- Existing localhost runtime smoke at 1440/390 confirmed late real status GET
  does not replace saved note/action/date, while status still updates. Note/action
  success feedback disappears after four seconds; summaries remain. Legacy dates
  survive action/note edits and clear with the action. Oversized direct PATCH
  responses are 41 bytes, contain no rejected input and retain BFF error mapping.
- Production Bot renderer shows an undated application read from the real API;
  dispatcher tests cover current prompts, Cancel, edit and delete. No Telegram
  smoke messages were sent. No new Bot flow or reminders were introduced.
- Reused the existing `make dev` API/Web/Bot/PostgreSQL processes, with automatic
  code reload only. No additional app runtime or manual restart. Finite browser
  drivers closed; synthetic rows, credentials and scripts removed. Screenshots
  remain outside Git in `/private/tmp/notes-fix-{1440,390}.png`.
- `next-env.d.ts` matches base; the automatic dev-types diff is removed.

## Files changed

```text
apps/api/alembic/versions/20261005_18_application_text_next_action.py
apps/api/app/main.py
apps/api/app/models.py
apps/api/app/schemas.py
apps/api/app/services/applications.py
apps/api/tests/test_application_next_actions.py
apps/api/tests/test_application_patch.py
apps/api/tests/test_application_patch_postgres.py
apps/api/tests/test_application_text_next_action_migration.py
apps/api/tests/test_applications_list.py
apps/api/tests/test_auth_security.py
apps/api/tests/test_email_auth_database.py
apps/api/tests/test_telegram_auth_migration.py
apps/bot/app/applications.py
apps/bot/tests/test_application_next_actions.py
apps/web/app/api/applications/[applicationId]/route.ts
apps/web/app/globals.css
apps/web/components/application-detail.tsx
apps/web/components/application-notes-editor.tsx
apps/web/components/application-status-detail.tsx
apps/web/components/applications-list.tsx
apps/web/lib/client.ts
apps/web/lib/contracts.ts
apps/web/lib/errors.ts
apps/web/lib/server/api.ts
apps/web/tests/application-notes-editor.test.tsx
apps/web/tests/application-patch-transport.test.ts
apps/web/tests/application-status-detail.test.tsx
apps/web/tests/applications-list.test.tsx
apps/web/tests/applications-transport.test.ts
docs/web-application-notes-next-action-v1.md
```
