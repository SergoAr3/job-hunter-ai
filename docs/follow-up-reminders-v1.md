# Follow-ups / Reminders v1

Notes keep vacancy context; Next Action records what the user wants to do next;
Reminder adds one optional notification time to that action. Nothing is scheduled
automatically. Delivery leaves the action open. **Выполнено** clears action,
exact reminder and legacy date, preserving notes and application status.

## Storage and migration

Additive Alembic `20261006_19` follows `20261005_18`. The one-to-one
`application_reminders` table has eleven fields: application_id (PK and cascading
FK), remind_at, timezone, generation, delivery_state, attempt_count,
next_attempt_at, lease_token, lease_until, sent_at, last_error_code. Checks bound
states, attempts (0–3), errors and lease/sent combinations. Time columns use
timezone-aware metadata (PostgreSQL timestamptz). There is no revision, history,
send-started flag, outbox, generic job table or scheduler dependency.

Existing applications and `next_action_due_on` dates are untouched; there is no
backfill. Downgrade refuses while any reminder row exists. Removing the reminder
table requires first explicitly resolving its data. SQLite migration tests verify
legacy preservation and constraints; PostgreSQL tests verify actual timestamps,
schema and locking.

## API and transactional semantics

Existing `PATCH /users/{user_id}/applications/{application_id}` accepts note,
next_action, next_action_remind_at and next_action_timezone. Missing means unchanged;
null/blank action clears both schedules; null remind_at removes only the exact
reminder. Text-only and status edits preserve schedule and delivery state.
The common lifecycle service locks **Application → Reminder**. No-op PATCH does
not change generation or re-arm sent/failed delivery. Real reschedule resets
attempts and delivery state, creates a new UUID generation and clears legacy date.
Creating a schedule requires action text and an instant at least 60 seconds away.

Legacy Bot PUT still writes date-only actions. It returns safe 409
REMINDER_LEGACY_CONFLICT when an exact reminder exists; legacy DELETE clears all
action scheduling. Date-only entries remain visible and never cause Telegram send.
Bot displays exact local times, accepts nullable legacy dates and directs exact
editing to Web. Bot has no database or delivery code.

Responses project only exact time, timezone, delivery state, sent_at and a public
failure reason (uncertain / telegram_not_connected / unavailable). Generations,
leases, attempt counts and Telegram IDs never cross the Web boundary. BFF uses
the authenticated session, explicit body/query whitelists, Origin checks and
no-store responses. Errors expose bounded codes, never rejected input/provider data.

## Timezone and Web

The browser detects its IANA timezone. Native date/time controls encode RFC3339
with explicit offset and IANA zone; the backend validates zoneinfo round trips,
offset agreement and normalizes to UTC. Both nonexistent DST gap inputs and
ambiguous repeated-hour inputs are rejected with guidance to choose another time.
If detection fails, manual ListboxSelect selection is required. No server timezone
or user-wide timezone preference is assumed. Detail shows the chosen schedule's
timezone; list and Dashboard show browser-local time.

The two independent Notes / Next Action accordions keep their draft, explicit
Save/Cancel, submission lock and focused header after completion. Helper copy
explains each field. Deterministic backend suggestions use canonical statuses;
clicks only change action draft. The 3/5-day suggestions do not invent a time.
Done is disabled while dirty. Removing reminder changes the draft until Save.
An uncertain PATCH requires fresh GET and canonical comparison of intended fields
(including reminder instant and timezone), without automatically replaying mutation.

Applications list uses a one-to-one LEFT JOIN, avoiding per-row reminder reads.
`sort=next_action` orders exact, legacy dated, undated actions, then the rest,
with stable created/id tie-breakers. Existing follow-ups endpoint accepts timezone
and all/overdue/today/upcoming bucket; local midnight is converted to UTC using
zoneinfo, so a local day can be 23 or 25 hours. Sent actions stay in this workflow.
Dashboard requests three bounded groups of at most five items. Undated actions
remain on detail/list without an invented due time. Wording says the reminder
time has passed, never that the user missed a deadline.

## Worker and Telegram delivery

Run `python -m app.workers.reminders` from apps/api with existing database settings,
TELEGRAM_BOT_TOKEN, WEB_PUBLIC_ORIGIN and APP_ENV. `--diagnostics` is a finite
read-only command reporting due backlog, oldest lag and expired claims.
Production requires a trusted HTTPS origin; development allows localhost HTTP.
Invalid credentials/configuration stop the worker rather than exhausting user
retries. Live execution requires PostgreSQL. A session advisory lock prevents an
accidental second process for the same database; domain claims independently
support competing consumers and are tested without this singleton guard.

