# Auth Slice 4: Telegram Web login and linking

Browser → Next BFF → FastAPI remains the only Web flow. Bot is a trusted thin
client authenticated with the separate Bot service credential. Configure the
API's public `TELEGRAM_BOT_USERNAME` (without @); no Bot token enters Next or Web.

## Proof and binding

`auth_telegram_challenges` stores SHA-256 digests of independent random 32-byte
challenge and browser binding secrets, purpose, initiating identity/session for
link, verified Telegram metadata, approval/consume times, outcome and five-minute
expiry. Only opaque `auth_<43-character-token>` enters the Telegram deep link.
The six-character code shown in Web/Bot is a comparison aid, not a credential.
Do not approve requests you did not initiate, or forward the link.

Next sets a separate five-minute HttpOnly/SameSite=Lax/Path=/ challenge cookie,
Secure + `__Host-` in production. Raw Cookie presence/duplicates are checked before
Next's decoded cookie abstraction. Binding never enters JSON, props or client
state. Each challenge has its own cookie name, suffixed with the validated opaque
challenge token (`job_hunter_telegram_dev_<token>` locally,
`__Host-job_hunter_telegram_<token>` in production). Pending attempts can coexist;
a terminal response deletes only its own cookie, even if another tab has already
created a newer attempt. Abandoned cookies expire with the five-minute challenge
TTL. The old shared challenge cookie is not accepted. Browser API responses
contain only the opaque reference,
link/code/expiry, status or safe user DTO. No numeric Telegram/users IDs, hashes,
Bot credential or AuthSession token leave the server.

## Contracts and purpose

Browser BFF POST `/api/auth/telegram/{challenges,status,complete}` all use the Slice
3 trusted-origin/JSON-only policy. Next supplies a binding secret and, for link,
Bearer from a real session. Dev identity is never used to authenticate link.
Backend counterparts are `/auth/telegram/{challenges,status,complete}`. Browser
metadata/Telegram ID input is forbidden. Status requires binding and returns only
pending/approved/expired/cancelled/conflict/consumed. Completion alone issues a
session; polling cannot create one.

Bot-only POST `/auth/telegram/bot/{inspect,approve,cancel}` requires Bot service
auth, rejects Bearer/dev/anonymous credentials, and receives identity only from
Telegram update/callback `from_user`. Ordinary `/start` is unchanged. Auth routing
runs first, never calls `/users/telegram` before deciding link. Confirmation is
private-chat only, with explicit approve/cancel buttons. Callback data contains
only action + comparison code; token stays in the current user/chat FSM context.
Old message IDs/codes and replay are rejected. Menu/command transitions remove
keyboards best effort; Telegram cleanup failure is logged without secrets.

## Accounts and session semantics

Login has no initiating account or Bearer. Completion finds the verified Telegram
identity, refreshes only existing Telegram metadata, and creates a fresh ordinary
AuthSession for the same `users.id`. If absent, it creates a Telegram-only User.
No Profile is created. Existing credentials/display name/domain data are preserved.

Link requires an active real email account session and password reauthentication
at challenge creation, because adding a login method grants permanent account
access. The challenge is pinned to that exact session/user; completion locks and
rechecks the session's validity. Its five-minute lifetime limits the reauth window.
Successful link attaches a free Telegram identity to the current row, preserves
credentials/domain data and keeps the initiating session. No rotation is needed:
no new browser privilege/session is issued, and both ownership proofs are required.
Same-account/same-Telegram link is idempotent. Changing an already linked identity
or taking another account's Telegram produces terminal `409 ACCOUNT_LINK_CONFLICT`.
No account ID/email/existence details are disclosed, no data copied or merged.

## Atomicity and lifecycle

Conditional approved/unconsumed/unexpired consume is the transaction's first
identity write. Consume + identity resolution/link + new login session commit
atomically. User row locks and unique Telegram ID constrain concurrent identity
writes; savepoints recover an insert collision or link conflict without poisoning
the transaction. SQLite serializes writers; one stale-snapshot lock retry reruns
the whole transaction. Conflict commits a terminal outcome but no user changes.
Consumed, cancelled, conflicted or expired challenges cannot be reused. Downgrade
revision `20261002_16` drops only challenges and preserves accounts/sessions/data;
initiator account/session deletion cascades its challenges. Expiry/initiator indexes
support future cleanup. No background cleanup worker is added; expired/terminal
rows can be removed by later maintenance.

