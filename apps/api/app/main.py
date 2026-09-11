from datetime import date

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_session
from app.services.cover_letter import CoverLetterGenerationService, CoverLetterOut, CoverLetterError, build_context
from app.services.letter_languages import CoverLetterRequest, LanguageInput, normalize_language
from app.models import ApplicationStatus, Job
from app.schemas import (
    ApplicationCreateIn,
    ApplicationDetailOut,
    ApplicationListItemOut,
    ApplicationOut,
    ApplicationNotePutIn,
    ApplicationNextActionPutIn,
    ApplicationStatusPutIn,
    ApplicationStatusHistoryItemOut,
    ApplicationStatusHistoryOut,
    ApplicationSort,
    ApplicationsPageOut,
    CVProfileDraftOut,
    JobOut,
    ProfileLanguagesNormalizeIn,
    ProfileExperienceFactIn,
    ProfileExperienceFactOut,
    ProfileExperienceFactsOut,
    MatchResultOut,
    ProfileSkillsNormalizeIn,
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
    get_application_status_history,
    list_applications_for_user,
    normalize_application_search_query,
    InvalidApplicationSearchQueryError,
    save_application_for_user,
    set_application_status,
    set_application_note,
    set_application_next_action,
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
from app.services.profile_experience_facts import (
    DuplicateProfileExperienceFactError,
    ProfileExperienceFactLimitError,
    ProfileExperienceFactNotFoundError,
    ProfileNotFoundError,
    create_profile_experience_fact,
    delete_profile_experience_fact,
    list_user_profile_experience_facts,
    update_profile_experience_fact,
)
from app.services.vacancy_enrichment import VacancyEnrichmentService
from app.services.job_ai_enrichment import JobAIEnrichmentService

app = FastAPI(title="Job Hunter AI API")
cover_letter_service = CoverLetterGenerationService()


@app.post("/users/{user_id}/applications/{application_id}/cover-letter", response_model=CoverLetterOut)
def generate_cover_letter(user_id: int, application_id: int, payload: CoverLetterRequest, session: Session = Depends(get_session)) -> CoverLetterOut:
    try:
        context = build_context(session, user_id, application_id, payload.language)
        # Release the read transaction before the external writing request.
        session.rollback()
        return cover_letter_service.generate(context)
    except CoverLetterError as error:
        code = str(error)
        codes = {"APPLICATION_NOT_FOUND": 404, "PROFILE_REQUIRED": 409,
                 "INSUFFICIENT_JOB_INFORMATION": 422, "cover_letter_ai_unavailable": 503,
                 "cover_letter_ai_timeout": 504}
        raise HTTPException(status_code=codes.get(code, 502), detail={"code": code}) from None


@app.post("/cover-letter/language", response_model=CoverLetterRequest)
def resolve_cover_letter_language(payload: LanguageInput) -> CoverLetterRequest:
    try:
        return CoverLetterRequest(language=normalize_language(payload.text))
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "INVALID_LANGUAGE"}) from None


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


@app.post("/profile/skills/normalize", response_model=ProfileSkillsNormalizeIn)
def normalize_profile_skills_for_draft(payload: ProfileSkillsNormalizeIn) -> ProfileSkillsNormalizeIn:
    """Return the canonical API-domain representation without persisting a profile."""
    return payload


@app.post("/profile/languages/normalize", response_model=ProfileLanguagesNormalizeIn)
def normalize_profile_languages_for_draft(
    payload: ProfileLanguagesNormalizeIn,
) -> ProfileLanguagesNormalizeIn:
    """Return validated canonical language values without persisting a profile."""
    return payload


@app.put("/users/{user_id}/profile", response_model=UserProfileOut)
def replace_user_profile(
    user_id: int, payload: UserProfilePutIn, session: Session = Depends(get_session)
) -> UserProfileOut:
    try:
        profile = put_user_profile(session, user_id, payload)
    except ProfileUserNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from error
    return UserProfileOut.model_validate(profile)


@app.get("/users/{user_id}/profile/experience-facts", response_model=ProfileExperienceFactsOut)
def read_profile_experience_facts(
    user_id: int, session: Session = Depends(get_session),
) -> ProfileExperienceFactsOut:
    try:
        facts = list_user_profile_experience_facts(session, user_id)
    except ProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "PROFILE_NOT_FOUND"}) from error
    return ProfileExperienceFactsOut(items=[ProfileExperienceFactOut.model_validate(fact) for fact in facts])


@app.post(
    "/users/{user_id}/profile/experience-facts",
    response_model=ProfileExperienceFactOut,
    status_code=status.HTTP_201_CREATED,
)
def create_experience_fact(
    user_id: int, payload: ProfileExperienceFactIn, session: Session = Depends(get_session),
) -> ProfileExperienceFactOut:
    try:
        fact = create_profile_experience_fact(session, user_id, payload)
    except ProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "PROFILE_NOT_FOUND"}) from error
    except DuplicateProfileExperienceFactError as error:
        raise HTTPException(status_code=422, detail={"code": "DUPLICATE_EXPERIENCE_FACT"}) from error
    except ProfileExperienceFactLimitError as error:
        raise HTTPException(status_code=422, detail={"code": "EXPERIENCE_FACT_LIMIT_REACHED"}) from error
    return ProfileExperienceFactOut.model_validate(fact)


