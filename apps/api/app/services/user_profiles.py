from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User, UserProfile
from app.schemas import UserProfilePutIn


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
    if session.get(User, user_id) is None:
        raise UserNotFoundError

    profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    values = data.model_dump(mode="json")
    if profile is None:
        profile = UserProfile(user_id=user_id, **values)
        session.add(profile)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
            if profile is None:
                raise
            _replace_profile(profile, values)
            session.commit()
    else:
        _replace_profile(profile, values)
        session.commit()

    session.refresh(profile)
    return profile


def _replace_profile(profile: UserProfile, values: dict[str, object]) -> None:
    for field, value in values.items():
        setattr(profile, field, value)