Safe-next and normal session cookies are shared with email auth. Telegram-created
real sessions outrank explicit legacy dev identity; bad sessions never fall back.
Email/password auth, logout, CSRF and 503 semantics stay unchanged.

## Limits and rollout

Browser binding prevents completion by another browser that only knows the deep
link. It cannot prevent a user explicitly approving an attacker's own browser
request; the matching code and initiation warning reduce this phishing risk.
Bot service credential is a trust boundary and must remain server-only. FSM
confirmation context is ephemeral: after Bot restart reopen the deep link.

This slice requires a separate review before commit. Live successful linking/new
identity smoke needs a second Telegram account that is not already linked. With
one Telegram account, use existing-account login/conflict live smoke and isolated
fixtures for the remaining scenarios, recording the limitation.

Slice 5 removes transitional dev identity. Slice 6 covers email verification,
password reset and rollout hardening/rate limiting. Merge/disconnect/change identity,
OAuth/MFA/passkeys and Redis are out of scope. Auth v1 is not declared complete.

## Login UX

The Telegram-blue social login button follows the primary email submit button at
full card-content width with a decorative white icon and a 48px tap target. Login
reserves a blank tab synchronously in the user click handler, removes its opener,
then requests the existing challenge. On success that tab navigates to the exact
BFF-projected deep link. Failure closes the reserved blank tab. Popup blocking,
a closed tab or failed navigation leaves the valid challenge and manual fallback.
There is no async-only second `window.open`, and the client does not build a URL.

The original browser remains in compact waiting state with a secondary comparison
code and fallback “Открыть Telegram”. Existing polling/completion and safe-next
continue automatically. Expired/cancelled login offers retry. Linking retains its
manual opening and password reauthentication flow. Bot login copy names Job Hunter
AI and reports “Вход подтверждён. Вернитесь на сайт.” / “Вход отменён.”; ownership
checks and the warning to approve only an initiated request are unchanged.

## Validation record

Prior Slice 4 full API: 894 passed, 54 skipped. Extended SQLite/PostgreSQL
identity/auth/migration checks: 157 passed, 5 skipped; migration 16 runtime FK
checks: 12 passed. Backend production code and migration were unchanged by UX
polish; targeted Telegram auth contract checks: 17 passed.

UX polish full Web: 301 passed; full Bot: 518 passed. Targeted Web UI/form tests:
26 passed; targeted Bot auth/start/client tests: 175 passed. Web lint, typecheck,
production build and Prettier passed. Single Alembic head remains `20261002_16`.

Responsive browser checks at desktop, 1024px, 768px and 390px passed; at 390px
Telegram button is 284×48px with no horizontal overflow or label wrapping. A live
click opened the Telegram deep-link tab automatically while the original page
remained waiting. Popup blocking/error/closed-window fallback, retry and safe-next
are covered by UI tests; no security settings were changed to force popup blocking.
Live Bot cancellation was confirmed by the user; Web visibly received cancelled
state and offered retry. A fresh retry opened a new Telegram challenge automatically.

Earlier isolated real FastAPI + production Next HTTP smoke passed existing Telegram
account data/history preservation, new Telegram-only profile creation, linking,
both methods resolving the same email account, terminal conflict with unchanged
accounts, cross-account read/mutation 404, binding/replay/cancel/CSRF and safe
JSON/cookie projection. Synthetic fixture approvals are not live ownership proof.

The local database is now confirmed on revision 16 (the user ran `make dev` after
the earlier tool upgrade request was rejected). Existing-account identity/domain
checksums were captured before live smoke. The user confirmed live Bot approval
with matching code; Web automatically opened the old Sergey Profile. Existing Applications and application 71 status history
were inspected successfully. Read-only checks confirmed the completed challenge
resolved existing `users.id=4`, one User for the Telegram identity, an ordinary
AuthSession, and unchanged checksums for every user's identity/credentials,
Profile, Applications and history. The user confirmed manual DevTools checks:
local session cookie HttpOnly/SameSite=Lax/Path=/ with Secure=false, challenge
cookie removed on completion, no internal identity/session/digest/password-hash/
binding or service credentials in browser JSON/transport. Cookie values visible
to the browser owner and the opaque deep-link reference are expected.
Only the primary real Telegram is available; live new-user/free-link scenarios use
the prior integration/concurrency coverage, with no second real account fabricated.

