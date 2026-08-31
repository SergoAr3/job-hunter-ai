import logging
import re
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AIEnrichmentStatus, Application, ApplicationStatus, Job, User
from app.models import IngestionMethod, ParsingStatus
from app.services.job_ai_enrichment import (
    AIEnrichmentResult,
    JobAIEnrichmentService,
    SalaryPeriod,
    SalaryPeriodEvidence,
    VacancyAIInput,
)
from app.services.job_sources import detect_job_source
from app.services.safe_http_fetcher import BlockedUrlError, FetchError
from app.services.salary_validation import is_iso_4217_currency
from app.services.vacancy_enrichment import VacancyEnrichmentService

logger = logging.getLogger(__name__)


class UserNotFoundError(Exception):
    pass


class UnsafeUrlError(Exception):
    pass


def normalize_source_url(source_url: str) -> str:
    parsed = urlsplit(source_url)
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("source_url must include a host")

    netloc = parsed.netloc
    hostname_start = netloc.lower().rfind(hostname.lower())
    normalized_netloc = (
        f"{netloc[:hostname_start]}{hostname.lower()}{netloc[hostname_start + len(hostname):]}"
    )
    return urlunsplit((parsed.scheme.lower(), normalized_netloc, parsed.path, parsed.query, ""))


def save_application_for_user(
    session: Session,
    user_id: int,
    source_url: str,
    enrichment_service: VacancyEnrichmentService | None = None,
    ai_enrichment_service: JobAIEnrichmentService | None = None,
) -> tuple[Job, Application, bool, bool]:
    normalized_url = normalize_source_url(source_url)
    service = enrichment_service or VacancyEnrichmentService()
    ai_service = ai_enrichment_service or JobAIEnrichmentService()
    try:
        service.preflight(normalized_url)
    except BlockedUrlError as error:
        raise UnsafeUrlError from error
    except FetchError:
        pass

    if session.get(User, user_id) is None:
        raise UserNotFoundError

    job, job_created = _get_or_create_job(session, normalized_url, detect_job_source(normalized_url))
    application, application_created = _get_or_create_application(session, user_id, job.id)
    job_id = job.id
    job_url = job.source_url
    session.commit()
    if session.in_transaction():
        raise RuntimeError("Database transaction must be closed before enrichment")
    if job_created:
        data, error = service.enrich(job_url)
        try:
            job = session.get(Job, job_id)
            if job is None:
                raise RuntimeError("Saved job disappeared before enrichment update")
            if data is None:
                job.parsing_status = ParsingStatus.FAILED.value
                job.parsing_error = error
            else:
                for key, value in service.values(data).items():
                    setattr(job, key, value)
                job.parsing_error = None
            session.commit()
            session.refresh(job)
        except Exception:
            session.rollback()
            logger.exception("Could not persist job enrichment", extra={"job_id": job_id})
            try:
                job = session.get(Job, job_id)
                if job is not None and job.parsing_status == ParsingStatus.PENDING.value:
                    job.parsing_status = ParsingStatus.FAILED.value
                    job.parsing_error = "enrichment_update_failed"
                    session.commit()
            except Exception:
                session.rollback()
                logger.exception("Could not mark failed enrichment", extra={"job_id": job_id})
    if job_created:
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError("Saved job disappeared before AI enrichment")
        _run_ai_enrichment(session, job, ai_service)
    if job_created:
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError("Saved job disappeared")
    if job is None:
        raise RuntimeError("Saved job disappeared")
    return job, application, job_created, application_created


def _run_ai_enrichment(session: Session, job: Job, service: JobAIEnrichmentService) -> None:
    vacancy = VacancyAIInput.from_job(job)
    if vacancy is None or not service.configured:
        return

    job_id = job.id
    job.ai_enrichment_status = AIEnrichmentStatus.PENDING.value
    job.ai_enrichment_error = None
    session.commit()
    if session.in_transaction():
        raise RuntimeError("Database transaction must be closed before AI enrichment")

    try:
        result, error = service.enrich(vacancy)
    except Exception:
        logger.exception("Unexpected AI enrichment failure", extra={"job_id": job_id})
        result, error = None, "processing_failed"
    try:
        refreshed_job = session.get(Job, job_id)
        if refreshed_job is None:
            raise RuntimeError("Saved job disappeared before AI enrichment update")
        if result is None:
            refreshed_job.ai_enrichment_status = AIEnrichmentStatus.FAILED.value
            refreshed_job.ai_enrichment_error = error or "processing_failed"
        else:
            _apply_ai_enrichment(refreshed_job, result)
            refreshed_job.ai_enrichment_status = AIEnrichmentStatus.SUCCESS.value
            refreshed_job.ai_enrichment_error = None
        session.commit()
        session.refresh(refreshed_job)
    except Exception:
        session.rollback()
        logger.exception("Could not persist AI enrichment", extra={"job_id": job_id})
        try:
            failed_job = session.get(Job, job_id)
            if failed_job is not None and failed_job.ai_enrichment_status == AIEnrichmentStatus.PENDING.value:
                failed_job.ai_enrichment_status = AIEnrichmentStatus.FAILED.value
                failed_job.ai_enrichment_error = "processing_failed"
                session.commit()
        except Exception:
            session.rollback()
            logger.exception("Could not mark failed AI enrichment", extra={"job_id": job_id})


