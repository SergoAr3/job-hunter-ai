"""Browser-bound, single-use Telegram ownership proofs. API owns identity writes."""
import hmac
import os
import re
import secrets
from datetime import timedelta
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError, OperationalError
from sqlalchemy.orm import Session
from app.models import TelegramChallenge, User, AuthSession
from app.schemas import TelegramUserIn
from app.services import auth
from app.services.users import _update_telegram_profile

TTL = timedelta(minutes=5)
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def digest(raw):
    if not isinstance(raw, str) or not TOKEN.fullmatch(raw):
        raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 400)
    return auth.token_hash(raw)


def code(record):
    return record.token_hash[:6].upper()


def state(record):
    if record.outcome:
        return {"completed": "consumed", "conflict": "conflict", "cancelled": "cancelled"}[record.outcome]
    if auth.aware(record.expires_at) <= auth.utc_now():
        return "expired"
    return "approved" if record.approved_at else "pending"


def create(session, purpose, binding, principal=None, password=None):
    binding_hash = digest(binding)
    bot = os.getenv("TELEGRAM_BOT_USERNAME", "")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot):
        raise auth.AuthError("AUTH_UNAVAILABLE", 503)
    if purpose == "login":
        if principal is not None or password is not None:
            raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 400)
    elif purpose == "link":
        if principal is None:
            raise auth.AuthError()
        user = session.get(User, principal.user_id)
        encoded = user.password_hash if user else None
        session.rollback()  # No transaction/locks during password work.
        if not encoded or password is None or not auth.get_passwords().verify(encoded, password):
            raise auth.AuthError("AUTH_INVALID_CREDENTIALS")
        # Recheck the exact authenticated session and unchanged credential after Argon2.
        record = session.scalar(select(AuthSession).where(AuthSession.token_hash == principal.session_hash).with_for_update().execution_options(populate_existing=True))
        validate_session(record, principal)
        user = session.scalar(select(User).where(User.id == principal.user_id).with_for_update().execution_options(populate_existing=True))
        if user is None or user.password_hash != encoded:
            raise auth.AuthError("AUTH_INVALID_CREDENTIALS")
    else:
        raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 400)
    now, raw = auth.utc_now(), secrets.token_urlsafe(32)
    challenge = TelegramChallenge(token_hash=digest(raw), binding_hash=binding_hash, purpose=purpose,
        initiating_user_id=principal.user_id if principal else None,
        initiating_session_hash=principal.session_hash if principal else None,
        created_at=now, expires_at=now + TTL)
    session.add(challenge)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise auth.AuthError("AUTH_UNAVAILABLE", 503) from None
    return {"token": raw, "deep_link": f"https://t.me/{bot}?start=auth_{raw}", "code": code(challenge), "expires_at": challenge.expires_at, "status": "pending"}


def validate_session(record, principal):
    now = auth.utc_now()
    if (record is None or record.user_id != principal.user_id or record.revoked_at is not None
        or auth.aware(record.expires_at) <= now or auth.aware(record.last_seen_at) + auth.SESSION_IDLE_LIFETIME <= now):
        raise auth.AuthError()


def browser_record(session, raw, binding, purpose, principal):
    record = session.get(TelegramChallenge, digest(raw))
    if record is None or not hmac.compare_digest(record.binding_hash, digest(binding)) or record.purpose != purpose:
        raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 400)
    if purpose == "link":
        if principal is None:
            raise auth.AuthError()
        if record.initiating_user_id != principal.user_id or record.initiating_session_hash != principal.session_hash:
            raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 400)
    elif principal is not None:
        raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 400)
    return record


def status(session, raw, binding, purpose, principal):
    record = browser_record(session, raw, binding, purpose, principal)
    return {"status": state(record)}


def inspect(session, raw):
    record = session.get(TelegramChallenge, digest(raw))
    if record is None:
        raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 400)
    return {"purpose": record.purpose, "code": code(record), "status": state(record)}


def approve(session, raw, telegram):
    now = auth.utc_now()
    result = session.execute(update(TelegramChallenge).execution_options(synchronize_session=False).where(
        TelegramChallenge.token_hash == digest(raw), TelegramChallenge.approved_at.is_(None),
        TelegramChallenge.consumed_at.is_(None), TelegramChallenge.expires_at > now,
    ).values(approved_at=now, approved_telegram_id=telegram.telegram_id, telegram_metadata=telegram.model_dump(exclude={"telegram_id"})))
    if result.rowcount != 1:
        session.rollback()
        raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 409)
    session.commit()
    return {"ok": True}


