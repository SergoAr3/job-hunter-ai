from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.schemas import ApplicationCreateIn, SavedApplicationOut, TelegramUserIn, TelegramUserOut
from app.services.applications import UserNotFoundError, save_application_for_user
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


@app.post("/users/{user_id}/applications", response_model=SavedApplicationOut)
def save_application(
    user_id: int, payload: ApplicationCreateIn, session: Session = Depends(get_session)
) -> SavedApplicationOut:
    try:
        job, application, job_created, application_created = save_application_for_user(
            session, user_id, payload.source_url
        )
    except UserNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from error

    return SavedApplicationOut(
        job=job,
        application=application,
        job_created=job_created,
        application_created=application_created,
    )
