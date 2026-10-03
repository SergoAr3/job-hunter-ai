# Auth Slice 5: full Web session cutover

Browser → HttpOnly session cookie → Next BFF → opaque Bearer AuthSession → API
principal → authorized users.id. Local development and production have the same
identity rules. Only cookie security/public-origin environment settings differ.
There is no configured/default Web user, credential-free impersonation or dev
principal. Unknown obsolete environment settings and headers cannot grant access.

## Removed surface (historical inventory)

The staged rollout used `WEB_DEV_USER_ID`, `WEB_DEV_API_TOKEN`,
`X-Web-Dev-Api-Token` and `AUTH_ROLLOUT_MODE=legacy-development`. Slice 5 deleted
those configuration/runtime mechanisms: API AuthSettings fields, loopback/IP
bypass, startup warning, Web getConfig/getDomainIdentity fallback, development
identity type/branch/UI copy, script generation/export and current setup docs.
The names remain only in this historical inventory and negative regression tests.

## Current contract

- `/profile`, `/discover`, `/applications` and detail pages require a session.
  Missing/invalid/expired/revoked sessions redirect to login with safe next.
- All domain BFF handlers resolve the same real session independently; they never
  trust browser Authorization/user ID/service headers. Auth errors produce 401
  and cookie cleanup. Unavailable principal/domain API produces unavailable UX
  and preserves the cookie; recovery uses the same still-valid session.
- `/` redirects anonymous users to `/login`, authenticated users to `/profile`,
  and renders unavailable UX when session resolution fails.
- `/login` and `/register` remain public and redirect authenticated users using
  existing safe-next behavior. Logout attempts revocation, clears the cookie and
  returns to login without selecting any other account.
- API's 27 user-scoped operations require a real session or trusted Bot service
  credential. Ownership checks remain in API. `/users/telegram` is Bot-only;
  `/auth/internal/principal` remains Bearer-only/no-store and server-use.
- Bot's independent `BOT_API_SERVICE_TOKEN` and thin-client boundary are retained.
- Email and Telegram login issue the same ordinary AuthSession. Existing Telegram
  login resolves the same User and domain graph; no identity migration/merge/copy
  is performed. Linking/password reauthentication and attempt-scoped challenge
  cookies are unchanged.

## Development

`make dev` applies existing migrations and starts API/Bot/Web. It shares an
existing or ephemeral Bot credential between API/Bot only. Web needs only the
API URL/environment/public origin, then the user signs in by email or Telegram.
Existing Bot data is accessed through Telegram login. No user ID, seed account
or development password is required. Do not overwrite existing `.env` secrets.

No schema change: Alembic head remains `20261002_16`. Email verification, password
reset and production rate limiting/public-rollout hardening remain Slice 6.