The separate supervised backend process imports neither FastAPI nor Bot FSM.
It polls approximately every 15 seconds, processes up to five claims, paces
requests at least one second apart, uses 120-second leases and a 10-second provider
deadline (3-second connect timeout). It never sends before remind_at. Normal
delivery delay includes polling and provider latency; second-level precision is
not promised.

Claims use PostgreSQL FOR UPDATE OF applications SKIP LOCKED, then lock the
reminder. They increment attempts, assign a new lease token and commit before
network I/O. A separate fresh preflight verifies owner, action, generation,
lease and destination. Destination is exclusively Application.user_id →
User.telegram_id. No database transaction remains open during send. Completion
is fenced by application, generation, lease and claimed state; stale results
cannot modify a newer schedule. A known provider result may be persisted up to
three times after database failure without sending again.

Telegram messages contain only bounded vacancy/company/action, chosen local
time/zone and trusted application link. They are plain text with previews off;
no notes, CV, email or matching data. The existing httpx version is declared as
a production dependency instead of only a dev dependency.

Safe connect failures and confirmed 429 rejection can retry: roughly 1 minute,
then 5 minutes, respecting longer retry_after, maximum three attempts per
generation. Delivery is never begun more than 24 hours after the original time.
Blocked/unavailable chats fail permanently. A 401 stops the worker. Read/write
failure, timeout after possible transmission, malformed/unconfirmed response or
generic provider error fails as DELIVERY_UNCERTAIN. **No automatic resend**.

Expired claims after restart also become terminal uncertain failures. UI says
“Не удалось подтвердить отправку. Напоминание могло прийти.” and
“Автоматически повторять не будем.” An unlinked account keeps the saved action
and reminder; when due, preflight fails without provider I/O. Late linking does
not re-arm failed backlog. A new schedule requires explicit user rescheduling.

Structured events include claim, result/retry/failure, stale completion, lease
recovery and heartbeat. Fields are limited to internal application ID, generation,
safe code, latency and queue counts/lag. HTTP client logging is suppressed to
avoid token-bearing URLs. No provider payload, destination or user text is logged.

## Local runtime and limitations

`make dev` now supervises API, Bot, Web and Reminder worker after PostgreSQL and
migrations. Its existing process-group cleanup terminates all four services.
API/Bot/Web retain existing reload behavior. Worker does not reload on edits:
an automatic restart during delivery would create an uncertain claim. When
changing worker code, stop its one existing process gracefully and start it again;
for the standard runner, stop/start the one dev runner. Never start a duplicate
stack or worker. The advisory lock is a defensive guard, not permission to do so.

V1 accepts existing multi-tab last-write-wins behavior. A user edit after preflight
but during provider I/O cannot revoke an already transmitted notification;
generation fencing protects stored completion, not Telegram cancellation.
A crash can lose a notification because expired leases never automatically
re-send. No recurring reminders, snooze, multiple reminders, history, email,
calendar, tasks page, AI scheduling or notification framework is included.

## Validation

Run suites sequentially. API lifecycle/migration/provider tests use injected
clocks and fake HTTP; no automated test sends real Telegram messages.
`RUN_REMINDERS_POSTGRES=1 .venv/bin/python -m pytest -q tests/test_reminders_postgres.py`
uses a temporary unique schema on the configured PostgreSQL connection and removes
it afterward. It covers competing consumers, SKIP LOCKED, no double claim,
reschedule/clear/done during claim, stale completion, expiry and migration refusal.
The full default API suite skips opt-in PostgreSQL/source/LLM tests.

Web tests cover timezone/DST encoding, editor draft/save/cancel/done/recovery,
safe BFF projection and identity, summary delivery states and Dashboard groups.
Bot tests cover exact nullable-date contracts and real dispatcher behavior.
Run full suites, Web lint/typecheck/build/Prettier, runner cleanup test and
`git diff --check`. Manual/visual evidence and exact counts are recorded after
the existing-runtime smoke below.

## Existing-runtime smoke and visual evidence — 2026-10-06/07

Reused the original make dev runner (PID 39828), API reload parent 39860,
Bot watcher 39882, Next server 39923 on port 3100 and the one healthy PostgreSQL
16 Docker container on port 5432. The additive migration was applied to head 19.
No duplicate API/Bot/Web/PostgreSQL or runner was started. Existing hot reload
picked up edits. The one repo worker was started for smoke, gracefully stopped,
then restarted alone for recovery and stopped again. At no time were two live
workers running. PostgreSQL concurrency used only finite disposable-schema tests.

