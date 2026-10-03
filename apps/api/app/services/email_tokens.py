"""Email ownership/recovery proofs; caller owns identity, no account transfer."""
import re
import secrets
from datetime import timedelta
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from app.models import AuthEmailToken, AuthSession, User
from app.services import auth
from app.services.auth_mail import get_mailer, deliver, web_origin

TTL = {"verify": timedelta(hours=24), "reset": timedelta(minutes=30)}
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def digest(raw):
    if not isinstance(raw, str) or not TOKEN.fullmatch(raw):
        raise auth.AuthError("EMAIL_TOKEN_INVALID", 400)
    return auth.token_hash("email:" + raw)  # Namespace apart from session/challenge digests.


def issue(session, user, purpose):
    now, raw = auth.utc_now(), secrets.token_urlsafe(32)
    # No-op UPDATE locks the User on both SQLite and PostgreSQL. Consuming and
    # issuing always lock User before token rows; this serializes replacement.
    session.execute(update(User).where(User.id == user.id).values(id=User.id))
    session.execute(update(AuthEmailToken).execution_options(synchronize_session=False).where(AuthEmailToken.user_id == user.id,
        AuthEmailToken.purpose == purpose, AuthEmailToken.consumed_at.is_(None)).values(consumed_at=now, outcome="replaced"))
    session.add(AuthEmailToken(user_id=user.id, purpose=purpose, token_digest=digest(raw), created_at=now, expires_at=now + TTL[purpose]))
    return raw


def request_email(session, email, purpose):
    get_mailer().check()  # Same provider availability result before identity lookup.
    web_origin()
    try:
        user = session.scalar(select(User).where(User.email_canonical == email.lower()))
        if user and user.password_hash and (purpose == "reset" or user.email_verified_at is None):
            recipient = user.email
            raw = issue(session, user, purpose)
            # Re-read eligibility under the serialization lock.
            session.refresh(user)
            if purpose == "verify" and user.email_verified_at is not None:
                session.rollback()
                return
            session.commit()
            deliver(purpose, recipient, raw)
        else:
            session.rollback()
    except SQLAlchemyError:
        session.rollback()
        raise auth.AuthError("AUTH_UNAVAILABLE", 503) from None


def consume(session, raw, purpose, password=None):
    token_digest = digest(raw)
    # Expensive hash happens before acquiring any transaction/lock, even for a
    # well-formatted unknown reset proof (no token/account oracle through Argon2).
    encoded = auth.get_passwords().hash(password) if purpose == "reset" else None
    try:
        user_id = session.scalar(select(AuthEmailToken.user_id).where(AuthEmailToken.token_digest == token_digest, AuthEmailToken.purpose == purpose))
        session.rollback()
        if user_id is None:
            raise auth.AuthError("EMAIL_TOKEN_INVALID", 400)
        session.execute(update(User).where(User.id == user_id).values(id=User.id))
        now = auth.utc_now()
        token = session.scalar(select(AuthEmailToken).where(AuthEmailToken.token_digest == token_digest).execution_options(populate_existing=True))
        if token is None or token.purpose != purpose or token.outcome:
            session.rollback()
            raise auth.AuthError("EMAIL_TOKEN_USED", 400)
        if auth.aware(token.expires_at) <= now:
            session.rollback()
            raise auth.AuthError("EMAIL_TOKEN_EXPIRED", 400)
        claimed = session.execute(update(AuthEmailToken).execution_options(synchronize_session=False).where(AuthEmailToken.token_digest == token_digest,
            AuthEmailToken.consumed_at.is_(None), AuthEmailToken.expires_at > now).values(consumed_at=now, outcome="consumed"))
        if claimed.rowcount != 1:
            session.rollback()
            raise auth.AuthError("EMAIL_TOKEN_USED", 400)
        if purpose == "verify":
            session.execute(update(User).where(User.id == user_id, User.email_verified_at.is_(None)).values(email_verified_at=now))
        else:
            session.execute(update(User).where(User.id == user_id).values(password_hash=encoded))
            session.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)).values(revoked_at=now))
            # All other recovery proofs become terminal after credential change.
            session.execute(update(AuthEmailToken).execution_options(synchronize_session=False).where(AuthEmailToken.user_id == user_id,
                AuthEmailToken.purpose == "reset", AuthEmailToken.consumed_at.is_(None)).values(consumed_at=now, outcome="replaced"))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise auth.AuthError("AUTH_UNAVAILABLE", 503) from None
