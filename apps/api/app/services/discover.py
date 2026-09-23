from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Application, IngestionMethod, Job, JobSource, ParsingStatus, User, UserProfile
from app.schemas import DiscoverJobItemOut, DiscoverJobsPageOut, MatchPreviewOut
from app.services.applications import (
    UserNotFoundError,
    get_or_create_application,
    normalize_source_url,
    run_job_ai_enrichment,
)
from app.services.job_ai_enrichment import JobAIEnrichmentService
from app.services.job_matching import (
    MatchEvidenceAvailability,
    calculate_match_preview,
)
from app.services.trudvsem import ExternalVacancy, SearchPage


class UnsupportedMarketError(ValueError):
    code = "unsupported_market"


class UnsupportedDiscoverSourceError(ValueError):
    code = "unsupported_source"


class SourceIdentityConflictError(Exception):
    code = "source_identity_conflict"


class DiscoverSourceClient(Protocol):
    def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int,
        region_code: str | None = None,
    ) -> SearchPage: ...

    def get_detail(self, source_scope: str, external_id: str) -> ExternalVacancy: ...


@dataclass(frozen=True)
class DiscoverSaveResult:
    job: Job
    application: Application
    job_created: bool
    application_created: bool


def search_discover_jobs(
    session: Session,
    user_id: int,
    *,
    market_country: str,
    query: str,
    limit: int,
    offset: int,
    region_code: str | None,
    remote_only: bool,
    client: DiscoverSourceClient,
) -> DiscoverJobsPageOut:
    if market_country.upper() != "RU":
        raise UnsupportedMarketError
    if session.get(User, user_id) is None:
        raise UserNotFoundError
    profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is not None:
        session.expunge(profile)
    session.rollback()

    page = client.search(query, limit=limit, offset=offset, region_code=region_code)
    vacancies = [item for item in page.items if not remote_only or item.workplace_type == "remote"]
    saved = _saved_identity_keys(session, user_id, vacancies)
    items = [
        _discover_item(
            vacancy,
            profile,
            _identity_key(vacancy) in saved or normalize_source_url(vacancy.source_url) in saved,
        )
        for vacancy in vacancies
    ]
    next_offset = offset + limit if offset + limit < page.total else None
    return DiscoverJobsPageOut(
        items=items,
        source_total=page.total,
        returned_count=len(items),
        limit=limit,
        offset=offset,
        next_offset=next_offset,
        locally_filtered=remote_only,
    )


def save_discovered_job(
    session: Session,
    user_id: int,
    *,
    source: str,
    source_scope: str,
    external_id: str,
    client: DiscoverSourceClient,
    ai_service: JobAIEnrichmentService,
) -> DiscoverSaveResult:
    if source != JobSource.TRUDVSEM.value:
        raise UnsupportedDiscoverSourceError
    if session.get(User, user_id) is None:
        raise UserNotFoundError
    session.rollback()

    vacancy = client.get_detail(source_scope, external_id)
    try:
        job, job_created = _get_or_create_external_job(session, vacancy)
        application, application_created = get_or_create_application(session, user_id, job.id)
        job_id = job.id
        session.commit()
    except Exception:
        session.rollback()
        raise

    job = session.get(Job, job_id)
    if job is None:
        raise RuntimeError("Saved Discover job disappeared")
    if job_created or job.ai_enrichment_status in {"not_attempted", "failed"}:
        run_job_ai_enrichment(session, job, ai_service, merge_existing=not job_created)
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError("Saved Discover job disappeared after enrichment")
    return DiscoverSaveResult(job, application, job_created, application_created)


def _get_or_create_external_job(session: Session, vacancy: ExternalVacancy) -> tuple[Job, bool]:
    normalized_url = normalize_source_url(vacancy.source_url)
    identity_job, url_job = _locked_external_job_candidates(session, vacancy, normalized_url)
    if identity_job is not None or url_job is not None:
        return _reuse_external_job(session, vacancy, normalized_url, identity_job, url_job)

    job = Job(
        source=JobSource.TRUDVSEM.value,
        ingestion_method=IngestionMethod.DISCOVER.value,
        source_url=normalized_url,
        external_id=vacancy.external_id,
        source_scope=vacancy.source_scope,
        parsing_status=ParsingStatus.PARTIAL.value,
        required_skills=[],
        nice_to_have_skills=[],
        experience_requirements=[],
        language_requirements=[],
        responsibilities=[],
    )
    _refresh_external_job(job, vacancy)
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        identity_job, url_job = _locked_external_job_candidates(session, vacancy, normalized_url)
        if identity_job is None and url_job is None:
            raise
        return _reuse_external_job(session, vacancy, normalized_url, identity_job, url_job)
    return job, True


