from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserProfile, WorkExperience
from app.work_experience_schema import WorkExperienceIn, WorkExperienceOut

MAX_WORK_EXPERIENCES = 20


class WorkExperienceError(Exception):
    def __init__(self, code: str, status: int):
        self.code, self.status = code, status


def profile_for_user(session: Session, user_id: int, *, lock=False):
    query = select(UserProfile).where(UserProfile.user_id == user_id)
    profile = session.scalar(query.with_for_update() if lock else query)
    if profile is None:
        raise WorkExperienceError("PROFILE_NOT_FOUND", 404)
    return profile


def list_for_profile(session: Session, profile_id: int):
    return list(session.scalars(select(WorkExperience).where(
        WorkExperience.user_profile_id == profile_id
    ).order_by(WorkExperience.id.desc())))


def identity(value):
    fields = WorkExperienceIn.model_fields
    data = value if isinstance(value, dict) else {key: getattr(value, key) for key in fields}
    return tuple(" ".join(data[key].split()).casefold() if isinstance(data[key], str) else data[key] for key in fields)


def duration_months(entry, today: date | None = None):
    today = today or date.today()
    if entry.start_year is None or entry.start_month is None:
        return None
    end_year, end_month = (today.year, today.month) if entry.is_current else (entry.end_year, entry.end_month)
    if end_year is None or end_month is None:
        return None
    return 12 * (end_year - entry.start_year) + end_month - entry.start_month + 1


def output(entry):
    result = WorkExperienceOut.model_validate(entry)
    return result.model_copy(update={"duration_months": duration_months(entry)})


def save(session: Session, user_id: int, payload: WorkExperienceIn, entry_id: int | None = None):
    profile = profile_for_user(session, user_id, lock=True)
    entries = list_for_profile(session, profile.id)
    entry = next((item for item in entries if item.id == entry_id), None)
    if entry_id is not None and entry is None:
        raise WorkExperienceError("WORK_EXPERIENCE_NOT_FOUND", 404)
    if any(item.id != entry_id and identity(item) == identity(payload) for item in entries):
        raise WorkExperienceError("DUPLICATE_WORK_EXPERIENCE", 422)
    if entry is None:
        if len(entries) >= MAX_WORK_EXPERIENCES:
            raise WorkExperienceError("WORK_EXPERIENCE_LIMIT_REACHED", 422)
        entry = WorkExperience(user_profile_id=profile.id)
        session.add(entry)
    for key, value in payload.model_dump().items():
        setattr(entry, key, value)
    session.commit()
    session.refresh(entry)
    return entry


def delete(session: Session, user_id: int, entry_id: int):
    profile = profile_for_user(session, user_id, lock=True)
    entry = session.scalar(select(WorkExperience).where(
        WorkExperience.id == entry_id, WorkExperience.user_profile_id == profile.id
    ))
    if entry is None:
        raise WorkExperienceError("WORK_EXPERIENCE_NOT_FOUND", 404)
    session.delete(entry)
    session.commit()
