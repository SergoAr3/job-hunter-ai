"""One current schedule per action. Application -> Reminder is the lock order."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update, func
from sqlalchemy.orm import Session

from app.models import Application, ApplicationReminder, Job, User

MAX_ATTEMPTS = 3
LEASE_SECONDS = 120
MAX_LATENESS = timedelta(hours=24)


class ReminderError(Exception):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


def utc_now():
    return datetime.now(timezone.utc)


def aware(value):
    # SQLite's timestamp metadata is lost; this subsystem only writes UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def zone(name):
    if not isinstance(name, str) or len(name) > 128:
        raise ReminderError("REMINDER_TIMEZONE_INVALID")
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ReminderError("REMINDER_TIMEZONE_INVALID") from None


def validate_schedule(value, name):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReminderError("REMINDER_DATETIME_INVALID")
    tz = zone(name)
    wall = value.replace(tzinfo=None)
    candidates = set()
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(tz).replace(tzinfo=None) == wall:
            candidates.add(candidate)
    if not candidates:
        raise ReminderError("REMINDER_TIME_NONEXISTENT")
    if len(candidates) > 1:
        raise ReminderError("REMINDER_TIME_AMBIGUOUS")
    instant = value.astimezone(timezone.utc)
    if instant not in candidates:
        raise ReminderError("REMINDER_OFFSET_MISMATCH")
    return instant


def apply_schedule(session: Session, application: Application, changes: dict, now=None):
    """Caller owns Application lock and transaction; all action writers use this."""
    reminder = session.scalar(select(ApplicationReminder).where(
        ApplicationReminder.application_id == application.id
    ).with_for_update().execution_options(populate_existing=True))
    before = (application.next_action_due_on, tuple(getattr(reminder, key) for key in ("generation", "delivery_state")) if reminder else None)
    if application.next_action is None:
        if changes.get("next_action_remind_at") is not None:
            raise ReminderError("REMINDER_ACTION_REQUIRED")
        if reminder:
            session.delete(reminder)
        application.next_action_due_on = None
    elif "next_action_remind_at" in changes:
        value = changes["next_action_remind_at"]
        if value is None:
            if changes.get("next_action_timezone") is not None:
                raise ReminderError("REMINDER_TIMEZONE_INVALID")
            if reminder:
                session.delete(reminder)
        else:
            name = changes.get("next_action_timezone")
            instant = validate_schedule(value, name)
            same = reminder and aware(reminder.remind_at) == instant and reminder.timezone == name
            if not same:
                if instant < (now or utc_now()) + timedelta(seconds=60):
                    raise ReminderError("REMINDER_TOO_SOON")
                if reminder is None:
                    reminder = ApplicationReminder(application_id=application.id)
                    session.add(reminder)
                reminder.remind_at, reminder.timezone = instant, name
                reminder.generation, reminder.delivery_state = uuid4(), "pending"
                reminder.attempt_count, reminder.next_attempt_at = 0, instant
                reminder.lease_token = reminder.lease_until = reminder.sent_at = reminder.last_error_code = None
            application.next_action_due_on = None
    elif "next_action_timezone" in changes:
        raise ReminderError("REMINDER_DATETIME_REQUIRED")
    deleted = bool(session.deleted)
    after = (application.next_action_due_on, None if reminder is None or reminder in session.deleted else (reminder.generation, reminder.delivery_state))
    changed = before != after or deleted
    session.flush()
    session.expire(application, ["reminder"])
    return changed


@dataclass(frozen=True)
class Claim:
    application_id: int
    user_id: int
    generation: UUID
    lease_token: UUID


@dataclass(frozen=True)
class Destination:
    chat_id: int
    action: str
    title: str | None
    company: str | None
    remind_at: datetime
    timezone: str


def claim_due(session: Session, now=None):
    now = now or utc_now()
    try:
        application = session.scalar(select(Application).join(ApplicationReminder).where(
            ApplicationReminder.delivery_state == "pending",
            ApplicationReminder.next_attempt_at <= now,
            ApplicationReminder.remind_at <= now,
        ).order_by(ApplicationReminder.next_attempt_at, Application.id)
            .with_for_update(of=Application, skip_locked=True).limit(1)
            .execution_options(populate_existing=True))
        if application is None:
            session.rollback()
            return None
        reminder = session.scalar(select(ApplicationReminder).where(
            ApplicationReminder.application_id == application.id
        ).with_for_update().execution_options(populate_existing=True))
        if reminder is None or reminder.delivery_state != "pending" or aware(reminder.next_attempt_at) > now:
            session.rollback()
            return None
        if not application.next_action or application.next_action_due_on is not None:
            _fail(reminder, "INVALID_STATE")
        elif now > aware(reminder.remind_at) + MAX_LATENESS:
            _fail(reminder, "DELIVERY_EXPIRED")
        elif reminder.attempt_count >= MAX_ATTEMPTS:
            _fail(reminder, "RETRY_EXHAUSTED")
        else:
            reminder.delivery_state = "claimed"
            reminder.lease_token = uuid4()
            reminder.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            reminder.attempt_count += 1
            claim = Claim(application.id, application.user_id, reminder.generation, reminder.lease_token)
            session.commit()
            return claim
        session.commit()
        return None
    except Exception:
        session.rollback()
        raise


def _fail(reminder, code):
    reminder.delivery_state, reminder.last_error_code = "failed", code
    reminder.lease_token = reminder.lease_until = None


def preflight(session: Session, claim: Claim, now=None):
    now = now or utc_now()
    try:
        application = session.scalar(select(Application).where(
            Application.id == claim.application_id, Application.user_id == claim.user_id
        ).with_for_update().execution_options(populate_existing=True))
        if application is None:
            session.rollback()
            return None
        reminder = session.scalar(select(ApplicationReminder).where(
            ApplicationReminder.application_id == claim.application_id
        ).with_for_update().execution_options(populate_existing=True))
        if (reminder is None or reminder.generation != claim.generation or
                reminder.lease_token != claim.lease_token or reminder.delivery_state != "claimed"):
            session.rollback()
            return None
        user = session.get(User, application.user_id)
        code = None
        if aware(reminder.lease_until) <= now:
            code = "DELIVERY_UNCERTAIN"
        elif not application.next_action or application.next_action_due_on is not None:
            code = "INVALID_STATE"
        elif now > aware(reminder.remind_at) + MAX_LATENESS:
            code = "DELIVERY_EXPIRED"
        elif user is None or user.telegram_id is None:
            code = "TELEGRAM_NOT_CONNECTED"
        if code:
            _fail(reminder, code)
            session.commit()
            return None
        job = session.get(Job, application.job_id)
        destination = Destination(user.telegram_id, application.next_action,
            job.title if job else None, job.company if job else None,
            aware(reminder.remind_at), reminder.timezone)
        session.rollback()  # No transaction or ORM object survives into network send.
        return destination
    except Exception:
        session.rollback()
        raise


@dataclass(frozen=True)
class Completion:
    delivery_state: Literal["pending", "sent", "failed"]
    error_code: str | None


def complete(session: Session, claim: Claim, outcome, now=None) -> Completion | None:
    now = now or utc_now()
    try:
        application = session.scalar(select(Application).where(Application.id == claim.application_id)
            .with_for_update())
        if application is None:
            session.rollback()
            return None
        reminder = session.scalar(select(ApplicationReminder).where(
            ApplicationReminder.application_id == claim.application_id
        ).with_for_update().execution_options(populate_existing=True))
        if (not reminder or reminder.generation != claim.generation or reminder.lease_token != claim.lease_token
                or reminder.delivery_state != "claimed"):
            session.rollback()
            return None
        if aware(reminder.lease_until) <= now:
            _fail(reminder, "DELIVERY_UNCERTAIN")
        elif outcome.kind == "sent":
            reminder.delivery_state, reminder.sent_at = "sent", now
            reminder.last_error_code = reminder.lease_token = reminder.lease_until = None
        elif outcome.kind == "retry" and reminder.attempt_count < MAX_ATTEMPTS:
            delay = max(60 if reminder.attempt_count == 1 else 300, outcome.retry_after or 0)
            next_at = now + timedelta(seconds=delay)
            if next_at > aware(reminder.remind_at) + MAX_LATENESS:
                _fail(reminder, "DELIVERY_EXPIRED")
            else:
                reminder.delivery_state, reminder.next_attempt_at = "pending", next_at
                reminder.last_error_code = outcome.code
                reminder.lease_token = reminder.lease_until = None
        else:
            _fail(reminder, "RETRY_EXHAUSTED" if outcome.kind == "retry" else outcome.code)
        result = Completion(reminder.delivery_state, reminder.last_error_code)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def recover_expired(session: Session, now=None):
    """A crashed claim is uncertain: never requeue automatically."""
    now = now or utc_now()
    try:
        result = session.execute(update(ApplicationReminder).where(
            ApplicationReminder.delivery_state == "claimed", ApplicationReminder.lease_until <= now
        ).values(delivery_state="failed", last_error_code="DELIVERY_UNCERTAIN", lease_token=None, lease_until=None))
        session.commit()
        return result.rowcount
    except Exception:
        session.rollback()
        raise


def diagnostics(session: Session, now=None):
    now = now or utc_now()
    due, oldest = session.execute(select(func.count(), func.min(ApplicationReminder.remind_at)).where(
        ApplicationReminder.delivery_state == "pending", ApplicationReminder.next_attempt_at <= now
    )).one()
    expired = session.scalar(select(func.count()).select_from(ApplicationReminder).where(
        ApplicationReminder.delivery_state == "claimed", ApplicationReminder.lease_until <= now))
    return {"due_backlog": due, "oldest_due_lag_seconds": max(0, int((now - aware(oldest)).total_seconds())) if oldest else 0,
            "expired_claims": expired}