Five disposable Applications/Jobs were created under the existing own linked
and unlinked accounts. Existing user data and identity/linking were preserved.
Temporary authenticated sessions were file-backed with 0600 permissions and
never printed. One necessary real notification was sent, confirmed by Telegram
and visually observed in the own Bot chat. Before the new instant, including
past the replaced old instant, attempt_count stayed zero. Delivery occurred about
2 seconds after the scheduled instant; multiple later polls kept one attempt.
Action, note and status remained. Text edit after sent did not re-arm; clear
reminder kept action; Done removed action and both schedules, retaining note/status.

Unlinked preflight failed without network send. Legacy date stayed readable and
caused no reminder claim. Dashboard attention/today/upcoming, timezone near local
midnight, list sorting and browser clear/Done were checked against the live BFF.
Foreign application, anonymous access and cross-Origin mutation were rejected.
A streamed PATCH response body was intentionally discarded, then fresh GET
confirmed canonical action/instant/timezone without mutation replay. The editor's
actual uncertain-response button workflow is covered by deterministic Web tests.

Restart recovered one explicitly prepared disposable expired claim to terminal
DELIVERY_UNCERTAIN, with no resend; the live UI displayed both uncertain messages.
Actual API responses passed the production Bot client and detail/queue renderers
for exact nullable dates and legacy dates. Native Telegram screenshot confirmed
receipt. Native Bot button clicks could not be performed because the computer-use
backend reported no available window; dispatcher-level tests cover routing instead.
This is a manual GUI verification limitation, not a reproduced feature defect.

Desktop 1440, tablet 768 and mobile 390 layouts were inspected. Mobile editor,
list and Dashboard had scrollWidth = viewport width. Date/time labels, error
associations, focus after Save/Cancel, dirty Done, helper text and suggestion draft
behavior were checked. PNG evidence is retained only in
/tmp/reminder-smoke-evidence (collapsed detail, editor, sent, uncertain, Dashboard,
list, including mobile). The Web account used for visual review is unlinked;
its disposable sent screenshot fixture mirrored the confirmed real delivery's
public projection, and did not send a second notification.

All five Applications/Jobs, reminder rows and temporary auth sessions were removed.
Private state and both finite smoke scripts were deleted. Worker is stopped and
finite diagnostics report due_backlog=0, oldest_due_lag_seconds=0, expired_claims=0.
Original runtime remains running; viewport and original Applications tab were
restored. The currently running old dev runner needs its normal next stop/start
before it supervises the newly integrated worker; it was deliberately not restarted.

Validation (sequential large suites): API targeted 53 passed; final full API
1161 passed / 80 opt-in tests skipped; isolated PostgreSQL 11 passed; full Bot
526 passed (targeted compatibility 203 passed); full Web 655 passed. Web lint,
typecheck, production build and Prettier passed. Dev cleanup checks passed for
SIGTERM, SIGINT, child failure and startup race across all four process groups.
Git diff --check passed. No commit, push or PR.

## Files changed

- `RUN_LOCAL.md`
- `apps/api/alembic/versions/20261006_19_application_reminders.py`
- `apps/api/app/main.py`
- `apps/api/app/models.py`
- `apps/api/app/schemas.py`
- `apps/api/app/services/action_suggestions.py`
- `apps/api/app/services/applications.py`
- `apps/api/app/services/reminders.py`
- `apps/api/app/services/telegram_notifications.py`
- `apps/api/app/workers/__init__.py`
- `apps/api/app/workers/reminders.py`
- `apps/api/requirements-dev.txt`
- `apps/api/requirements.txt`
- `apps/api/tests/test_applications_list.py`
- `apps/api/tests/test_reminder_migration.py`
- `apps/api/tests/test_reminders.py`
- `apps/api/tests/test_reminders_postgres.py`
- `apps/api/tests/test_telegram_auth_migration.py`
- `apps/api/tests/test_telegram_notifications.py`
- `apps/bot/app/api_client.py`
- `apps/bot/app/applications.py`
- `apps/bot/tests/test_reminder_compatibility.py`
- `apps/web/app/(workspace)/applications/[applicationId]/page.tsx`
- `apps/web/app/api/applications/[applicationId]/route.ts`
- `apps/web/app/api/applications/follow-ups/route.ts`
- `apps/web/app/globals.css`
- `apps/web/components/application-notes-editor.tsx`
- `apps/web/components/application-status-detail.tsx`
- `apps/web/components/applications-list.tsx`
- `apps/web/components/dashboard-follow-ups.tsx`
- `apps/web/components/dashboard.tsx`
- `apps/web/components/reminder-summary.tsx`
- `apps/web/lib/browser-timezone.ts`
- `apps/web/lib/contracts.ts`
- `apps/web/lib/errors.ts`
- `apps/web/lib/reminder-time.ts`
- `apps/web/lib/server/api.ts`
- `apps/web/tests/application-notes-editor.test.tsx`
- `apps/web/tests/applications-list.test.tsx`
- `apps/web/tests/reminder-editor.test.tsx`
- `apps/web/tests/reminder-time.test.ts`
- `apps/web/tests/reminder-transport.test.ts`
- `docs/follow-up-reminders-v1.md`
- `scripts/dev.sh`
- `scripts/tests/test_dev_cleanup.sh`

