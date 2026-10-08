from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, UserProfile
from app.schemas import UserProfilePutIn
from app.services.skill_sync import sync_profile_skills


class UserNotFoundError(Exception):
    pass


class UserProfileNotFoundError(Exception):
    pass


def get_user_profile(session: Session, user_id: int) -> UserProfile:
    if session.get(User, user_id) is None:
        raise UserNotFoundError
    profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        raise UserProfileNotFoundError
    return profile


def put_user_profile(session: Session, user_id: int, data: UserProfilePutIn) -> UserProfile:
    try:
        # Same lock order as CV Apply, including first-profile creation.
        user = session.scalar(select(User).where(User.id == user_id).with_for_update()
                              .execution_options(populate_existing=True))
        if user is None:
            raise UserNotFoundError
        profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id)
                                 .with_for_update().execution_options(populate_existing=True))
        if profile is None:
            profile = UserProfile(user_id=user_id)
            session.add(profile)
        _replace_profile(profile, data.model_dump(mode="json"))
        session.flush()
        sync_profile_skills(session, profile)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(profile)
    return profile


def _replace_profile(profile: UserProfile, values: dict[str, object]) -> None:
    for field, value in values.items():
        setattr(profile, field, value)