def cancel(session, raw, telegram):
    # Keep the Bot actor restriction; browser cancellation uses ownership binding.
    return _cancel_challenge(session, digest(raw), (
        TelegramChallenge.approved_telegram_id.is_(None) | (TelegramChallenge.approved_telegram_id == telegram.telegram_id),
    ))


def cancel_browser(session, raw, binding, purpose, principal):
    if purpose != "login":
        raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 400)
    record = browser_record(session, raw, binding, purpose, principal)
    return _cancel_challenge(session, record.token_hash)


def _cancel_challenge(session, token_hash, conditions=()):
    now = auth.utc_now()
    try:
        result = session.execute(update(TelegramChallenge).execution_options(synchronize_session=False).where(
            TelegramChallenge.token_hash == token_hash, TelegramChallenge.consumed_at.is_(None),
            TelegramChallenge.expires_at > now, *conditions,
        ).values(consumed_at=now, outcome="cancelled"))
        if result.rowcount != 1:
            session.rollback()
            raise auth.AuthError("TELEGRAM_CHALLENGE_INVALID", 409)
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise auth.AuthError("AUTH_UNAVAILABLE", 503) from None
    return {"ok": True}


def resolve_telegram(session, data):
    user = session.scalar(select(User).where(User.telegram_id == data.telegram_id).with_for_update().execution_options(populate_existing=True))
    if user is None:
        try:
            with session.begin_nested():
                user = User(**data.model_dump())
                session.add(user)
                session.flush()
        except IntegrityError:
            user = session.scalar(select(User).where(User.telegram_id == data.telegram_id).with_for_update().execution_options(populate_existing=True))
            if user is None:
                raise
    _update_telegram_profile(user, data)
    session.flush()
    return user


def complete_once(session, raw, binding, purpose, principal):
    record = browser_record(session, raw, binding, purpose, principal)
    current = state(record)
    if current != "approved":
        raise auth.AuthError("TELEGRAM_CHALLENGE_" + current.upper(), 409 if current != "expired" else 410)
    if principal:
        active = session.scalar(select(AuthSession).where(AuthSession.token_hash == principal.session_hash).with_for_update().execution_options(populate_existing=True))
        validate_session(active, principal)
    now = auth.utc_now()
    claimed = session.execute(update(TelegramChallenge).execution_options(synchronize_session=False).where(
        TelegramChallenge.token_hash == record.token_hash, TelegramChallenge.consumed_at.is_(None),
        TelegramChallenge.approved_at.is_not(None), TelegramChallenge.expires_at > now,
    ).values(consumed_at=now, outcome="completed"))
    if claimed.rowcount != 1:
        session.rollback()
        raise auth.AuthError("TELEGRAM_CHALLENGE_CONSUMED", 409)
    data = TelegramUserIn(telegram_id=record.approved_telegram_id, **record.telegram_metadata)
    if purpose == "login":
        user = resolve_telegram(session, data)
        result = auth.issue_session(session, user.id)
    else:
        user = session.scalar(select(User).where(User.id == principal.user_id).with_for_update().execution_options(populate_existing=True))
        owner = session.scalar(select(User.id).where(User.telegram_id == data.telegram_id))
        conflict = user is None or (user.telegram_id is not None and user.telegram_id != data.telegram_id) or (owner is not None and owner != user.id)
        if not conflict:
            try:
                with session.begin_nested():
                    user.telegram_id = data.telegram_id
                    _update_telegram_profile(user, data)
                    session.flush()
            except IntegrityError:
                conflict = True
        if conflict:
            session.execute(update(TelegramChallenge).execution_options(synchronize_session=False).where(TelegramChallenge.token_hash == record.token_hash).values(outcome="conflict"))
            session.commit()  # Terminal conflict persists; no user/session mutation.
            raise auth.AuthError("ACCOUNT_LINK_CONFLICT", 409)
        result = {"ok": True, "me": auth.current_user(session, user.id)}
    session.commit()  # Consume, identity write and login session are one transaction.
    return result


def complete(session, raw, binding, purpose, principal):
    # SQLite may reject an upgrade of a stale read snapshot. Retry the entire
    # transaction once; the conditional consume remains authoritative.
    for attempt in range(2):
        try:
            return complete_once(session, raw, binding, purpose, principal)
        except OperationalError as error:
            session.rollback()
            if session.bind.dialect.name == "sqlite" and "locked" in str(error).lower() and attempt == 0:
                continue
            raise auth.AuthError("AUTH_UNAVAILABLE", 503) from None
        except auth.AuthError:
            session.rollback()
            raise
        except SQLAlchemyError:
            session.rollback()
            raise auth.AuthError("AUTH_UNAVAILABLE", 503) from None
