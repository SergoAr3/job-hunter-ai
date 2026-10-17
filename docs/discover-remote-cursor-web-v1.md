# Discover remote-only Web cursor migration v1

The new Web remote-only submit uses the continuation feed API. Normal search,
Bot, and existing remote links containing an explicit `offset` retain the legacy
API. No backend/schema/source foundation changes are part of this slice.

## URLs and confirmation

- `/discover?query=Python` uses legacy normal search.
- `remote_only=true&offset=20` (including explicit zero) uses legacy compatibility.
- `remote_only=true&cursor=<32-character opaque token>` uses GET replay.
- Remote query without either parameter is a start candidate. Opening/refreshing
  it does not silently POST; the user confirms with Submit.
- Cursor and offset together, duplicate/malformed cursor, or cursor outside
  remote mode normalize safely to the idle Discover URL without an API search.

URL query/filter/cursor/selection remain authoritative. Draft edits do not fetch.
An explicit remote Submit creates one cursorless history entry and POSTs start;
its successful response seeds the display and replaces that entry with the
server-issued replayable cursor. This includes initial paused/end outcomes.
A successful advance with a page pushes one new cursor entry and seeds its DTO.
Neither URL update triggers an immediate GET. Selection uses replace semantics.

## Forward and backward actions

`next_action` directly controls Next / Continue / no forward action. POST always
uses `next_cursor`, never the visible page's cursor. A paused/end advance keeps
current items, selection and URL, updating only loaded navigation metadata.
That update uses the latest visible page, preserving concurrent successful Save
reconciliation; it does not restore an earlier item snapshot.
There is no polling, prefetch, automatic retry, or client-side paused history.
After refresh, one GET of the visible cursor restores latest continuation/end
metadata through the backend resolver. Previous and browser Back/Forward each
replay with one GET; they never POST or calculate offsets in cursor mode.
A completed successor can be recovered by explicit idempotent POST after a lost
response; the returned page is then installed at its own visible cursor.

## BFF and errors

Feed helpers/route are separate from the legacy Discover helper. The BFF validates
and explicitly projects feed/display fields, including opaque token syntax,
items/count/state consistency, nullable links/action and timezone-bearing expiry.
Server identity/Bearer handling stays in the existing auth boundary. POST requires
the existing same-origin JSON mutation check. Responses are no-store.
Timeouts are 30 seconds for POST and 10 seconds for replay, without retries.
These are Web wait limits, not a promise of hard source/DB cancellation.

Feed-specific unknown/expired errors map to unavailable results with an explicit
restart. Unrelated 404 stays a generic feed failure. DB lock/query/active-claim
conflicts map to manual retry; source errors retain their safe known codes.
Backend internals are not forwarded. A validated retry cursor from failed start
is retained for explicit retry of that same logical action. If the start response
is lost before any token is delivered, Web cannot recover an unknown token;
an explicit retry may create a new TTL-limited chain. No hidden restart occurs.

Next/Continue failure leaves current cards/URL intact and retries the same token.
Generation guards and aborts prevent late start/advance/replay responses from
changing a newer URL, result, selection or feedback. StrictMode reuses one pending
GET; real unmount closes its client request. Abort does not guarantee that already
running BFF/API work stops immediately. The backend retains fencing/idempotency.

## Presentation and return

Existing cards, Save and matching presentation are reused. Slow feedback starts
after 2800ms; a pending advance keeps disabled stale cards and lower feedback.
Replay uses neutral loading copy, no source-slow message, no skeleton/minimum wait.
Only one loading live region is active; lower feedback is aria-hidden. Paused/end
use compact inline status. Cursor mode has no arithmetic page number. Keyboard
navigation and existing reduced-motion behavior are preserved.
When a focused forward action becomes terminal, focus moves to the terminal
status, unless the user moved focus elsewhere while waiting. Passive replay does
not move focus. Legacy direction copy compares offsets only within the same
confirmed query/filter identity.

Next development incoming request logs selectively suppress cursor-bearing
Discover/feed lines and Application/auth lines carrying the expected encoded
`from`/`next` cursor URLs (up to two encoding layers). Ordinary request logs stay
enabled. Application code does not log cursors. This is development request-log
suppression, not production redaction; outer proxy/platform logging is outside
this PR. Actual request URLs and navigation parameters are unchanged.

Application `from` stores the current visible cursor and selected identity, never
the pending/next token. Cursor syntax, duplicate and mixed-offset validation are
added to the existing return allowlist. Existing path and safeNext size guards
remain unchanged. Return/login round trips replay and refresh current viewer's
Application/matching data; no creator ownership is assumed.

## Validation

Vitest covers explicit request counts, mode resolution, initial/page/paused/end,
refresh/history/selection, unavailable/retry errors, typed BFF projection, stale
responses, slow timers and Application/auth return. Browser smoke uses the normal
Next dev component with a temporary local fixture API, without database writes or
live Trudvsem requests. Temporary processes are stopped after smoke. Backend/full
API tests are unnecessary for this Web-only slice.
