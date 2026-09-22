import logging

from app.models import Application, Job, UserProfile
from app.services.job_matching import calculate_match, serialize_match_inputs


logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA_VERSION = 1
_RESULT_DETAIL_FIELDS = {
    "components",
    "strengths",
    "gaps",
    "unknowns",
    "conflicts",
    "recommendation",
}


def prepare_match_snapshot(
    profile: UserProfile | None,
    job: Job,
    application: Application,
) -> dict[str, object]:
    """Prepare one immutable captured result or an explicit unavailable marker."""
    if profile is None:
        return _unavailable("profile_missing")

    try:
        inputs = serialize_match_inputs(profile, job)
        result = calculate_match(profile, job, application)
    except Exception:
        logger.exception(
            "Could not calculate first-applied match snapshot",
            extra={"application_id": application.id, "job_id": job.id},
        )
        return _unavailable("matcher_error")

    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "capture_status": "captured",
        "unavailable_reason": None,
        "algorithm_version": result.algorithm_version,
        "score": result.score,
        "verdict": result.verdict,
        "coverage": result.coverage,
        "confidence": result.confidence,
        "profile_updated_at": result.input_state.profile_updated_at,
        "job_updated_at": result.input_state.job_updated_at,
        "job_parsing_status": result.input_state.parsing_status,
        "job_ai_enrichment_status": result.input_state.ai_enrichment_status,
        "inputs": inputs,
        "result_detail": result.model_dump(mode="json", include=_RESULT_DETAIL_FIELDS),
    }


def _unavailable(reason: str) -> dict[str, object]:
    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "capture_status": "unavailable",
        "unavailable_reason": reason,
        "algorithm_version": None,
        "score": None,
        "verdict": None,
        "coverage": None,
        "confidence": None,
        "profile_updated_at": None,
        "job_updated_at": None,
        "job_parsing_status": None,
        "job_ai_enrichment_status": None,
        "inputs": None,
        "result_detail": None,
    }
