"""Credentials and opaque sessions. No browser/cookie or Bot UI logic."""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from threading import BoundedSemaphore

from argon2 import PasswordHasher, Type
from argon2.exceptions import HashingError, VerificationError, InvalidHashError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth_config import SESSION_ABSOLUTE_LIFETIME, SESSION_IDLE_LIFETIME, SESSION_TOUCH_INTERVAL
from app.auth_schemas import AuthCredentials, CurrentUserOut, LoginOut, RegisterIn
from app.models import AuthSession, User, UserProfile


class AuthError(Exception):
    def __init__(self, code="AUTH_REQUIRED", status=401):
        self.code, self.status = code, status
        super().__init__(code)


def utc_now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Passwords:
    def __init__(self):
        self.hasher = PasswordHasher(type=Type.ID, memory_cost=65536, time_cost=3, parallelism=1)
        # Bound per-process memory/CPU consumption, without a distributed limiter.
        self.slots = BoundedSemaphore(2)
        self.dummy_hash = self.hash(secrets.token_urlsafe(32))

    def hash(self, password: str) -> str:
        if not self.slots.acquire(blocking=False):
            raise AuthError("AUTH_BUSY", 503)
        try:
            return self.hasher.hash(password)
        except HashingError:
            raise AuthError("AUTH_UNAVAILABLE", 503) from None
        finally:
            self.slots.release()

    def verify(self, encoded: str, password: str) -> bool:
        if not self.slots.acquire(blocking=False):
            raise AuthError("AUTH_BUSY", 503)
        try:
            try:
                return self.hasher.verify(encoded, password)
            except (VerificationError, InvalidHashError):
                return False
        finally:
            self.slots.release()

    def needs_rehash(self, encoded: str) -> bool:
        return self.hasher.check_needs_rehash(encoded)


@lru_cache
def get_passwords():
    return Passwords()


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Principal:
    user_id: int
    session_hash: str


def register(session: Session, payload: RegisterIn):
    # Hash before any DB transaction, also for duplicates (generic result).
    encoded = get_passwords().hash(payload.password.get_secret_value())
    canonical = payload.email.lower()
    user = User(email=payload.email, email_canonical=canonical,
                password_hash=encoded, display_name=payload.display_name)
    session.add(user)
    try:
        session.flush()
        from app.services.email_tokens import issue
        from app.services.auth_mail import deliver
        raw = issue(session, user, "verify")
        session.commit()
        deliver("verify", payload.email, raw)
    except IntegrityError:
        session.rollback()
        if session.scalar(select(User.id).where(User.email_canonical == canonical)) is None:
            raise AuthError("AUTH_UNAVAILABLE", 503) from None
        # Never update credentials/display_name of an existing account.
        session.rollback()
    except SQLAlchemyError:
        session.rollback()
        raise AuthError("AUTH_UNAVAILABLE", 503) from None


def current_user(session: Session, user_id: int) -> CurrentUserOut:
    user = session.get(User, user_id)
    if user is None:
        raise AuthError()
    return CurrentUserOut(
        email=user.email, email_verified=user.email_verified_at is not None,
        telegram_linked=user.telegram_id is not None,
        display_name=user.display_name or user.first_name,
        profile_exists=session.scalar(select(UserProfile.id).where(UserProfile.user_id == user_id)) is not None,
        created_at=aware(user.created_at),
    )


def login(session: Session, payload: AuthCredentials) -> LoginOut:
    try:
        return _login(session, payload)
    except SQLAlchemyError:
        session.rollback()
        raise AuthError("AUTH_UNAVAILABLE", 503) from None


