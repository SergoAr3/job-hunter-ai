from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Job
from app.schemas import (
    ApplicationCreateIn,
    ApplicationDetailOut,
    ApplicationListItemOut,
    ApplicationOut,
    ApplicationsPageOut,
    JobOut,
    MatchResultOut,
    SavedApplicationOut,
    TelegramUserIn,
    TelegramUserOut,
    UserProfileOut,
    UserProfilePutIn,
)
from app.services.applications import (
    UnsafeUrlError,
    UserNotFoundError,
    get_application_for_user,
    list_applications_for_user,
    save_application_for_user,
)
from app.services.job_matching import calculate_match
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
        job=JobOut.model_validate(job),
        application=ApplicationOut.model_validate(application),
        job_created=job_created,
        application_created=application_created,
    )


@app.get("/users/{user_id}/applications", response_model=ApplicationsPageOut)
def read_applications(
    user_id: int,
    limit: int = Query(default=5, ge=1, le=5),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ApplicationsPageOut:
    rows = list_applications_for_user(session, user_id, limit=limit, offset=offset)
    return ApplicationsPageOut(
        items=[
            ApplicationListItemOut(
                app_id=application.id,
                job_id=job.id,
                created_at=application.created_at,
                title=job.title,
                company=job.company,
                location=job.location,
                workplace_type=job.workplace_type,
                parsing_status=job.parsing_status,
                ai_enrichment_status=job.ai_enrichment_status,
            )
            for application, job in rows[:limit]
        ],
        has_next=len(rows) > limit,
    )


@app.get("/users/{user_id}/applications/{application_id}", response_model=ApplicationDetailOut)
def read_application(
    user_id: int, application_id: int, session: Session = Depends(get_session)
) -> ApplicationDetailOut:
    application = get_application_for_user(session, user_id, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "APPLICATION_NOT_FOUND"})
    job = session.get(Job, application.job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "APPLICATION_NOT_FOUND"})
    return ApplicationDetailOut(
        application=ApplicationOut.model_validate(application), job=JobOut.model_validate(job)
    )


@app.get("/users/{user_id}/applications/{application_id}/match", response_model=MatchResultOut)
def read_application_match(
    user_id: int, application_id: int, session: Session = Depends(get_session)
) -> MatchResultOut:
    application = get_application_for_user(session, user_id, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "APPLICATION_NOT_FOUND"})
    try:
        profile = get_user_profile(session, user_id)
    except (ProfileUserNotFoundError, UserProfileNotFoundError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "PROFILE_REQUIRED"}) from error
    job = session.get(Job, application.job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "APPLICATION_NOT_FOUND"})
    return calculate_match(profile, job, application)