def _locked_external_job_candidates(
    session: Session,
    vacancy: ExternalVacancy,
    normalized_url: str,
) -> tuple[Job | None, Job | None]:
    candidates = list(
        session.scalars(
            select(Job)
            .where(
                or_(
                    and_(
                        Job.source == JobSource.TRUDVSEM.value,
                        Job.source_scope == vacancy.source_scope,
                        Job.external_id == vacancy.external_id,
                    ),
                    Job.source_url == normalized_url,
                )
            )
            .order_by(Job.id)
            .with_for_update()
        )
    )
    identity_job = next(
        (
            job
            for job in candidates
            if job.source == JobSource.TRUDVSEM.value
            and job.source_scope == vacancy.source_scope
            and job.external_id == vacancy.external_id
        ),
        None,
    )
    url_job = next((job for job in candidates if job.source_url == normalized_url), None)
    return identity_job, url_job


def _reuse_external_job(
    session: Session,
    vacancy: ExternalVacancy,
    normalized_url: str,
    identity_job: Job | None,
    url_job: Job | None,
) -> tuple[Job, bool]:
    if identity_job is not None and url_job is not None and identity_job.id != url_job.id:
        raise SourceIdentityConflictError
    if identity_job is not None:
        try:
            with session.begin_nested():
                identity_job.source_url = normalized_url
                _refresh_external_job(identity_job, vacancy)
                session.flush()
        except IntegrityError as error:
            raise SourceIdentityConflictError from error
        return identity_job, False
    if url_job is not None:
        if (url_job.external_id, url_job.source_scope) not in {
            (None, None),
            (vacancy.external_id, vacancy.source_scope),
        }:
            raise SourceIdentityConflictError
        try:
            with session.begin_nested():
                if url_job.external_id is None:
                    url_job.source = JobSource.TRUDVSEM.value
                    url_job.external_id = vacancy.external_id
                    url_job.source_scope = vacancy.source_scope
                    _fill_missing_job_fields(url_job, vacancy)
                else:
                    _refresh_external_job(url_job, vacancy)
                if vacancy.source_updated_at is not None:
                    url_job.source_updated_at = vacancy.source_updated_at
                url_job.fetched_at = vacancy.fetched_at
                session.flush()
        except IntegrityError as error:
            raise SourceIdentityConflictError from error
        return url_job, False
    raise RuntimeError("Expected an external identity or URL candidate")


def _refresh_external_job(job: Job, vacancy: ExternalVacancy) -> None:
    for field in (
        "title",
        "company",
        "description",
        "requirements_text",
        "location",
        "salary_text",
        "salary_currency",
    ):
        value = getattr(vacancy, field)
        if _is_known(value):
            setattr(job, field, value)
    if vacancy.workplace_type != "unknown":
        job.workplace_type = vacancy.workplace_type
    _merge_salary_bounds(job, vacancy, overwrite=True)
    if vacancy.source_updated_at is not None:
        job.source_updated_at = vacancy.source_updated_at
    job.fetched_at = vacancy.fetched_at
    _mark_deterministic_data_available(job)


def _fill_missing_job_fields(job: Job, vacancy: ExternalVacancy) -> None:
    for field in (
        "title",
        "company",
        "description",
        "requirements_text",
        "location",
        "salary_text",
        "salary_currency",
    ):
        if not _is_known(getattr(job, field)) and _is_known(getattr(vacancy, field)):
            setattr(job, field, getattr(vacancy, field))
    if job.workplace_type == "unknown" and vacancy.workplace_type == "remote":
        job.workplace_type = "remote"
    _merge_salary_bounds(job, vacancy, overwrite=False)
    _mark_deterministic_data_available(job)


