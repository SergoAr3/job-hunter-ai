from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Application, ApplicationStatus, Job, User


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
    session: Session, user_id: int, source_url: str
) -> tuple[Job, Application, bool, bool]:
    if session.get(User, user_id) is None:
        raise UserNotFoundError

    normalized_url = normalize_source_url(source_url)
    job, job_created = _get_or_create_job(session, normalized_url)
    application, application_created = _get_or_create_application(session, user_id, job.id)
    session.commit()
    session.refresh(job)
    session.refresh(application)
    return job, application, job_created, application_created


def _get_or_create_job(session: Session, source_url: str) -> tuple[Job, bool]:
    job = session.scalar(select(Job).where(Job.source_url == source_url))
    if job is not None:
        return job, False

    job = Job(source="manual", source_url=source_url)
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