def _login(session: Session, payload: AuthCredentials) -> LoginOut:
    user = session.scalar(select(User).where(User.email_canonical == payload.email.lower()))
    user_id, encoded = (user.id, user.password_hash) if user else (None, None)
    session.rollback()  # No read transaction/DB locks during Argon2 work.
    passwords = get_passwords()
    # Reverify once if another successful login rehashed the same credential.
    # A real credential change must still pass verification before a session.
    for attempt in range(2):
        verified = passwords.verify(encoded or passwords.dummy_hash, payload.password.get_secret_value())
        if not verified or user_id is None or encoded is None:
            raise AuthError("AUTH_INVALID_CREDENTIALS")
        replacement = None
        if passwords.needs_rehash(encoded):
            try:
                replacement = passwords.hash(payload.password.get_secret_value())
            except AuthError:
                replacement = None  # Preserve valid login on a rehash outage.
        user = session.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
        if user is None:
            session.rollback()
            raise AuthError("AUTH_INVALID_CREDENTIALS")
        if user.password_hash != encoded:
            encoded = user.password_hash
            session.rollback()  # No locks while rechecking the changed hash.
            if attempt == 0 and encoded is not None:
                continue
            raise AuthError("AUTH_INVALID_CREDENTIALS")
        if user.email_verified_at is None:
            session.rollback()
            raise AuthError("EMAIL_VERIFICATION_REQUIRED", 403)
        # SQLite has no row-level SELECT FOR UPDATE; conditional write also
        # prevents a reset racing old-password session issuance on that dialect.
        guard = session.execute(update(User).where(User.id == user_id, User.password_hash == encoded).values(id=User.id))
        if guard.rowcount != 1:
            session.rollback()
            # A concurrent rehash can win the conditional write on SQLite.
            # Reverify fresh credentials, exactly as after a PostgreSQL row lock.
            encoded = session.scalar(select(User.password_hash).where(User.id == user_id))
            session.rollback()
            if attempt == 0 and encoded is not None:
                continue
            raise AuthError("AUTH_INVALID_CREDENTIALS")
        if replacement is not None:
            user.password_hash = replacement
        break
    result = issue_session(session, user_id)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        # Do not propagate/log SQL parameters containing the token digest.
        raise AuthError("AUTH_UNAVAILABLE", 503) from None
    return result


def issue_session(session: Session, user_id: int) -> LoginOut:
    """Add a fresh session to the caller's identity transaction; never commit."""
    now = utc_now()
    raw = secrets.token_urlsafe(32)
    expiry = now + SESSION_ABSOLUTE_LIFETIME
    session.add(AuthSession(token_hash=token_hash(raw), user_id=user_id,
                            created_at=now, expires_at=expiry, last_seen_at=now))
    return LoginOut(session_token=raw, expires_at=expiry, me=current_user(session, user_id))


def authenticate(session: Session, raw: str) -> Principal:
    digest, now = token_hash(raw), utc_now()
    record = session.get(AuthSession, digest)
    if (record is None or record.revoked_at is not None or aware(record.expires_at) <= now
            or aware(record.last_seen_at) + SESSION_IDLE_LIFETIME <= now):
        raise AuthError()
    if session.get(User, record.user_id) is None:
        raise AuthError()
    principal = Principal(record.user_id, digest)
    if aware(record.last_seen_at) + SESSION_TOUCH_INTERVAL <= now:
        # Conditional update avoids reviving revoked/expired sessions or moving
        # last_seen backwards under concurrent requests. Touch owns its commit
        # BEFORE domain services start their writes.
        result = session.execute(update(AuthSession).where(
            AuthSession.token_hash == digest, AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
            AuthSession.last_seen_at > now - SESSION_IDLE_LIFETIME,
            AuthSession.last_seen_at <= now - SESSION_TOUCH_INTERVAL,
        ).values(last_seen_at=now).execution_options(synchronize_session=False))
        session.commit()
        if result.rowcount == 0:
            session.expire_all()
            record = session.get(AuthSession, digest)
            if record is None or record.revoked_at is not None or aware(record.expires_at) <= now or aware(record.last_seen_at) + SESSION_IDLE_LIFETIME <= now:
                raise AuthError()
    return principal


def logout(session: Session, raw: str):
    # Validly formatted unknown/expired/revoked tokens are idempotent no-ops.
    session.execute(update(AuthSession).where(
        AuthSession.token_hash == token_hash(raw), AuthSession.revoked_at.is_(None),
    ).values(revoked_at=utc_now()))
    session.commit()
