# Auth Slice 6: email ownership, recovery and abuse budgets

Email/password login now requires `email_verified_at`. Correct password on an
unverified identity returns 403 `EMAIL_VERIFICATION_REQUIRED`; wrong password and
unknown email still share 401 `AUTH_INVALID_CREDENTIALS` and the unknown path uses
Argon2id dummy verification. Existing sessions remain valid until normal expiry
or revocation. Telegram identity is independent: Telegram login still works for
an unverified hybrid account. Verification/reset never transfer identity, change
users.id, link/unlink Telegram or modify domain data.

## Public contract

- POST `/auth/register`: validated requests return generic 202 with the same
  message for new/existing email. A new account gets an ownership proof; an
  existing account's credentials/name/verification/tokens stay unchanged.
- POST `/auth/email/resend`: email input, generic 202 `{ok:true}`. Only an
  unverified email/password account gets a new proof. Verified/unknown identities
  receive no token/mail. Previous unconsumed verification proofs become replaced.
- POST `/auth/password/forgot`: email input, same generic 202. Any identity with
  email + password hash is eligible (including unverified); Telegram-only is not.
  New requests replace all previous unconsumed reset proofs.
- POST `/auth/email/verify`: opaque token input; 200 `{ok:true}`. No session.
- POST `/auth/password/reset`: opaque token + new password; 200 `{ok:true}`. No
  auto-login. Does not set email_verified_at. All user AuthSessions, including
  Telegram sessions, are revoked; Telegram remains usable for a fresh login.
- Invalid/malformed proof: 400 `EMAIL_TOKEN_INVALID`; expired: 400
  `EMAIL_TOKEN_EXPIRED`; consumed/replaced: 400 `EMAIL_TOKEN_USED`. These disclose
  proof state, never account details. Recovery page offers a replacement request.
- Validation 422 never echoes credentials/proofs. API/BFF auth responses are
  no-store. Browser JSON projections contain no IDs/digests/hashes/session tokens.

## Proofs and transactions

`auth_email_tokens` has a unique digest PK, user FK ON DELETE CASCADE, purpose
check, expiry check, terminal-state check, user/purpose and expiry indexes.
Migration `20261003_17` has parent `20261002_16`, no identity backfill, downgrade
only drops this new table. Revisions 14–16 are unchanged.

Each proof is 32 CSPRNG bytes encoded base64url (43 chars, 256 bits). DB stores
SHA-256 of `email:` + proof, never the raw value. Purpose is checked independently;
verification cannot reset or act as an AuthSession. Verification TTL is 24 hours;
reset TTL is 30 minutes, fixed constants to avoid unnecessary deployment knobs.

Issuance/consumption lock the User with a no-op UPDATE on both databases, then
conditionally claim an unconsumed, unexpired token. Replacement uses the same
serialization lock. Password hashing happens before DB locks, using unchanged
Argon2id and Unicode 15–128 character policy (no trim, spaces retained).
Password update, proof consume, invalidation of other recovery proofs and ALL
session revocations commit together. Any DB failure rolls back the transaction
and returns sanitized 503; a session remains reusable. Login rechecks credentials
under a lock/conditional write so an old-password verification racing reset
cannot issue a valid new session after reset. In-flight requests authorized before
revocation may finish; subsequent authentication rejects revoked sessions.

Expired/terminal rows remain stored; no background cleanup is introduced. Future
retention can delete expired rows using the expiry index. Local captured messages
also require operator cleanup; never check them into Git.

## Delivery and privacy

Delivery is a small replaceable service boundary: explicit development/test
capture, or production SMTP-over-TLS via stdlib SMTP_SSL (port 465 by default,
certificate/hostname verification, optional username/password).
Missing SMTP configuration or unsafe trusted origin fails startup clearly.
Capture is rejected in production. Provider availability is checked before ANY
identity lookup for register/resend/forgot: failures return the same 503 for
known/unknown email. If actual sending fails AFTER the preflight and committed
identity/proof, the public result remains generic 202. Identity/proof stay stored;
the user can request another message later. Provider exceptions, recipients,
passwords and links are not logged. Failed send emits only the fixed event
`auth_email_delivery_failed`, without exception details. No rollback of an already-created identity.
No delivery queue/retries/background worker is added.

