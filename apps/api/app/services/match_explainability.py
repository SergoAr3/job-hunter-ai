"""Read-side notes for the current scorer. Never computes scores or skill identity."""
from app.models import Job, UserProfile
from app.schemas import (
    MatchComponentExplanationOut, MatchExplainabilityOut, MatchExplanationCode,
    MatchExplanationItemOut, MatchResultOut,
)
from app.services.job_matching import (
    _canonical_language, _level, _parse_language_requirement, _SENIORITY_ORDER,
)


def explain_match(profile: UserProfile, job: Job, result: MatchResultOut) -> MatchExplainabilityOut:
    components = {name: MatchComponentExplanationOut() for name in result.components}

    def notice(name: str, code: MatchExplanationCode, source: str) -> None:
        components[name].notices.append(MatchExplanationItemOut(code=code, source=source))

    def unknown(name: str, values: list[str], code: MatchExplanationCode, source: str) -> None:
        components[name].unknown_items.extend(
            MatchExplanationItemOut(code=code, source=source, value=value) for value in values
        )

    evidence_available = job.ai_enrichment_status == "success"
    for name, requirements in (
        ("required_skills", job.required_skills),
        ("nice_to_have_skills", job.nice_to_have_skills),
    ):
        if result.components[name].score is not None:
            continue
        if not requirements:
            notice(name, "job_skills_missing", "job")
        if not profile.skills:
            notice(name, "profile_skills_missing", "profile")
        if not evidence_available:
            notice(name, "job_skills_unavailable", "job")
            unknown(name, requirements, "job_skills_unavailable", "job")
        elif requirements and not profile.skills:
            unknown(name, requirements, "profile_skills_missing", "profile")

    if not job.language_requirements:
        notice("languages", "job_languages_missing", "job")
    if not evidence_available:
        notice("languages", "job_languages_unavailable", "job")
        unknown("languages", job.language_requirements, "job_languages_unavailable", "job")
    else:
        # Same parser/level lookup and last-entry-wins semantics as _languages_component.
        levels = {
            _canonical_language(str(item.get("language", ""))): _level(str(item.get("level", "")))
            for item in profile.languages
        }
        for requirement in job.language_requirements:
            language, level, raw = _parse_language_requirement(requirement)
            if language is None or level is None:
                unknown("languages", [raw], "language_requirement_unparseable", "job")
            elif language in levels and levels[language] is None:
                unknown("languages", [raw], "profile_language_level_unknown", "profile")

    if result.components["role"].score is None:
        if not job.title:
            notice("role", "job_role_missing", "job")
        if not profile.target_roles:
            notice("role", "profile_roles_missing", "profile")
    if result.components["seniority"].score is None:
        if job.seniority not in _SENIORITY_ORDER:
            notice("seniority", "job_seniority_unknown", "job")
        if profile.experience not in _SENIORITY_ORDER:
            notice("seniority", "profile_seniority_unknown", "profile")
    if result.components["workplace"].score is None:
        notice("workplace", "job_workplace_unknown", "job")
    elif profile.workplace_preference == "any":
        notice("workplace", "workplace_any", "profile")

    if job.workplace_type == "remote":
        notice("location", "remote_location_not_compared", "comparison")
    elif job.workplace_type not in {"onsite", "hybrid"}:
        notice("location", "location_not_compared", "comparison")
    elif result.components["location"].score is None:
        if not job.location:
            notice("location", "job_location_missing", "job")
        if not profile.location:
            notice("location", "profile_locations_missing", "profile")

    if result.components["salary"].score is None:
        if job.salary_min is None and job.salary_max is None:
            notice("salary", "job_salary_missing", "job")
        if profile.salary_min is None:
            notice("salary", "profile_salary_missing", "profile")
        if job.salary_currency is None:
            notice("salary", "job_salary_currency_missing", "job")
        if profile.salary_currency is None:
            notice("salary", "profile_salary_currency_missing", "profile")
        if (job.salary_currency is not None and profile.salary_currency is not None
                and job.salary_currency != profile.salary_currency):
            notice("salary", "salary_currencies_incompatible", "comparison")
        if job.salary_period_inferred:
            notice("salary", "salary_period_inferred", "job")
        if job.salary_period not in {"month", "year"}:
            notice("salary", "job_salary_period_unknown", "job")
        if profile.salary_period not in {"month", "year"}:
            notice("salary", "profile_salary_period_unknown", "profile")
        if not components["salary"].notices:
            notice("salary", "salary_not_comparable", "comparison")
    return MatchExplainabilityOut(components=components)
