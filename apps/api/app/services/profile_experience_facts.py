"""Confirmed, user-authored experience evidence attached to a profile."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProfileExperienceFact, User, UserProfile
from app.schemas import ProfileExperienceFactIn, normalize_experience_fact_text

MAX_PROFILE_EXPERIENCE_FACTS = 20


class ProfileNotFoundError(Exception):
    pass


class ProfileExperienceFactNotFoundError(Exception):
    pass


class DuplicateProfileExperienceFactError(Exception):
    pass


class ProfileExperienceFactLimitError(Exception):
    pass


def list_profile_experience_facts(session: Session, profile_id: int) -> list[ProfileExperienceFact]:
    return list(session.scalars(
        select(ProfileExperienceFact)
        .where(ProfileExperienceFact.user_profile_id == profile_id)
        .order_by(ProfileExperienceFact.created_at, ProfileExperienceFact.id)
    ))


def list_user_profile_experience_facts(session: Session, user_id: int) -> list[ProfileExperienceFact]:
    profile = _profile_for_user(session, user_id, lock=False)
    return list_profile_experience_facts(session, profile.id)


def create_profile_experience_fact(
    session: Session, user_id: int, payload: ProfileExperienceFactIn,
) -> ProfileExperienceFact:
    profile = _profile_for_user(session, user_id, lock=True)
    facts = list_profile_experience_facts(session, profile.id)
    _ensure_available_text(facts, payload.text)
    if len(facts) >= MAX_PROFILE_EXPERIENCE_FACTS:
        raise ProfileExperienceFactLimitError
    fact = ProfileExperienceFact(user_profile_id=profile.id, text=payload.text)
    session.add(fact)
    session.commit()
    session.refresh(fact)
    return fact


def update_profile_experience_fact(
    session: Session, user_id: int, fact_id: int, payload: ProfileExperienceFactIn,
) -> ProfileExperienceFact:
    profile = _profile_for_user(session, user_id, lock=True)
    fact = _fact_for_profile(session, profile.id, fact_id, lock=True)
    _ensure_available_text(list_profile_experience_facts(session, profile.id), payload.text, excluding_id=fact.id)
    fact.text = payload.text
    session.commit()
    session.refresh(fact)
    return fact


def delete_profile_experience_fact(session: Session, user_id: int, fact_id: int) -> None:
    profile = _profile_for_user(session, user_id, lock=True)
    fact = _fact_for_profile(session, profile.id, fact_id, lock=True)
    session.delete(fact)
    session.commit()


def _profile_for_user(session: Session, user_id: int, *, lock: bool) -> UserProfile:
    if session.get(User, user_id) is None:
        raise ProfileNotFoundError
    statement = select(UserProfile).where(UserProfile.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    profile = session.scalar(statement)
    if profile is None:
        raise ProfileNotFoundError
    return profile


def _fact_for_profile(
    session: Session, profile_id: int, fact_id: int, *, lock: bool,
) -> ProfileExperienceFact:
    statement = select(ProfileExperienceFact).where(
        ProfileExperienceFact.id == fact_id,
        ProfileExperienceFact.user_profile_id == profile_id,
    )
    if lock:
        statement = statement.with_for_update()
    fact = session.scalar(statement)
    if fact is None:
        raise ProfileExperienceFactNotFoundError
    return fact


def _ensure_available_text(
    facts: list[ProfileExperienceFact], text: str, *, excluding_id: int | None = None,
) -> None:
    normalized = normalize_experience_fact_text(text).casefold()
    if any(
        fact.id != excluding_id and normalize_experience_fact_text(fact.text).casefold() == normalized
        for fact in facts
    ):
        raise DuplicateProfileExperienceFactError