## Leaving login waiting state

“Отменить вход” is a keyboard-accessible secondary button only in login waiting
state; linking UI and semantics are unchanged. It synchronously aborts the status
request/timer and invalidates the effect before a late approval can trigger
completion, clears client state, announces cancellation and restores focus to the
Telegram action. Cancellation is disabled once completion has started: a session
issuance already dispatched cannot be safely undone by local UI cancellation.
Successful completion, terminal responses/errors and unmount all stop polling.
The cadence remains one request every two seconds, after the prior request settles.
No external app/tab closing detection is added.

Bot-only cancellation cannot be called by Web without crossing its credential
boundary. POST `/api/auth/telegram/cancel` therefore forwards the existing token
and HttpOnly binding to browser POST `/auth/telegram/cancel`; API validates the
same login ownership binding/purpose and invokes the same conditional terminal
cancel write used by Bot. No Telegram identity comes from Browser, no Bot token
enters Next, and Bot retains its verified-actor restriction. DB model/migration,
TTL, approval, linking, identity resolution and session issuance are unchanged.

BFF validates trusted origin, JSON, login purpose and matching raw binding cookie
before attempting cancellation. The API call has a 1.5-second deadline. BFF clears
the challenge cookie even on API failure and returns only safe cancellation
confirmation, without touching the ordinary session cookie. Client returns to the
login UI immediately; a new attempt waits for the old cookie-clearing response
(with a three-second client deadline) before creating its replacement. Each
response deletes only its own challenge cookie, so stale status,
complete and cancel responses cannot delete a newer attempt's binding. Ordinary
AuthSession cookies remain shared across tabs: the existing rule disallowing
login while authenticated is unchanged; linking retains its initiating session.
If cancellation cannot reach API,
local polling stays stopped and the backend challenge expires at the existing TTL.

Validation after this fix: full Web 319 passed; targeted UI/BFF 52 passed; API
contracts 26 passed; full API 904 passed, 55 skipped. SQLite/PostgreSQL concurrency
checks including cancel-vs-completion: 8 passed. Web lint, typecheck, production
build, Prettier and diff checks passed. Bot production code was not changed in this
fix; the prior 518-pass suite remains its validation reference.

Live Web cancellation after closing the external Telegram tab returned login UI
and focus immediately. The user confirmed no additional status requests over
10–15 seconds and removal of the challenge cookie. DB checks confirmed terminal
cancel without approval or AuthSession issuance. Keyboard retry created a fresh
challenge; Bot cancellation visibly exited waiting. Final live approval opened the
old Profile automatically, and the user confirmed no further polling after success.

## Attempt-scoped binding cleanup review fix

The former shared cookie allowed an in-flight response for A to delete the newer
binding for B. Cookie names now include the validated opaque challenge token;
status terminals, terminal errors, successful complete and browser cancel can
only delete their own name. No request-cookie snapshot comparison is relied on
to determine which binding currently exists in the browser. The value still
contains independent secret binding material and purpose; all existing raw-cookie
validation and security attributes are retained. API/model/migration and Bot
production code are unchanged.

Deterministic Next BFF regression tests pause A after cookie validation, create B,
release A and apply its response cookies to the same browser jar. They cover login
and link terminal statuses/errors, successful completion, cancellation and cancel
outage, both terminal orders, missing/wrong/legacy bindings and production cookie
attributes. B's proof survives and remains usable; login after A has authenticated
uses the existing logout prerequisite, while linking preserves the initiating
session. No sleep-based race is used.

Validation: targeted Web UI/BFF 77 passed (25 new binding regression cases); full
Web 344 passed; lint/typecheck/production build/Prettier passed. Targeted Telegram
API contracts/concurrency 34 passed, including SQLite and PostgreSQL. PostgreSQL
was rerun outside sandbox after its initial connection was blocked. Full API/Bot
suites were not repeated because their production code was unchanged.
