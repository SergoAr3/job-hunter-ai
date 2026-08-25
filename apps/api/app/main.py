from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.database import get_session
from app.schemas import TelegramUserIn, TelegramUserOut
from app.services.users import get_or_create_telegram_user

app = FastAPI(title="Job Hunter AI API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/users/telegram", response_model=TelegramUserOut)
def create_or_get_telegram_user(
    payload: TelegramUserIn, session: Session = Depends(get_session)
) -> TelegramUserOut:
    user, created = get_or_create_telegram_user(session, payload)
    return TelegramUserOut(id=user.id, telegram_id=user.telegram_id, created=created)