@app.put("/users/{user_id}/profile/experience-facts/{fact_id}", response_model=ProfileExperienceFactOut)
def replace_experience_fact(
    user_id: int, fact_id: int, payload: ProfileExperienceFactIn,
    session: Session = Depends(get_session),
) -> ProfileExperienceFactOut:
    try:
        fact = update_profile_experience_fact(session, user_id, fact_id, payload)
    except ProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "PROFILE_NOT_FOUND"}) from error
    except ProfileExperienceFactNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "EXPERIENCE_FACT_NOT_FOUND"}) from error
    except DuplicateProfileExperienceFactError as error:
        raise HTTPException(status_code=422, detail={"code": "DUPLICATE_EXPERIENCE_FACT"}) from error
    return ProfileExperienceFactOut.model_validate(fact)


@app.delete("/users/{user_id}/profile/experience-facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_experience_fact(
    user_id: int, fact_id: int, session: Session = Depends(get_session),
) -> None:
    try:
        delete_profile_experience_fact(session, user_id, fact_id)
    except ProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "PROFILE_NOT_FOUND"}) from error
    except ProfileExperienceFactNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "EXPERIENCE_FACT_NOT_FOUND"}) from error


@app.post("/users/{user_id}/profile/draft-from-cv", response_model=CVProfileDraftOut)
async def draft_user_profile_from_cv(
    user_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> CVProfileDraftOut:
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
    status: ApplicationStatus | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: ApplicationSort = Query(default=ApplicationSort.NEWEST),
    limit: int = Query(default=5, ge=1, le=5),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ApplicationsPageOut:
    try:
        search_query = normalize_application_search_query(q)
    except InvalidApplicationSearchQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    rows = list_applications_for_user(
        session, user_id, limit=limit, offset=offset, status=status, q=search_query, sort=sort
    )
    return ApplicationsPageOut(
        items=[
            ApplicationListItemOut(
                app_id=application.id,
                status=ApplicationStatus(application.status),
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


@app.put("/users/{user_id}/applications/{application_id}/status", response_model=ApplicationDetailOut)
def update_application_status(
    user_id: int, application_id: int, payload: ApplicationStatusPutIn,
    session: Session = Depends(get_session),
) -> ApplicationDetailOut:
    result = set_application_status(session, user_id, application_id, payload.status)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "APPLICATION_NOT_FOUND"})
    application, job = result
    return ApplicationDetailOut(
        application=ApplicationOut.model_validate(application), job=JobOut.model_validate(job)
    )


@app.get(
    "/users/{user_id}/applications/{application_id}/status-history",
    response_model=ApplicationStatusHistoryOut,
)
def read_application_status_history(
    user_id: int, application_id: int, session: Session = Depends(get_session),
) -> ApplicationStatusHistoryOut:
    history = get_application_status_history(session, user_id, application_id)
    if history is None:
        raise HTTPException(status_code=404, detail={"code": "APPLICATION_NOT_FOUND"})
    return ApplicationStatusHistoryOut(
        items=[ApplicationStatusHistoryItemOut.model_validate(item) for item in history]
    )


@app.put("/users/{user_id}/applications/{application_id}/note", response_model=ApplicationDetailOut)
def update_application_note(
    user_id: int, application_id: int, payload: ApplicationNotePutIn,
    session: Session = Depends(get_session),
) -> ApplicationDetailOut:
    return _write_application_note(session, user_id, application_id, payload.note)


@app.delete("/users/{user_id}/applications/{application_id}/note", response_model=ApplicationDetailOut)
def delete_application_note(
    user_id: int, application_id: int, session: Session = Depends(get_session),
) -> ApplicationDetailOut:
    return _write_application_note(session, user_id, application_id, None)


def _write_application_note(
    session: Session, user_id: int, application_id: int, note: str | None,
) -> ApplicationDetailOut:
    result = set_application_note(session, user_id, application_id, note)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "APPLICATION_NOT_FOUND"})
    application, job = result
    return ApplicationDetailOut(
        application=ApplicationOut.model_validate(application), job=JobOut.model_validate(job)
    )


@app.put("/users/{user_id}/applications/{application_id}/next-action", response_model=ApplicationDetailOut)
def update_application_next_action(
    user_id: int, application_id: int, payload: ApplicationNextActionPutIn,
    session: Session = Depends(get_session),
) -> ApplicationDetailOut:
    return _write_application_next_action(
        session, user_id, application_id, payload.next_action, payload.next_action_due_on
    )


@app.delete("/users/{user_id}/applications/{application_id}/next-action", response_model=ApplicationDetailOut)
def delete_application_next_action(
    user_id: int, application_id: int, session: Session = Depends(get_session),
) -> ApplicationDetailOut:
    return _write_application_next_action(session, user_id, application_id, None, None)


def _write_application_next_action(
    session: Session, user_id: int, application_id: int, action: str | None, due_on: date | None,
) -> ApplicationDetailOut:
    result = set_application_next_action(session, user_id, application_id, action, due_on)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "APPLICATION_NOT_FOUND"})
    application, job = result
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
