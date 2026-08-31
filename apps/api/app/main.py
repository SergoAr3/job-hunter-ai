from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool
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
from app.services.cv_profile_draft import (
    ERROR_AI_PROVIDER,
    ERROR_AI_TIMEOUT,
    ERROR_AI_UNAVAILABLE,
    ERROR_FILE_TOO_LARGE,
    ERROR_INSUFFICIENT_JOB_INFORMATION,
    ERROR_INVALID_AI_OUTPUT,
    ERROR_MALFORMED_DOCUMENT,
    ERROR_NO_EXTRACTABLE_TEXT,
    ERROR_UNSUPPORTED_FILE_TYPE,
    ERROR_USER_NOT_FOUND,
    MAX_UPLOAD_BYTES,
    CVProfileDraftAIService,
    CVProfileDraftError,
    create_profile_draft_from_cv,
)
from app.services.users import get_or_create_telegram_user
from app.request_limits import CVUploadBodyLimitMiddleware
from app.services.user_profiles import (
    UserNotFoundError as ProfileUserNotFoundError,
    UserProfileNotFoundError,
    get_user_profile,
    put_user_profile,
)
from app.services.vacancy_enrichment import VacancyEnrichmentService
from app.services.job_ai_enrichment import JobAIEnrichmentService

app = FastAPI(title="Job Hunter AI API")
app.add_middleware(CVUploadBodyLimitMiddleware)
enrichment_service = VacancyEnrichmentService()
ai_enrichment_service = JobAIEnrichmentService()
cv_profile_draft_ai_service = CVProfileDraftAIService()

CV_DRAFT_ERROR_STATUS = {
    ERROR_USER_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ERROR_FILE_TOO_LARGE: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    ERROR_UNSUPPORTED_FILE_TYPE: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    ERROR_MALFORMED_DOCUMENT: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ERROR_NO_EXTRACTABLE_TEXT: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ERROR_INSUFFICIENT_JOB_INFORMATION: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ERROR_INVALID_AI_OUTPUT: status.HTTP_502_BAD_GATEWAY,
    ERROR_AI_PROVIDER: status.HTTP_502_BAD_GATEWAY,
    ERROR_AI_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ERROR_AI_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
}


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


@app.post("/users/{user_id}/profile/draft-from-cv", response_model=UserProfilePutIn)
async def draft_user_profile_from_cv(
    user_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> UserProfilePutIn:
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise CVProfileDraftError(ERROR_FILE_TOO_LARGE)
        return await run_in_threadpool(
            create_profile_draft_from_cv,
            session,
            user_id,
            filename=file.filename,
            content_type=file.content_type,
            content=content,
            ai_service=cv_profile_draft_ai_service,
        )
    except CVProfileDraftError as error:
        raise HTTPException(
            status_code=CV_DRAFT_ERROR_STATUS[error.code], detail=error.code
        ) from error
    finally:
        await file.close()


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
