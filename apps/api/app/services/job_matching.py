from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from app.models import Application, Job, UserProfile
from app.schemas import MatchComponentOut, MatchInputStateOut, MatchReasonOut, MatchRecommendationOut, MatchResultOut
from app.services.match_aliases import (
    LANGUAGE_ALIASES,
    ROLE_PHRASE_ALIASES,
    ROLE_TOKEN_ALIASES,
    SAFE_ROLE_TRAILING_QUALIFIERS,
    SENIORITY_TOKENS,
    SKILL_ALIASES,
)

ALGORITHM_VERSION = "job-match-v2"
WEIGHTS = {
    "role": 20,
    "required_skills": 30,
    "nice_to_have_skills": 5,
    "seniority": 15,
    "languages": 10,
    "workplace": 5,
    "location": 5,
    "salary": 10,
}
_SENIORITY_ORDER = {"intern": 0, "junior": 1, "middle": 2, "senior": 3, "lead": 4}
_LEVEL_ORDER = {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6, "fluent": 6, "native": 7}


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def canonical_skill(value: str) -> str:
    key = normalize_text(value)
    return SKILL_ALIASES.get(key, key)


def calculate_match(profile: UserProfile, job: Job, application: Application) -> MatchResultOut:
    components: dict[str, MatchComponentOut] = {}
    conflicts: list[MatchReasonOut] = []

    components["role"] = _role_component(profile.target_roles, job.title)

    skills_available = job.ai_enrichment_status == "success"
    components["required_skills"] = _skills_component(profile.skills, job.required_skills, skills_available, "required_skills")
    components["nice_to_have_skills"] = _skills_component(profile.skills, job.nice_to_have_skills, skills_available, "nice_to_have_skills")

    components["seniority"] = _seniority_component(profile.experience, job.seniority)
    components["languages"] = _languages_component(profile.languages, job.language_requirements, skills_available)
    components["workplace"] = _workplace_component(profile.workplace_preference, job.workplace_type)
    components["location"] = _location_component(profile.location, job.location, job.workplace_type)
    components["salary"], salary_conflict = _salary_component(profile, job)
    if salary_conflict:
        conflicts.append(MatchReasonOut(code="salary_below_minimum", component="salary"))

    evaluated_weight = sum(component.weight for component in components.values() if component.score is not None)
    coverage = evaluated_weight
    core_unavailable = components["role"].score is None and components["required_skills"].score is None
    if coverage < 40 or core_unavailable:
        score: int | None = None
        verdict = "insufficient_data"
    else:
        weighted = sum(component.weight * component.score / 100 for component in components.values() if component.score is not None)
        score = _round_to_five(weighted * 100 / evaluated_weight)
        verdict = "high" if score >= 75 else "medium" if score >= 50 else "low"

    strengths, gaps, unknowns = _explanation_reasons(profile, job, components, skills_available)
    recommendation = _recommendation(verdict, conflicts, gaps, unknowns)

    return MatchResultOut(
        algorithm_version=ALGORITHM_VERSION,
        application_id=application.id,
        job_id=job.id,
        score=score,
        verdict=verdict,
        coverage=coverage,
        input_state=MatchInputStateOut(
            profile_updated_at=profile.updated_at,
            job_updated_at=job.updated_at,
            parsing_status=job.parsing_status,
            ai_enrichment_status=job.ai_enrichment_status,
        ),
        components=components,
        strengths=strengths,
        gaps=gaps,
        unknowns=unknowns,
        conflicts=conflicts,
        recommendation=recommendation,
    )


def _component(name: str, score: int | None, status: str, matched: list[str] | None = None, missing: list[str] | None = None) -> MatchComponentOut:
    return MatchComponentOut(weight=WEIGHTS[name], score=score, status=status, matched=matched or [], missing=missing or [])


def _role_component(roles: list[str], title: str | None) -> MatchComponentOut:
    if not title or not roles:
        return _component("role", None, "unknown")
    title_key, title_tokens = _role_key(title)
    partial_role: str | None = None
    for role in roles:
        role_key, role_tokens = _role_key(role)
        if title_key == role_key:
            return _component("role", 100, "matched", [role])
        shared = title_tokens & role_tokens
        if len(shared) >= 2 and (shared == title_tokens or shared == role_tokens):
            return _component("role", 100, "matched", [role])
        if len(shared) >= 2 and partial_role is None:
            partial_role = role
    if partial_role is not None:
        return _component("role", 50, "partial", [partial_role])
    return _component("role", 0, "mismatch", missing=roles)