## UX polish — 2026-10-08

- «Убрать напоминание» depends on the confirmed exact reminder, never on an
  unsaved date/time draft. Removal remains draft-only until explicit Save;
  Cancel restores the confirmed schedule.
- Action-only Save sends only `next_action`. Partial reminders identify the
  missing date or time; action, reminder and timezone errors have separate
  ownership and accessible descriptions. Reminder errors sit immediately below
  the date/time group. Transport/recovery errors do not mark valid inputs red.
- Existing `pageAccess().user.telegram_linked` supplies the safe boolean to
  detail, Applications list and Dashboard presentation. No API fields,
  identifiers, client storage inference or additional requests were added.
- An unlinked reminder draft explains that the schedule appears only in Job
  Hunter AI and invites Telegram connection. Pending/claimed read state says
  «Запланировано только в Job Hunter AI». Linked drafts say «Уведомление придёт в
  Telegram.» and read state says «Уведомление запланировано». Sent and failure
  wording is preserved. The timezone remains visible and muted.
- Validation: targeted reminder/editor/list/transport/time suites **79 passed**;
  full Web **674 passed / 43 files** (including **19 new UX cases**); lint,
  typecheck, production build, scoped Prettier and `git diff --check` passed.
  API/Bot tests were not rerun for this Web-only round.
- Manual Chrome smoke at **1440 × 1000** and **390 × 844** checked action-only
  Save, date-only/time-only errors, full unsaved draft without Remove, persisted
  Remove after reopening, draft removal with Cancel, both Telegram helper/read
  states, and no horizontal overflow. Connected copy used an isolated disposable
  server-linked test identity, not a delivery test. Applications list and
  Dashboard disconnected read copy were also checked.
- Evidence is outside Git: `/tmp/reminder-ux-evidence/`. Both temporary jobs,
  applications, reminders, the linked test account and its sessions were removed.
  The original user application was not edited.
- Existing runtime was reused: one Web, API, Bot, PostgreSQL container and an
  already-running reminder worker. No process was launched or restarted for this
  round, and the worker remained running. No Telegram delivery smoke was repeated.

## Optional reveal polish — 2026-10-08

- An action editor without a confirmed exact reminder starts compact, with
  «+ Добавить напоминание» instead of date/time, timezone and Telegram helper.
  Reveal is local presentation state: it does not make the form dirty or send
  requests. Suggestions do not reveal it.
- Revealed empty date/time remains valid for action-only Save. Save/Cancel reset
  reveal to the confirmed state; existing exact reminders always open with
  controls visible. Staged removal still requires Save and Cancel restores it.
- The exact read-summary is rendered only while the action accordion is closed.
  Open editors retain sent/failed/uncertain delivery information once, without
  repeated date/timezone. Pending/claimed editors use inputs and helper only.
- Add and Remove are compact text actions. Save, Cancel and Done share the
  action-level actions row; Done retains existing dirty-state blocking.
- Validation: targeted reminder/editor suites **58 passed**; full Web **684
  passed / 43 files**, with **10 additional reveal/status cases** and adapted
  prior UX regressions. Lint, typecheck, production build, scoped Prettier and
  `git diff --check` passed. API/Bot suites were not rerun; their production code
  and contracts were unchanged in this round.
- Manual Chrome smoke at **1440 × 1000** and **390 × 844** verified compact
  action-only editor, suggestion without reveal, Add without persistence,
  empty-reveal action-only Save, persisted controls/Remove, no open read-summary,
  read-summary on close, Cancel restoring staged removal, and both server-derived
  Telegram helpers. Mobile compact/revealed/persisted and linked layouts had
  `scrollWidth === 390` with a 390 px viewport. Screenshots are outside Git in
  `/tmp/reminder-reveal-evidence/`.
