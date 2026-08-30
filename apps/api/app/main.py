from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.schemas import (
    ApplicationCreateIn,
    SavedApplicationOut,
    TelegramUserIn,
    TelegramUserOut,
    UserProfileOut,
    UserProfilePutIn,
)
from app.services.applications import UnsafeUrlError, UserNotFoundError, save_application_for_user
from app.services.users import get_or_create_telegram_user
from app.services.user_profiles import (
    UserNotFoundError as ProfileUserNotFoundError,
    UserProfileNotFoundError,
    get_user_profile,
    put_user_profile,
)
from app.services.vacancy_enrichment import VacancyEnrichmentService
from app.services.job_ai_enrichment import JobAIEnrichmentService

app = FastAPI(title="Job Hunter AI API")
enrichment_service = VacancyEnrichmentService()
ai_enrichment_service = JobAIEnrichmentService()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/users/telegram", response_model=TelegramUserOut)
def create_or_get_telegram_user(
    payload: TelegramUserIn, session: Session = Depends(get_session)
) -> TelegramUserOut:
    user, created = get_or_create_telegram_user(session, payload)
    return TelegramUserOut(id=user.id, telegram_id=user.telegram_id, created=created)


@app.get("/users/{user_id}/profile", response_model=UserProfileOut)
def read_user_profile(user_id: int, session: Session = Depends(get_session)) -> UserProfileOut:
    try:
        profile = get_user_profile(session, user_id)
    except (ProfileUserNotFoundError, UserProfileNotFoundError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found") from error
    return UserProfileOut.model_validate(profile)


@app.put("/users/{user_id}/profile", response_model=UserProfileOut)
def replace_user_profile(
    user_id: int, payload: UserProfilePutIn, session: Session = Depends(get_session)
) -> UserProfileOut:
    try:
        profile = put_user_profile(session, user_id, payload)
    except ProfileUserNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from error
    return UserProfileOut.model_validate(profile)


@app.post("/users/{user_id}/applications", response_model=SavedApplicationOut)
def save_application(
    user_id: int, payload: ApplicationCreateIn, session: Session = Depends(get_session)
) -> SavedApplicationOut:
    try:
        job, application, job_created, application_created = save_application_for_user(
            session, user_id, payload.source_url, enrichment_service, ai_enrichment_service
        )
    except UserNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from error
    except UnsafeUrlError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsafe URL") from error

    return SavedApplicationOut(
        job=job,
        application=application,
        job_created=job_created,
        application_created=application_created,
    )