def _role_key(value: str) -> tuple[str, set[str]]:
    normalized = normalize_text(value)
    raw_tokens = re.findall(r"[\w+#.]+", normalized)
    raw_tokens = [token for token in raw_tokens if token not in SENIORITY_TOKENS]
    raw_phrase = " ".join(raw_tokens)
    alias = ROLE_PHRASE_ALIASES.get(raw_phrase)
    phrase = alias or raw_phrase
    if alias is None:
        base_phrase = _safe_trailing_qualifier_base_phrase(normalized)
        if base_phrase is not None:
            phrase = ROLE_PHRASE_ALIASES.get(base_phrase, phrase)
    tokens = [_canonical_role_token(token) for token in phrase.split()]
    return " ".join(tokens), set(tokens)


def _safe_trailing_qualifier_base_phrase(value: str) -> str | None:
    match = re.fullmatch(r"(.+?)\s*\(([^()]*)\)", value)
    if match is None:
        return None
    qualifiers = [normalize_text(item) for item in match.group(2).split(",")]
    if not qualifiers or any(not _is_safe_trailing_qualifier(item) for item in qualifiers):
        return None
    base_tokens = re.findall(r"[\w+#.]+", match.group(1))
    base_tokens = [token for token in base_tokens if token not in SENIORITY_TOKENS]
    return " ".join(base_tokens)


def _is_safe_trailing_qualifier(value: str) -> bool:
    return value in SAFE_ROLE_TRAILING_QUALIFIERS or value.removesuffix("+") in SAFE_ROLE_TRAILING_QUALIFIERS


def _canonical_role_token(token: str) -> str:
    alias = ROLE_TOKEN_ALIASES.get(token)
    return alias if alias is not None else token


def _skills_component(user_skills: list[str], job_skills: list[str], available: bool, name: str) -> MatchComponentOut:
    if not available or not job_skills or not user_skills:
        return _component(name, None, "unknown")
    profile_by_key = {canonical_skill(skill): skill for skill in user_skills}
    matched: list[str] = []
    missing: list[str] = []
    for skill in job_skills:
        if canonical_skill(skill) in profile_by_key:
            matched.append(skill)
        else:
            missing.append(skill)
    score = _round_percent(len(matched), len(job_skills))
    status = "matched" if not missing else "partial" if matched else "mismatch"
    return _component(name, score, status, matched, missing)


def _seniority_component(user: str, required: str) -> MatchComponentOut:
    if user not in _SENIORITY_ORDER or required not in _SENIORITY_ORDER:
        return _component("seniority", None, "unknown")
    difference = _SENIORITY_ORDER[user] - _SENIORITY_ORDER[required]
    if difference >= 0:
        return _component("seniority", 100, "matched", [required])
    if difference == -1:
        return _component("seniority", 50, "partial", missing=[required])
    return _component("seniority", 0, "mismatch", missing=[required])


def _languages_component(profile_languages: list[dict[str, str]], requirements: list[str], available: bool) -> MatchComponentOut:
    if not available or not requirements:
        return _component("languages", None, "unknown")
    profile = {_canonical_language(str(item.get("language", ""))): _level(str(item.get("level", ""))) for item in profile_languages}
    usable: list[tuple[str, int, str]] = []
    for item in requirements:
        language, level, raw = _parse_language_requirement(item)
        if language is not None and level is not None:
            usable.append((language, level, raw))
    if not usable:
        return _component("languages", None, "unknown")
    matched: list[str] = []
    missing: list[str] = []
    ratios: list[int] = []
    for language, level, raw in usable:
        if language not in profile:
            missing.append(raw)
            ratios.append(0)
            continue
        candidate_level = profile[language]
        if candidate_level is None:
            continue
        if candidate_level >= level:
            matched.append(raw)
            ratios.append(100)
        else:
            missing.append(raw)
            ratios.append(0)
    if not ratios:
        return _component("languages", None, "unknown")
    score = _round_to_five(sum(ratios) / len(ratios))
    return _component("languages", score, "matched" if not missing else "partial" if matched else "mismatch", matched, missing)