def _merge_salary_bounds(job: Job, vacancy: ExternalVacancy, *, overwrite: bool) -> None:
    source_min = vacancy.salary_min if vacancy.salary_min is not None and vacancy.salary_min > 0 else None
    source_max = vacancy.salary_max if vacancy.salary_max is not None and vacancy.salary_max > 0 else None
    current_min = job.salary_min if job.salary_min is not None and job.salary_min > 0 else None
    current_max = job.salary_max if job.salary_max is not None and job.salary_max > 0 else None

    if source_min is not None and source_max is not None:
        if overwrite:
            job.salary_min = source_min
            job.salary_max = source_max
            return
        candidate_min = current_min if current_min is not None else source_min
        candidate_max = current_max if current_max is not None else source_max
        if candidate_min <= candidate_max:
            if current_min is None:
                job.salary_min = source_min
            if current_max is None:
                job.salary_max = source_max
        return
    if source_min is not None and (overwrite or current_min is None):
        if current_max is None or source_min <= current_max:
            job.salary_min = source_min
    if source_max is not None and (overwrite or current_max is None):
        effective_min = job.salary_min if job.salary_min is not None and job.salary_min > 0 else None
        if effective_min is None or source_max >= effective_min:
            job.salary_max = source_max


def _mark_deterministic_data_available(job: Job) -> None:
    if job.parsing_status in {"not_attempted", "pending", "failed"}:
        job.parsing_status = ParsingStatus.PARTIAL.value
        job.parsing_error = None


def _is_known(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _discover_item(
    vacancy: ExternalVacancy,
    profile: UserProfile | None,
    already_saved: bool,
) -> DiscoverJobItemOut:
    if profile is None:
        preview = MatchPreviewOut(
            available=False, unavailable_reason="profile_missing_for_preview"
        )
    else:
        transient_job = Job(
            source=JobSource.TRUDVSEM.value,
            ingestion_method=IngestionMethod.DISCOVER.value,
            source_url=vacancy.source_url,
            title=vacancy.title,
            company=vacancy.company,
            description=vacancy.description,
            requirements_text=vacancy.requirements_text,
            location=vacancy.location,
            workplace_type=vacancy.workplace_type,
            salary_text=vacancy.salary_text,
            salary_min=vacancy.salary_min,
            salary_max=vacancy.salary_max,
            salary_currency=vacancy.salary_currency,
            salary_period="unknown",
            salary_period_inferred=False,
            required_skills=[],
            nice_to_have_skills=[],
            experience_requirements=[],
            language_requirements=[],
            responsibilities=[],
            seniority="unknown",
            parsing_status="partial",
            ai_enrichment_status="not_attempted",
        )
        preview = calculate_match_preview(
            profile,
            transient_job,
            evidence=MatchEvidenceAvailability(
                skills=False,
                languages=False,
                location=vacancy.location is not None and vacancy.workplace_type != "remote",
            ),
        )
    return DiscoverJobItemOut(
        source=JobSource.TRUDVSEM.value,
        source_scope=vacancy.source_scope,
        external_id=vacancy.external_id,
        source_url=vacancy.source_url,
        title=vacancy.title,
        company=vacancy.company,
        location=vacancy.location,
        workplace_type=vacancy.workplace_type,
        salary_text=vacancy.salary_text,
        salary_min=float(vacancy.salary_min) if vacancy.salary_min is not None else None,
        salary_max=float(vacancy.salary_max) if vacancy.salary_max is not None else None,
        salary_currency=vacancy.salary_currency,
        source_updated_at=vacancy.source_updated_at,
        preview_match=preview,
        already_saved_for_user=already_saved,
    )


def _saved_identity_keys(
    session: Session,
    user_id: int,
    vacancies: list[ExternalVacancy],
) -> set[tuple[str, str, str] | str]:
    if not vacancies:
        return set()
    identities = [(JobSource.TRUDVSEM.value, item.source_scope, item.external_id) for item in vacancies]
    urls = [normalize_source_url(item.source_url) for item in vacancies]
    rows = session.execute(
        select(Job, Application.id)
        .outerjoin(
            Application,
            and_(Application.job_id == Job.id, Application.user_id == user_id),
        )
        .where(
            or_(
                tuple_(Job.source, Job.source_scope, Job.external_id).in_(identities),
                Job.source_url.in_(urls),
            )
        )
    )
    saved: set[tuple[str, str, str] | str] = set()
    for job, application_id in rows:
        if application_id is None:
            continue
        if job.source_scope is not None and job.external_id is not None:
            saved.add((job.source, job.source_scope, job.external_id))
        saved.add(job.source_url)
    return saved


def _identity_key(vacancy: ExternalVacancy) -> tuple[str, str, str]:
    return JobSource.TRUDVSEM.value, vacancy.source_scope, vacancy.external_id
