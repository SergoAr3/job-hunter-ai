"""Configured current matching; legacy remains the explicit production default."""
import logging
from contextlib import ExitStack

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, object_session

from app import config
from app.models import Application, Job, UserProfile
from app.schemas import MatchResultOut
from app.services.job_matching import _calculate_saved_match, calculate_match
from app.services.matcher_hybrid_skills import evaluate_hybrid_skills
from app.services.matcher_owner_read import HybridUnavailable, read_hybrid_skills


logger = logging.getLogger(__name__)


def calculate_current_match(
    session: Session | None, profile: UserProfile, job: Job, application: Application,
    *, algorithm_version: str | None = None,
) -> MatchResultOut:
    version = config.MATCH_ALGORITHM_VERSION if algorithm_version is None else algorithm_version
    if version == "job-match-v2.1":
        return calculate_match(profile, job, application)
    if version != "job-match-v2.2":
        raise ValueError("unsupported_match_algorithm_version")
    reason = None
    if (session is None or session.new or session.dirty or session.deleted
            or object_session(profile) is not session or object_session(job) is not session):
        # Never flush pending caller mutations or refresh them away.
        reason = "db_consistency_failure"
    else:
        try:
            with session.no_autoflush:
                connection = session.connection()
                if connection.dialect.name == "sqlite":
                    # Legacy sqlite3 does not BEGIN for SELECT. Ensure releasing
                    # a savepoint cannot end the caller's outer transaction.
                    driver = connection.connection.driver_connection
                    if driver is None:
                        raise HybridUnavailable("db_consistency_failure")
                    if not driver.in_transaction:
                        connection.exec_driver_sql("BEGIN")
                # Session.begin_nested() flushes even under no_autoflush. Use a
                # connection savepoint: SQL errors cannot poison snapshot writes.
                with connection.begin_nested():
                    skills = read_hybrid_skills(session, profile, job)
                components = evaluate_hybrid_skills(skills, available=job.ai_enrichment_status == "success")
                return _calculate_saved_match(profile, job, application,
                                              algorithm_version="job-match-v2.2", skill_components=components)
        except HybridUnavailable as error:
            reason = str(error)
        except SQLAlchemyError:
            # No exception text/traceback: these may expose SQL parameters.
            reason = "db_consistency_failure"
    logger.info("match_v22_fallback reason_code=%s", reason)
    # Legacy accepts arbitrary strings, but not corrupt arrays/items. Do not
    # turn malformed JSON into a misleading zero/unknown score via short circuits.
    with ExitStack() as stack:
        # A fallback can also read expired objects attached to another Session.
        # Suppress its autoflush as well; the legacy computation owns no writes.
        owner_sessions = {session, object_session(profile), object_session(job), object_session(application)}
        for owner_session in owner_sessions:
            if owner_session is not None:
                stack.enter_context(owner_session.no_autoflush)
        if any(not isinstance(values, list) or any(not isinstance(item, str) for item in values)
               for values in (profile.skills, job.required_skills, job.nice_to_have_skills)):
            raise ValueError("invalid_match_semantic_input")
        return calculate_match(profile, job, application)
