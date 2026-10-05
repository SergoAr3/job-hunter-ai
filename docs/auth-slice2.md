# Auth Slice 2 — backend credentials and sessions

Slice 1 remains committed unchanged. Slice 2 adds API-owned credentials, opaque
sessions and the Bot service boundary. No Web auth, cookies, linking or identity
merge is implemented. Email login currently permits unverified accounts for the
private/development flow; verification remains Slice 6 work.

## Credentials and public API

Email: printable ASCII, outer ASCII spaces trimmed, simple local/domain syntax,
canonical `lower(trim(email))`. Plus suffixes and dots are preserved; the Slice 1
DB checks and canonical unique constraint remain authoritative.
Password: 15–128 Unicode characters, including spaces, without trimming.
`argon2-cffi` creates/verifies Argon2id hashes (64 MiB, 3 iterations, parallelism 1,
library-generated salt). Successful login rehashes obsolete parameters. Unknown
email performs dummy verification with the same parameters. A nonblocking limit
of two concurrent Argon2 operations per process bounds hashing memory to roughly
128 MiB plus overhead; overload returns 503 `AUTH_BUSY`. This is not rate limiting.
Hashing and verification run outside DB transactions; login then locks/rechecks
the User before persisting rehash and session. If a concurrent login changes the
hash, login releases the transaction and reverifies the current hash once before
locking/rechecking again; stale credentials cannot create a session. A failed rehash preserves valid
credentials and permits login. Registration uniqueness errors roll back before
resolving the winning canonical email and never overwrite its credentials.
Session persistence errors (including duplicate digest) roll back and return
503 `AUTH_UNAVAILABLE`, without propagating/logging SQL parameters. No retries.

All Auth responses use `Cache-Control: no-store`. Errors use
`{"detail":{"code":"..."}}`; malformed input returns 422 with locations,
types and safe messages, without rejected input/context. Neither passwords nor
raw tokens/service secrets are logged by the implementation.

| Endpoint | Input/authentication | Result |
| --- | --- | --- |
| POST `/auth/register` | email, password, optional display_name | Generic 202 for new/existing canonical email; no token, no profile; existing credentials/name unchanged |
| POST `/auth/login` | email, password | 200 `{session_token, expires_at, me}`; raw token is for trusted future BFF server transport |
| POST `/auth/logout` | Single Bearer token | 204; well-formed unknown/expired/revoked token is a safe idempotent no-op; missing/malformed/ambiguous credentials return 401 |
| GET `/auth/me` | Valid Bearer session | Safe email, email_verified, telegram_linked, display_name, profile_exists, created_at; no internal ID/hash/numeric Telegram ID |

Unknown email and wrong password both return `AUTH_INVALID_CREDENTIALS` (401).
Missing, malformed, unknown, expired and revoked session credentials collapse to
`AUTH_REQUIRED` (401). Authorization path mismatch returns `RESOURCE_NOT_FOUND`
(404), and existing nested ownership lookups continue returning 404.

## Session contract and migration

Revision `20261002_15`, parent `20261002_14`, creates `auth_sessions`. It does not
modify Users or domain tables. The token is `secrets.token_urlsafe(32)` (256 bits
of randomness); only its 64-character SHA-256 digest is persisted as indexed PK.
Rows contain user_id, created_at, expires_at, last_seen_at, revoked_at. Secondary
indexes support user-wide future revocation and expiry cleanup. The User FK uses
`ON DELETE CASCADE`: sessions cannot block a future account deletion. Existing
domain FK deletion policies remain unchanged.

Lifetime constants live in `auth_config.py`: absolute 7 days, idle 24 hours,
last_seen touch interval 5 minutes. The idle timestamp has up to five minutes of
conservative lag. Conditional touches cannot revive revoked/expired sessions or
move last_seen backwards. The touch transaction commits before domain writes.
No refresh token/JWT/rotation is introduced; sessions remain independently
revocable. Downgrading revision 15 drops all sessions (signs users out), preserving
accounts/profile/application data. Downgrading Slice 1 afterwards retains its
existing refusal of destructive identity rollback.

## Authorization inventory

Every matched route with `user_id` has `require_user_access`. A validated session
produces an internal `Principal(user_id, session_hash)`, then `authorize_user`
compares the path target with that principal. Nested resources are selected using
the authorized user/profile ownership. A browser-supplied `X-User-ID` has no role.
Bot uses a separate service credential plus the explicit target in the existing
path; the trusted Bot takes Telegram identity from update.from_user.id.