def _apply_ai_enrichment(job: Job, result: AIEnrichmentResult) -> None:
    job.required_skills = result.required_skills
    job.nice_to_have_skills = result.nice_to_have_skills
    job.experience_requirements = result.experience_requirements
    job.language_requirements = result.language_requirements
    job.responsibilities = result.responsibilities
    job.seniority = _validated_seniority(job, result)

    if job.location is None and result.location is not None:
        job.location = result.location
    if job.workplace_type == "unknown" and result.workplace_type.value != "unknown":
        job.workplace_type = result.workplace_type.value
    if job.employment_type == "unknown" and result.employment_type.value != "unknown":
        job.employment_type = result.employment_type.value

    supplement = _validated_salary_supplement(job, result)
    if supplement is not None:
        for key, value in supplement.items():
            setattr(job, key, value)


def _validated_salary_supplement(job: Job, result: AIEnrichmentResult) -> dict[str, object] | None:
    """Return one coherent AI salary block, or reject it without partial writes."""
    ai_has_amount = result.salary_min is not None or result.salary_max is not None
    if not ai_has_amount:
        return None
    if result.salary_currency is not None and not is_iso_4217_currency(result.salary_currency):
        return None
    # salary_text cannot be reconciled safely with an AI-derived numeric range.
    if job.salary_text is not None:
        return None
    if _conflicts(job.salary_min, result.salary_min) or _conflicts(job.salary_max, result.salary_max):
        return None
    if _conflicts(job.salary_currency, result.salary_currency):
        return None
    deterministic_period = job.salary_period if job.salary_period not in {None, "unknown"} else None
    if deterministic_period is not None and result.salary_period != SalaryPeriod.UNKNOWN and deterministic_period != result.salary_period.value:
        return None

    final_min = job.salary_min if job.salary_min is not None else result.salary_min
    final_max = job.salary_max if job.salary_max is not None else result.salary_max
    final_currency = job.salary_currency if job.salary_currency is not None else result.salary_currency
    final_period = deterministic_period or result.salary_period.value
    if not is_iso_4217_currency(final_currency) or (final_min is None and final_max is None):
        return None
    if final_min is not None and final_max is not None and final_min > final_max:
        return None
    if final_period == SalaryPeriod.UNKNOWN.value and result.salary_period_evidence != SalaryPeriodEvidence.UNKNOWN:
        return None

    supplement: dict[str, object] = {}
    if job.salary_min is None and result.salary_min is not None:
        supplement["salary_min"] = result.salary_min
    if job.salary_max is None and result.salary_max is not None:
        supplement["salary_max"] = result.salary_max
    if job.salary_currency is None and result.salary_currency is not None:
        supplement["salary_currency"] = result.salary_currency
    if deterministic_period is None and result.salary_period != SalaryPeriod.UNKNOWN:
        supplement["salary_period"] = result.salary_period.value
        supplement["salary_period_inferred"] = result.salary_period_evidence == SalaryPeriodEvidence.INFERRED
    return supplement or None


def _conflicts(existing: object, candidate: object) -> bool:
    return existing is not None and candidate is not None and existing != candidate


_OUT_OF_TAXONOMY_SENIORITY = re.compile(
    r"\b(?:staff|principal|head(?:\s+of)?|director|chief|vp|vice[ -]president|manager|директор\w*|руководител\w*|главн\w*)\b",
    re.IGNORECASE,
)


def _validated_seniority(job: Job, result: AIEnrichmentResult) -> str:
    source_text = "\n".join(
        value for value in (job.title, job.description, job.requirements_text) if isinstance(value, str)
    )
    if _OUT_OF_TAXONOMY_SENIORITY.search(source_text):
        return "unknown"
    return result.seniority.value


def _get_or_create_job(session: Session, source_url: str, source: str) -> tuple[Job, bool]:
    job = session.scalar(select(Job).where(Job.source_url == source_url))
    if job is not None:
        return job, False

    job = Job(source=source, ingestion_method=IngestionMethod.MANUAL.value, source_url=source_url, parsing_status=ParsingStatus.PENDING.value)
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        job = session.scalar(select(Job).where(Job.source_url == source_url))
        if job is None:
            raise
        return job, False
    return job, True


def _get_or_create_application(session: Session, user_id: int, job_id: int) -> tuple[Application, bool]:
    application = session.scalar(
        select(Application).where(Application.user_id == user_id, Application.job_id == job_id)
    )
    if application is not None:
        return application, False

    application = Application(user_id=user_id, job_id=job_id, status=ApplicationStatus.SAVED.value)
    try:
        with session.begin_nested():
            session.add(application)
            session.flush()
    except IntegrityError:
        application = session.scalar(
            select(Application).where(Application.user_id == user_id, Application.job_id == job_id)
        )
        if application is None:
            raise
        return application, False
    return application, True


def get_application_for_user(session: Session, user_id: int, application_id: int) -> Application | None:
    """Return an application only when it belongs to the requested user."""
    return session.scalar(
        select(Application).where(Application.id == application_id, Application.user_id == user_id)
    )
