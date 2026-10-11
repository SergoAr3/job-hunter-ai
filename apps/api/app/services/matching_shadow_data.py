"""Bounded consistent reads, separated from comparison and privacy projection."""
from collections.abc import Callable
from contextlib import contextmanager

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, load_only

from app.models import Application, Job, UserProfile
from app.services.matching_shadow import compare_pair
from app.services.matching_shadow_report import ShadowReport
from app.services.skill_identity_reader import (
    FOUNDATION_TABLES, IdentityBatch, read_semantic_skills, read_skill_identities,
)


PROFILE_FIELDS = (UserProfile.id, UserProfile.user_id, UserProfile.target_roles, UserProfile.skills,
                  UserProfile.experience, UserProfile.languages, UserProfile.location,
                  UserProfile.workplace_preference, UserProfile.salary_min,
                  UserProfile.salary_currency, UserProfile.salary_period)
JOB_FIELDS = (Job.id, Job.title, Job.required_skills, Job.nice_to_have_skills, Job.seniority,
              Job.language_requirements, Job.location, Job.workplace_type, Job.salary_min,
              Job.salary_max, Job.salary_currency, Job.salary_period, Job.salary_period_inferred,
              Job.ai_enrichment_status)


@contextmanager
def consistent_read(session_factory: Callable[[], Session]):
    with session_factory() as session:
        if session.in_transaction() or session.new or session.dirty or session.deleted:
            raise ValueError("shadow_requires_clean_session")
        dialect = session.get_bind().dialect.name
        query_only = None
        try:
            if dialect == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ",
                                                      "postgresql_readonly": True})
            elif dialect == "sqlite":
                connection = session.connection()
                query_only = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
                connection.exec_driver_sql("PRAGMA query_only=ON")
                # sqlite3 legacy mode does not start a transaction for SELECT.
                if not connection.connection.driver_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN")
            else:
                raise ValueError("shadow_unsupported_database")
            with session.no_autoflush:
                yield session
        finally:
            if query_only is not None:
                connection.exec_driver_sql(f"PRAGMA query_only={int(query_only)}")
            # Session close rolls back. Never commit a diagnostic transaction.


def compare_matching(session_factory: Callable[[], Session], *, batch_size: int = 100,
                     limit: int = 1000, after_id: int = 0, pair_limit: int = 0) -> dict:
    if not 1 <= batch_size <= 1000 or not 1 <= limit <= 10000 or after_id < 0 or not 0 <= pair_limit <= 1000:
        raise ValueError("invalid_shadow_options")
    report = ShadowReport(after_id, pair_limit)
    selected = 0
    while selected < limit:
        with consistent_read(session_factory) as session:
            ready = FOUNDATION_TABLES <= set(inspect(session.connection()).get_table_names())
            report.schema_ready = report.schema_ready and ready
            ids = list(session.scalars(select(Application.id).where(Application.id > report.cursor)
                                       .order_by(Application.id).limit(min(batch_size, limit - selected))))
            if not ids:
                break
            pairs = list(session.execute(select(Application.id, Application.user_id, Application.job_id)
                                         .where(Application.id.in_(ids)).order_by(Application.id)))
            selected += len(ids)
            report.cursor = ids[-1]
            report.skipped += len(ids) - len(pairs)
            user_ids = {row.user_id for row in pairs}
            job_ids = {row.job_id for row in pairs}
            profiles = {profile.user_id: profile for profile in session.scalars(
                select(UserProfile).where(UserProfile.user_id.in_(user_ids))
                .options(load_only(*PROFILE_FIELDS, raiseload=True)))}
            jobs = {job.id: job for job in session.scalars(select(Job).where(Job.id.in_(job_ids))
                                                         .options(load_only(*JOB_FIELDS, raiseload=True)))}
            keys = set()
            for profile in profiles.values():
                keys.update(read_semantic_skills(profile.skills).keys)
            for job in jobs.values():
                keys.update(read_semantic_skills(job.required_skills).keys)
                keys.update(read_semantic_skills(job.nice_to_have_skills).keys)
            identities = read_skill_identities(session, keys, {p.id for p in profiles.values()}, job_ids) if ready else IdentityBatch()
            # Owner inputs and relations all belong to this batch's DB snapshot.
            cache = {}
            for application_id, user_id, job_id in pairs:
                profile, job = profiles.get(user_id), jobs.get(job_id)
                key = (user_id, job_id)
                if key not in cache:
                    cache[key] = compare_pair(profile, job, identities, schema_ready=ready)
                report.add(application_id, user_id, profile.id if profile else None, job_id, cache[key])
    return report.output()