In every environment ALL user-scoped routes below require a valid session
or Bot service credential. Public utility endpoints neither read nor mutate
user-owned resources. Auth routes never accept Bot credentials as user sessions.

Paths in this inventory omit the common `/users/{user_id}` prefix.

| Methods and path | Current callers | Ownership boundary |
| --- | --- | --- |
| GET/PUT `/profile` | shared Web/Bot | authorized User → profile |
| GET `/profile/work-experiences` | shared Web/Bot | authorized User → profile → entries |
| POST `/profile/work-experiences`; PATCH/PUT/DELETE `/profile/work-experiences/{experience_id}` | shared Web/Bot (PUT retained for Bot) | authorized profile; entry must belong to it |
| GET `/profile/experience-facts` | shared Web/Bot | authorized profile |
| POST `/profile/experience-facts`; PUT/DELETE `/profile/experience-facts/{fact_id}` | Bot-only | authorized profile; fact must belong to it |
| POST `/profile/draft-from-cv` | Bot-only | authorized User; CV draft, no cross-user reads |
| GET `/discover/jobs`; POST `/discover/jobs/save` | shared Web/Bot | authorized User; personalized preview/saved state/application |
| POST `/applications` | Bot-only | authorized User → saved application |
| GET `/applications`; GET `/applications/{application_id}` | shared Web/Bot | authorized User → owned application |
| PUT `/applications/{application_id}/status`; GET `/applications/{application_id}/status-history` | shared Web/Bot | owned application → history/snapshot |
| PUT/DELETE `/applications/{application_id}/note` | Bot-only | owned application |
| PUT/DELETE `/applications/{application_id}/next-action` | Bot-only | owned application |
| GET `/applications/{application_id}/match`; POST `/applications/{application_id}/cover-letter` | Bot-only | owned application → job/profile |
| GET `/applications/follow-ups`; GET `/applications/learning-summary`; GET `/applications/match-learning-summary` | Bot-only | authorized User; all aggregate queries scoped to User |
| POST `/users/telegram` (no common prefix) | Bot-only identity resolver | mandatory service credential; Telegram ID taken from trusted Bot |
| GET `/health`; POST `/profile/skills/normalize`, `/profile/languages/normalize`, `/cover-letter/language` | public/shared utilities | no account identity or user data access |

## Rollout history and current authentication

Slice 2 introduced an explicitly configured loopback development principal for
staged rollout. Slice 3 added Email/password Web sessions and Slice 4 added
Telegram login/linking. Slice 5 removed that development principal, credential,
rollout flag and all fallback branches. Current Web access requires a real
AuthSession in every environment; Bot retains its separate service credential.
No browser-supplied identity or service credential is trusted by Next.
Current cutover contract: [auth-slice5.md](auth-slice5.md).

Slice 6 supplies verification/reset and hardening/rate limits. No automatic
matching of Email and Telegram accounts is performed.

## Bot credential and operational limits

Both API and Bot require `BOT_API_SERVICE_TOKEN`: a shared server-only secret of
32–512 printable ASCII characters without whitespace. Production rejects known
placeholder/test/dev/default strings (explicit denylist, no entropy estimator).
Generate a separate random Bot secret via
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and supply it through
server secret storage. API compares it with
`hmac.compare_digest`. Bot sends it as `X-Bot-Service-Token` only on `/users/`
requests to its configured API; normalization and health requests omit it. Duplicate
headers or Bearer + service credentials are rejected on protected operations.
The credential grants the trusted Bot access to explicit user targets; it must
never be shipped to a browser. Deployment must provide TLS and secret management.
Existing Telegram IDs, metadata refresh and all domain graphs remain unchanged.

No distributed limiter, email delivery, reset, session cleanup job or public Web
Auth UI is included. Expired/revoked rows accumulate until later cleanup. The
per-process Argon2 guard does not prevent sustained abuse, and generic registration
reduces explicit email enumeration without claiming perfect timing indistinguishability.
Already-authorized in-flight requests can finish when a concurrent logout occurs.

## Verification commands

From API: `.venv/bin/python -m pytest -q`; isolated PostgreSQL parity and races:
`RUN_IDENTITY_POSTGRES=1 RUN_DISCOVER_POSTGRES=1 RUN_WORK_HISTORY_POSTGRES=1 .venv/bin/python -m pytest -q`.
From Bot: `.venv/bin/python -m pytest -q`. From Web: existing transport/navigation
Vitest tests. Migration tests use disposable schemas/files, never reset runtime
accounts. Restore generated `apps/web/next-env.d.ts` before finalizing.
