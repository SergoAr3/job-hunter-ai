import logging
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Application, ApplicationStatus, Job, User
from app.models import IngestionMethod, ParsingStatus
from app.services.job_sources import detect_job_source
from app.services.vacancy_enrichment import VacancyEnrichmentService

logger = logging.getLogger(__name__)


class UserNotFoundError(Exception):
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
    session: Session, user_id: int, source_url: str, enrichment_service: VacancyEnrichmentService | None = None
) -> tuple[Job, Application, bool, bool]:
    if session.get(User, user_id) is None:
        raise UserNotFoundError

    normalized_url = normalize_source_url(source_url)
    job, job_created = _get_or_create_job(session, normalized_url, detect_job_source(normalized_url))
    application, application_created = _get_or_create_application(session, user_id, job.id)
    job_id = job.id
    job_url = job.source_url
    session.commit()
    if session.in_transaction():
        raise RuntimeError("Database transaction must be closed before enrichment")
    if job_created:
        service = enrichment_service or VacancyEnrichmentService()
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
            raise RuntimeError("Saved job disappeared")
    return job, application, job_created, application_created


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