This response contract is enumeration resistant, not constant-time SMTP delivery:
real mail delivery/DB writes can add latency to eligible accounts. The common
provider preflight reduces an availability oracle but does not eliminate all
network timing inference. A future asynchronous delivery system can improve this;
it is outside this slice.

Links use only configured `WEB_PUBLIC_ORIGIN`, an exact origin (HTTPS in production,
HTTP allowed only on localhost/127.0.0.1 in development/test). Host/Referer/XFF never
construct links. Links have `/verify-email#token=...` or `/reset-password#token=...`:
fragments are not sent in HTTP requests/access logs. Client retains proof only in
memory and replaces visible URL/history immediately. Refresh requires reopening
the original message. Public token pages use no-referrer/noindex; no analytics.
Verification is an explicit click, so merely opening a link does not consume it.
BFF mutations retain the same configured-Origin/content-type checks; API proof
endpoints use no ambient cookie authentication. A bearer proof is required for
mutation. Password reset also clears the current browser session cookie.

## Rate limiting and deployment

Bounded (10,000 keys), thread-safe fixed-window limiter is PROCESS LOCAL. It is
single-process protection; restart clears state and multiple workers/instances
have independent budgets. It is NOT a distributed limiter. Multi-instance release
requires shared enforcement/storage later, without introducing Redis here.
No passwords, raw proofs, session tokens or plaintext email are limiter keys.

| Surface | Per API peer IP | Additional dimension |
| --- | --- | --- |
| Login | 60 / 15 min | SHA-256 canonical email: 10 / 15 min |
| Register | 20 / hour | Shared mail email budget: 5 / hour |
| Resend + forgot combined | 20 / hour | Same shared email budget: 5 / hour |
| Verify + reset completion combined | 60 / 15 min | Proof entropy + one-use DB claim |
| Telegram create | 30 / 10 min | Authenticated link user: 5 / 10 min before password reauth |
| Telegram status + completion combined | 600 / 10 min | Existing browser binding and challenge TTL |

All budgets are checked atomically before expensive auth/delivery work. 429 body
is exactly `{code:"rate_limited"}`, no-store, with rounded-up remaining-window
Retry-After. Capacity exhaustion fails closed with a 60-second retry hint. Web
projects safe rate errors/Retry-After and offers normal retry; no cookie clear.
Bot approve/cancel/inspect remain service-secret-only, so no anonymous rate budget
is added there. Browser cancellation stays available when a budget is exhausted.

Only ASGI request.client.host is used; X-Forwarded-For/Forwarded are ignored.
`make dev` keeps Uvicorn --no-proxy-headers. API requests through the BFF share
its peer-IP budget; email/user dimensions still apply across IPs. This is a
conservative aggregate cap for current single-instance/low-volume deployment.
Do not expose an API with globally trusted proxy headers. A production reverse
proxy needs an explicit allowlist of proxy peers plus edge IP enforcement; current
code does not claim browser-IP isolation behind the BFF. No new trust header or
Web/Bot service credential is introduced.

## Configuration

API: WEB_PUBLIC_ORIGIN, AUTH_MAIL_DELIVERY=smtp, AUTH_SMTP_HOST, AUTH_SMTP_PORT,
AUTH_MAIL_FROM, optional AUTH_SMTP_USERNAME/AUTH_SMTP_PASSWORD. Secrets only in
runtime environment. Local capture additionally requires APP_ENV=development
and absolute AUTH_MAIL_SINK_DIR with 0700 directory; each message is a random
0600 JSON file containing purpose/recipient/link. `make dev` explicitly selects
capture and fixed local Web origin, defaults sink to /tmp/job-hunter-auth-mail,
and does not print raw links. Web only needs its existing API/origin settings.

Deferred: change email, multiple emails, unlink, account merge, OAuth, MFA,
passkeys, magic-link login, CAPTCHA, shared limiter, delivery queue and cleanup jobs.