def _canonical_language(value: str) -> str:
    key = normalize_text(value)
    return LANGUAGE_ALIASES.get(key, key)


def _parse_language_requirement(value: str) -> tuple[str | None, int | None, str]:
    normalized = normalize_text(value)
    match = re.fullmatch(r"(.+?)\s+(a[1-2]|b[1-2]|c[1-2]|native|fluent)", normalized)
    if match is None:
        return None, None, value
    return _canonical_language(match.group(1)), _LEVEL_ORDER[match.group(2)], value


def _level(value: str) -> int | None:
    return _LEVEL_ORDER.get(normalize_text(value))


def _workplace_component(preference: str, workplace: str) -> MatchComponentOut:
    if workplace == "unknown":
        return _component("workplace", None, "unknown")
    if preference == "any" or preference == workplace:
        return _component("workplace", 100, "matched", [workplace])
    return _component("workplace", 0, "mismatch", missing=[workplace])


def _location_component(locations: list[str], job_location: str | None, workplace: str) -> MatchComponentOut:
    if workplace not in {"onsite", "hybrid"} or not job_location or not locations:
        return _component("location", None, "unknown")
    if normalize_text(job_location) in {normalize_text(location) for location in locations}:
        return _component("location", 100, "matched", [job_location])
    return _component("location", 0, "mismatch", missing=[job_location])


def _salary_component(profile: UserProfile, job: Job) -> tuple[MatchComponentOut, bool]:
    if job.salary_period_inferred:
        return _component("salary", None, "unknown"), False
    if (profile.salary_min is None or profile.salary_currency is None or profile.salary_period not in {"month", "year"} or job.salary_currency is None or profile.salary_currency != job.salary_currency or job.salary_period not in {"month", "year"}):
        return _component("salary", None, "unknown"), False
    desired = _annual_amount(profile.salary_min, profile.salary_period)
    minimum = _annual_amount(job.salary_min, job.salary_period) if job.salary_min is not None else None
    maximum = _annual_amount(job.salary_max, job.salary_period) if job.salary_max is not None else None
    if maximum is not None and maximum < desired:
        return _component("salary", 0, "mismatch"), True
    if minimum is not None and minimum >= desired:
        return _component("salary", 100, "matched"), False
    if maximum is not None and maximum >= desired:
        return _component("salary", 50, "partial"), False
    return _component("salary", None, "unknown"), False


def _annual_amount(amount: Decimal, period: str) -> Decimal:
    return amount * 12 if period == "month" else amount


