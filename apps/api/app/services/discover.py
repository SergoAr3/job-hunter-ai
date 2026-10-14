from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Application, IngestionMethod, Job, ParsingStatus, User, UserProfile
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
from app.services.vacancy_sources import DiscoverQuery, ExternalVacancy, VacancySourceProvider


class UnsupportedMarketError(ValueError):
    code = "unsupported_market"


class UnsupportedDiscoverSourceError(ValueError):
    code = "unsupported_source"


class SourceIdentityConflictError(Exception):
    code = "source_identity_conflict"


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
    provider: VacancySourceProvider,
) -> DiscoverJobsPageOut:
    if not provider.supports_market(market_country.upper()):
        raise UnsupportedMarketError
    if session.get(User, user_id) is None:
        raise UserNotFoundError
    profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is not None:
        session.expunge(profile)
    session.rollback()

    page = provider.search(
        DiscoverQuery(
            keywords=query.strip(),
            market_country=market_country.upper(),
            market_region=region_code,
            limit=limit,
            offset=offset,
        )
    )
    vacancies = [item for item in page.items if not remote_only or item.workplace_type == "remote"]
    saved = _saved_applications(session, user_id, vacancies)
    items = [
        _discover_item(
            vacancy,
            profile,
            saved.get(_identity_key(vacancy)),
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
    provider: VacancySourceProvider,
    ai_service: JobAIEnrichmentService,
) -> DiscoverSaveResult:
    if source != provider.source_key:
        raise UnsupportedDiscoverSourceError
    if session.get(User, user_id) is None:
        raise UserNotFoundError
    session.rollback()

    vacancy = provider.get_detail(source_scope, external_id)
    try:
        if session.get_bind().dialect.name == "sqlite":
            # Reserve SQLite's writer before the identity SELECT: a deferred
            # read transaction cannot safely upgrade when another Save writes.
            # The outer transaction also keeps nested inserts rollback-safe.
            connection = session.connection()
            if not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                # Engines that already BEGIN need a write reservation too.
                # This changes no rows and happens before any identity read.
                connection.exec_driver_sql("UPDATE jobs SET id = id WHERE 0")
        job, job_created = _get_or_create_external_job(session, vacancy)
        application, application_created = get_or_create_application(session, user_id, job.id)
        job_id = job.id
        application_id = application.id
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
    # Runtime sessions do not expire on commit; reload CRM state after the
    # transaction and optional enrichment instead of returning a cached status.
    application = session.get(Application, application_id, populate_existing=True)
    if application is None:
        raise RuntimeError("Saved Discover application disappeared")
    return DiscoverSaveResult(job, application, job_created, application_created)


def _get_or_create_external_job(session: Session, vacancy: ExternalVacancy) -> tuple[Job, bool]:
    normalized_url = normalize_source_url(vacancy.source_url)
    identity_job, url_job = _locked_external_job_candidates(session, vacancy, normalized_url)
    if identity_job is not None or url_job is not None:
        return _reuse_external_job(session, vacancy, normalized_url, identity_job, url_job)

    job = Job(
        source=vacancy.source,
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
                        Job.source == vacancy.source,
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
            if job.source == vacancy.source
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
        if url_job.external_id is not None and (
            url_job.source, url_job.source_scope, url_job.external_id
        ) != _identity_key(vacancy):
            raise SourceIdentityConflictError
        try:
            with session.begin_nested():
                if url_job.external_id is None:
                    url_job.source = vacancy.source
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
    application_state: tuple[int, str] | None,
) -> DiscoverJobItemOut:
    if profile is None:
        preview = MatchPreviewOut(
            available=False, unavailable_reason="profile_missing_for_preview"
        )
    else:
        transient_job = Job(
            source=vacancy.source,
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
        # Transient source evidence deliberately stays on the v2.1 preview
        # contract, independently of current saved-match configuration.
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
        source=vacancy.source,
        source_scope=vacancy.source_scope,
        external_id=vacancy.external_id,
        source_url=vacancy.source_url,
        title=vacancy.title,
        company=vacancy.company,
        description=vacancy.description,
        requirements_text=vacancy.requirements_text,
        location=vacancy.location,
        workplace_type=vacancy.workplace_type,
        salary_text=vacancy.salary_text,
        salary_min=float(vacancy.salary_min) if vacancy.salary_min is not None else None,
        salary_max=float(vacancy.salary_max) if vacancy.salary_max is not None else None,
        salary_currency=vacancy.salary_currency,
        source_updated_at=vacancy.source_updated_at,
        preview_match=preview,
        already_saved_for_user=application_state is not None,
        application_id=application_state[0] if application_state is not None else None,
        application_status=application_state[1] if application_state is not None else None,
    )


def _saved_applications(
    session: Session,
    user_id: int,
    vacancies: list[ExternalVacancy],
) -> dict[tuple[str, str, str], tuple[int, str]]:
    if not vacancies:
        return {}
    identities = [_identity_key(item) for item in vacancies]
    urls = [normalize_source_url(item.source_url) for item in vacancies]
    rows = session.execute(
        select(
            Job.id, Job.source, Job.source_scope, Job.external_id, Job.source_url,
            Application.id, Application.status,
        )
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
    identity_jobs: dict[tuple[str, str, str], tuple[int, tuple[int, str] | None]] = {}
    url_jobs: dict[str, tuple[int, tuple[str, str, str] | None, tuple[int, str] | None]] = {}
    for job_id, source, scope, external_id, url, application_id, status in rows:
        state = (application_id, status) if application_id is not None else None
        identity = (source, scope, external_id) if scope is not None and external_id is not None else None
        if identity is not None:
            identity_jobs[identity] = (job_id, state)
        url_jobs[url] = (job_id, identity, state)
    saved: dict[tuple[str, str, str], tuple[int, str]] = {}
    for vacancy, url in zip(vacancies, urls):
        key = _identity_key(vacancy)
        identity_job = identity_jobs.get(key)
        url_job = url_jobs.get(url)
        # Mirror Save's candidate resolution before returning any handoff.
        # Conflicts matter even when either Job has no owned Application.
        if identity_job is not None:
            if url_job is not None and identity_job[0] != url_job[0]:
                continue
            state = identity_job[1]
        elif url_job is not None:
            if url_job[1] is not None and url_job[1] != key:
                continue
            state = url_job[2]
        else:
            state = None
        if state is not None:
            saved[key] = state
    return saved


def _identity_key(vacancy: ExternalVacancy) -> tuple[str, str, str]:
    return vacancy.source, vacancy.source_scope, vacancy.external_id
