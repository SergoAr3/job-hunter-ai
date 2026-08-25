from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.schemas import TelegramUserIn


def get_or_create_telegram_user(session: Session, data: TelegramUserIn) -> tuple[User, bool]:
    user = session.scalar(select(User).where(User.telegram_id == data.telegram_id))
    if user is not None:
        _update_telegram_profile(user, data)
        session.commit()
        return user, False

    user = User(**data.model_dump())
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        user = session.scalar(select(User).where(User.telegram_id == data.telegram_id))
        if user is None:
            raise
        _update_telegram_profile(user, data)
        session.commit()
        return user, False

    session.refresh(user)
    return user, True


def _update_telegram_profile(user: User, data: TelegramUserIn) -> None:
    user.username = data.username
    user.first_name = data.first_name
    user.last_name = data.last_name