- Connected helper used a disposable server-linked test identity. No Telegram
  delivery was attempted. Temporary applications/jobs/reminder/account/sessions
  were removed; the original user application was not edited. The original tab
  and normal viewport were restored.
- Existing single Web/API/Bot/PostgreSQL/worker runtime was reused without
  launches or restarts. The already-running worker remained running.

## Text-action state polish — 2026-10-08

- Shared `button.text-action` styles Add/Remove reminder and the matching
  non-destructive Edit/Cancel actions in work experience. Navigation links,
  profile outlined/add controls and destructive work deletion retain their styles.
- Normal is violet on transparent; hover uses a subtle violet tint; active uses
  a stronger tint. There is no border, underline or button shadow. Keyboard
  focus-visible has a controlled 2 px violet ring; disabled remains muted and
  transparent. The pattern has 44 px minimum height and horizontal padding.
- Native appearance, WebKit text selection and tap highlight are explicitly
  reset. Text contrast on white: normal **4.96:1**, hover **5.51:1**, active
  **5.10:1**; hover/active use the existing darker accent token.
- No component structure, handlers, ARIA or business behavior changed. Two
  existing shared-class assertions were updated; no CSS snapshots were added.
  Targeted reminder/editor/work-experience tests: **89 passed**. Full Web:
  **684 passed / 43 files**. Lint, typecheck, production build, scoped Prettier
  and `git diff --check` passed.
- Chrome live-app smoke at **1440 × 1000** checked Add and Remove normal, hover
  and keyboard focus, Tab/Shift+Tab, Enter/Space, and Cancel after staged removal.
  Both actions measured **44 px** tall at **390 × 844**, with scrollWidth 390.
  Screenshots: `/tmp/text-action-evidence/`.
- Safari checked the same production CSS in a local static specimen, without an
  additional server: normal/disabled appearance, Add hover and keyboard ring.
  Selection testing prompted the WebKit prefix fix. Subsequent native window
  input became unavailable, so the post-fix selection retest and separate Remove
  hover were not completed in Safari. The specimen tab was closed via its
  accessibility action; existing Safari tabs were preserved.
- One disposable future reminder/application/job was used for Remove smoke and
  removed afterward. No user application was saved or changed, no Telegram
  message was sent, and the existing Web/API/Bot/PostgreSQL/worker processes were
  reused without launching duplicates or restarting them.

## Final review fix pass — 2026-10-09

- Suggestion buttons use the existing darker accent token. Computed contrast is
  **5.51:1** on the normal subtle background and **6.02:1** on the existing hover
  background. Active uses the same foreground/background rules; focus ring and
  disabled behavior retain their existing styles. No CSS snapshots or tokens added.
- User-triggered Add reminder reveal focuses the date input after rendering.
  Enter, Space and click work; persisted editor opening and unrelated draft
  rerenders do not autofocus. Save/Cancel still return focus to the action header.
- Internal worker completion returns a frozen safe state/error result only after
  commit, or no result when fenced out. Worker logs the persisted state: pending
  retry, sent, failed or uncertain. Retry exhaustion and expired-lease completion
  now report their actual terminal code instead of the provider classification.
  Delivery, retry, generation, lease and public API contracts are unchanged.
- Five Web regressions cover activation/focus and no-autofocus behavior (JSDOM
  models native keyboard activation with a click; actual Enter/Space are checked
  in Chrome). Five fake-sender/injected-clock worker cases check final-state
  mapping and log field whitelists, including both review reproductions.
- Validation: targeted Web **63 passed**; full Web **689 passed / 43 files**;
  targeted reminder/provider **49 passed**; isolated PostgreSQL **11 passed**.
  Web lint, typecheck, build, scoped Prettier and diff checks passed. Full API/Bot
  were not repeated: completion has only the internal worker production caller,
  and public API/Bot surfaces are unchanged.
- Chrome checked suggestion normal/hover/focus and Enter/Space/click reveal,
  persisted opening, staged removal/Cancel and text Save/focus return. Evidence:
  `/tmp/reminder-final-fix-evidence/`. One disposable future application/job/reminder
  was removed afterward; original user data was not saved and no Telegram send ran.
- Existing runtime was reused. The live worker was neither restarted nor
  duplicated; it picks up the logging fix on its next normal restart. Updated
  worker code was checked through finite fake-sender tests, not a live send.
  `docs/auth-slice3.md` remains unrelated and excluded from this feature scope.
