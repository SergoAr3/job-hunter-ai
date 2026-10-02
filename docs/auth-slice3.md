# Auth Slice 3: Email/password Web flow

Slice 3 adds Next BFF and UI over Slice 2's FastAPI sessions. It does not complete
Auth v1: Telegram login/linking is Slice 4, session-only cutover completed in Slice 5, email
verification/password reset/rate limiting Slice 6.

Browser → `/api/auth/{register,login,logout,me}` → FastAPI. FastAPI owns passwords,
Argon2id, session DB and final authorization. Register preserves generic 202 and
creates neither session nor Profile. Login sets an opaque token in a server-managed
cookie and returns only a safe user projection. Forms preserve Unicode/password
spaces, accept 15–128 characters, clear password after server submission and guard
duplicate submissions. No client `/me` bootstrap fetch is needed.

Production cookie: `__Host-job_hunter_session`, HttpOnly, Secure, SameSite=Lax,
Path=/, no Domain. Expiry is capped by backend absolute expiry and seven days;
backend remains authoritative for idle/absolute expiry and revocation. Local HTTP
uses `job_hunter_session_dev` without Secure. Tokens never enter JSON, React props,
HTML, storage, URLs or application logs. Credentials necessarily travel in the
login/register POST request: browser DevTools can inspect the submitted request.
They are not echoed back or retained in the form after submission.

`getCurrentUser` is server-only and memoized within a React server render. Its
states are authenticated, unauthenticated and unavailable. Next needs internal ID
for legacy `/users/{user_id}` routes; Bearer-only `/auth/internal/principal` returns
`{user_id, me}` to Next server. Public FastAPI `/auth/me` and Web `/api/auth/me`
continue to omit internal ID. BFF reprojects known safe user fields. Each domain
handler independently resolves the cookie and forwards only Bearer credentials;
FastAPI checks principal ownership against the path ID.

`(workspace)` keeps existing URLs and navigation order. Pages resolve real auth
server-side; BFF handlers enforce it independently. On an unavailable resolver,
the workspace keeps logout available without presenting development identity.
Missing/expired/revoked session
redirects to `/login?next=...`; backend outage shows unavailable UX. Safe next only
allows Profile, Discover, Applications and positive-ID detail routes, with queries;
external URLs, backslashes, control characters, malformed/dangerous encoding and
auth loops fall back to `/profile`.

During staged rollout, Slice 3 supported an explicit development fallback.
Slice 5 removed that path. Current Web identity requires a valid session in all
environments; absent, invalid, duplicate, expired or revoked cookies never select
another identity. No browser-supplied credential or user ID is trusted. Backend
401 becomes standardized BFF `unauthenticated`, clears cookie, and centralized
client transport redirects once with safe next. Backend 503 preserves cookie and
never redirects to login.

All mutations use one JSON-only origin policy: configured exact
`WEB_PUBLIC_ORIGIN` (HTTPS in production), plus fixed local :3100 origins in
explicit development. Missing/null/foreign Origin and cross-site Fetch Metadata
are rejected. Host/X-Forwarded-Host are not trusted. No credentialed CORS added.

Logout attempts backend revocation and always clears local cookie after trusted
origin validation, including backend outage. Response indicates whether revocation
was confirmed; login UI explains that an unconfirmed server session may remain
active until expiry. Invalid/absent cookie is also cleared. This prevents outage
from trapping the user in the UI but cannot guarantee server revocation offline.

This slice remains for private/local use. Verification/reset and public-rollout
hardening are deferred; no public deployment readiness is implied.