def _explanation_reasons(
    profile: UserProfile, job: Job, components: dict[str, MatchComponentOut], skills_available: bool
) -> tuple[list[MatchReasonOut], list[MatchReasonOut], list[MatchReasonOut]]:
    strengths: list[MatchReasonOut] = []
    gaps: list[MatchReasonOut] = []
    unknowns: list[MatchReasonOut] = []

    role = components["role"]
    if role.status == "matched":
        strengths.extend(MatchReasonOut(code="role_matched", component="role", value=value) for value in role.matched)
    elif role.status == "partial":
        gaps.extend(MatchReasonOut(code="role_partial", component="role", value=value) for value in role.matched)
    elif role.status == "mismatch":
        gaps.extend(MatchReasonOut(code="role_not_matched", component="role", value=value) for value in role.missing)
    elif job.title is None:
        unknowns.append(MatchReasonOut(code="vacancy_role_unknown", component="role"))
    else:
        unknowns.append(MatchReasonOut(code="profile_target_roles_missing", component="role"))

    for component, match_code, gap_code in (
        ("required_skills", "required_skill_listed", "required_skill_not_listed"),
        ("nice_to_have_skills", "nice_to_have_skill_listed", "nice_to_have_skill_not_listed"),
    ):
        value = components[component]
        strengths.extend(MatchReasonOut(code=match_code, component=component, value=item) for item in value.matched)
        gaps.extend(MatchReasonOut(code=gap_code, component=component, value=item) for item in value.missing)
    if not skills_available and (job.required_skills or job.nice_to_have_skills):
        unknowns.append(MatchReasonOut(code="vacancy_skills_unavailable", component="required_skills"))
    elif job.required_skills and not profile.skills:
        unknowns.append(MatchReasonOut(code="profile_skills_missing", component="required_skills"))

    seniority = components["seniority"]
    if seniority.status == "matched":
        strengths.extend(MatchReasonOut(code="seniority_matches", component="seniority", value=item) for item in seniority.matched)
    elif seniority.status in {"partial", "mismatch"}:
        gaps.extend(MatchReasonOut(code="seniority_below_requirement", component="seniority", value=item) for item in seniority.missing)
    elif job.seniority == "unknown":
        unknowns.append(MatchReasonOut(code="vacancy_seniority_unknown", component="seniority"))
    else:
        unknowns.append(MatchReasonOut(code="profile_seniority_unknown", component="seniority"))

    languages = components["languages"]
    strengths.extend(MatchReasonOut(code="language_level_sufficient", component="languages", value=item) for item in languages.matched)
    if skills_available and job.language_requirements:
        parsed = [_parse_language_requirement(item) for item in job.language_requirements]
        usable = [(language, level, raw) for language, level, raw in parsed if language is not None and level is not None]
        if not usable:
            unknowns.append(MatchReasonOut(code="language_requirements_unparseable", component="languages"))
        else:
            levels = {_canonical_language(str(item.get("language", ""))): _level(str(item.get("level", ""))) for item in profile.languages}
            for language, level, raw in usable:
                actual = levels.get(language)
                if actual is None:
                    gaps.append(MatchReasonOut(code="language_not_listed", component="languages", value=raw))
                elif actual < level:
                    gaps.append(MatchReasonOut(code="language_level_below_requirement", component="languages", value=raw))
    elif not skills_available and job.language_requirements:
        unknowns.append(MatchReasonOut(code="vacancy_language_requirements_unavailable", component="languages"))

    workplace = components["workplace"]
    if workplace.status == "matched":
        strengths.append(MatchReasonOut(code="workplace_matches", component="workplace"))
    elif workplace.status == "mismatch":
        gaps.extend(MatchReasonOut(code="workplace_not_preferred", component="workplace", value=item) for item in workplace.missing)
    else:
        unknowns.append(MatchReasonOut(code="vacancy_workplace_unknown", component="workplace"))

    location = components["location"]
    if location.status == "matched":
        strengths.extend(MatchReasonOut(code="location_matches", component="location", value=item) for item in location.matched)
    elif location.status == "mismatch":
        gaps.extend(MatchReasonOut(code="location_not_listed", component="location", value=item) for item in location.missing)
    elif job.workplace_type in {"onsite", "hybrid"}:
        unknowns.append(MatchReasonOut(code="vacancy_location_missing" if not job.location else "profile_locations_missing", component="location"))

    salary = components["salary"]
    if salary.status == "matched":
        strengths.append(MatchReasonOut(code="salary_meets_expectations", component="salary"))
    elif salary.status == "unknown":
        unknowns.append(_salary_unknown_reason(profile, job))
    return strengths, gaps, unknowns


def _salary_unknown_reason(profile: UserProfile, job: Job) -> MatchReasonOut:
    if job.salary_min is None and job.salary_max is None:
        return MatchReasonOut(code="vacancy_salary_missing", component="salary")
    if profile.salary_min is None:
        return MatchReasonOut(code="profile_salary_missing", component="salary")
    if job.salary_period_inferred:
        return MatchReasonOut(code="salary_period_inferred", component="salary")
    return MatchReasonOut(code="salary_not_comparable", component="salary")


def _recommendation(
    verdict: str, conflicts: list[MatchReasonOut], gaps: list[MatchReasonOut], unknowns: list[MatchReasonOut]
) -> MatchRecommendationOut:
    if verdict == "insufficient_data":
        return MatchRecommendationOut(code="insufficient_data", primary_reason=unknowns[0] if unknowns else None)
    if verdict == "low":
        return MatchRecommendationOut(code="unlikely_fit", primary_reason=conflicts[0] if conflicts else gaps[0] if gaps else None)
    if verdict == "medium" or conflicts:
        return MatchRecommendationOut(code="apply_with_risks", primary_reason=conflicts[0] if conflicts else gaps[0] if gaps else None)
    return MatchRecommendationOut(code="apply", primary_reason=None)


def _round_percent(numerator: int, denominator: int) -> int:
    return _round_to_five(numerator * 100 / denominator)


def _round_to_five(value: float) -> int:
    return int(5 * round(value / 5))
