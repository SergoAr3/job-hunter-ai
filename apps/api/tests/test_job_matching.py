from decimal import Decimal

import pytest

from app.models import Application, Job, UserProfile
from app.services.job_matching import ALGORITHM_VERSION, calculate_match


def profile(**changes: object) -> UserProfile:
    values: dict[str, object] = {
        "user_id": 1,
        "target_roles": ["Python Backend Developer"],
        "skills": ["Python", "PostgreSQL", "JavaScript", "Docker"],
        "experience": "middle",
        "location": ["Yerevan"],
        "workplace_preference": "remote",
        "salary_min": Decimal("3000"),
        "salary_currency": "USD",
        "salary_period": "month",
        "languages": [{"language": "English", "level": "B2"}],
    }
    values.update(changes)
    return UserProfile(**values)


def job(**changes: object) -> Job:
    values: dict[str, object] = {
        "id": 2,
        "source": "company_site",
        "source_url": "https://example.com/job",
        "title": "Senior Python Backend Engineer",
        "required_skills": ["Python", "PostgreSQL"],
        "nice_to_have_skills": ["Docker"],
        "language_requirements": ["English B2"],
        "seniority": "middle",
        "workplace_type": "remote",
        "location": "Berlin",
        "salary_min": Decimal("3000"),
        "salary_max": Decimal("4000"),
        "salary_currency": "USD",
        "salary_period": "month",
        "parsing_status": "success",
        "ai_enrichment_status": "success",
    }
    values.update(changes)
    return Job(**values)


def match(**changes: object):
    current_profile = changes.pop("profile", profile())
    current_job = changes.pop("job", job())
    return calculate_match(current_profile, current_job, Application(id=3, user_id=1, job_id=2))


def test_strong_match_is_deterministic_and_versioned() -> None:
    first = match()
    second = match()

    assert first.algorithm_version == ALGORITHM_VERSION
    assert first.model_dump() == second.model_dump()
    assert first.verdict == "high"
    assert first.score == 100
    assert first.components["role"].status == "matched"


def test_missing_required_skill_is_a_gap() -> None:
    result = match(job=job(required_skills=["Python", "Kubernetes"]))

    assert result.components["required_skills"].score == 50
    assert result.components["required_skills"].missing == ["Kubernetes"]
    assert any(reason.code == "required_skills_missing" for reason in result.gaps)


@pytest.mark.parametrize(("user_skill", "job_skill"), [("Postgres", "PostgreSQL"), ("JS", "JavaScript")])
def test_exact_skill_aliases_match(user_skill: str, job_skill: str) -> None:
    result = match(profile=profile(skills=[user_skill]), job=job(required_skills=[job_skill]))
    assert result.components["required_skills"].status == "matched"


def test_short_skills_do_not_match_by_substring() -> None:
    result = match(profile=profile(skills=["Rust", "Golang", "C++", "Java"]), job=job(required_skills=["R", "Go", "C", "JS"]))
    assert result.components["required_skills"].matched == []
    assert result.components["required_skills"].score == 0


@pytest.mark.parametrize(
    ("user", "required", "score", "status"),
    [("middle", "middle", 100, "matched"), ("senior", "middle", 100, "matched"), ("junior", "middle", 50, "partial"), ("intern", "middle", 0, "mismatch")],
)
def test_seniority_rules(user: str, required: str, score: int, status: str) -> None:
    result = match(profile=profile(experience=user), job=job(seniority=required))
    assert result.components["seniority"].score == score
    assert result.components["seniority"].status == status


def test_language_match_and_mismatch() -> None:
    matched = match()
    mismatched = match(profile=profile(languages=[{"language": "English", "level": "B1"}]))
    absent = match(profile=profile(languages=[{"language": "Russian", "level": "native"}]))
    assert matched.components["languages"].status == "matched"
    assert mismatched.components["languages"].status == "mismatch"
    assert absent.components["languages"].score == 0
    assert absent.components["languages"].status == "mismatch"
    assert absent.components["languages"].missing == ["English B2"]
    assert any(reason.code == "languages_missing" for reason in absent.gaps)


def test_unparseable_language_requirement_is_unknown() -> None:
    result = match(job=job(language_requirements=["English conversational"]))
    assert result.components["languages"].score is None
    assert result.components["languages"].status == "unknown"


@pytest.mark.parametrize("workplace", ["onsite", "remote", "hybrid"])
def test_workplace_any_matches_every_known_format(workplace: str) -> None:
    result = match(profile=profile(workplace_preference="any"), job=job(workplace_type=workplace))
    assert result.components["workplace"].score == 100
    assert result.components["workplace"].status == "matched"
    assert result.components["workplace"].matched == [workplace]
    assert any(reason.code == "workplace_matched" for reason in result.strengths)
    assert not any(reason.component == "workplace" for reason in result.gaps)


def test_workplace_unknown_is_neutral_for_any_preference() -> None:
    result = match(profile=profile(workplace_preference="any"), job=job(workplace_type="unknown"))
    assert result.components["workplace"].score is None
    assert result.components["workplace"].status == "unknown"
    assert result.components["location"].score is None


def test_workplace_any_counts_toward_score_and_coverage() -> None:
    result = match(profile=profile(workplace_preference="any"), job=job(workplace_type="onsite"))
    assert result.components["workplace"].score == 100
    assert result.coverage == 100
    assert result.score == 95


def test_workplace_and_onsite_location_comparison() -> None:
    result = match(job=job(workplace_type="onsite", location="Yerevan"))
    assert result.components["workplace"].status == "mismatch"
    assert result.components["location"].status == "matched"


@pytest.mark.parametrize(
    ("preference", "workplace", "score", "status"),
    [
        ("onsite", "onsite", 100, "matched"),
        ("remote", "remote", 100, "matched"),
        ("hybrid", "hybrid", 100, "matched"),
        ("onsite", "remote", 0, "mismatch"),
        ("onsite", "hybrid", 0, "mismatch"),
    ],
)
def test_workplace_explicit_equal_and_different_formats(
    preference: str, workplace: str, score: int, status: str
) -> None:
    result = match(profile=profile(workplace_preference=preference), job=job(workplace_type=workplace))
    assert result.components["workplace"].score == score
    assert result.components["workplace"].status == status


def test_salary_below_minimum_and_overlap() -> None:
    below = match(job=job(salary_min=Decimal("1000"), salary_max=Decimal("2999")))
    overlap = match(job=job(salary_min=Decimal("2000"), salary_max=Decimal("3500")))
    assert below.components["salary"].score == 0
    assert below.conflicts[0].code == "salary_below_minimum"
    assert overlap.components["salary"].score == 50


def test_inferred_salary_period_is_unknown_without_conflict() -> None:
    inferred = match(job=job(salary_min=Decimal("1000"), salary_max=Decimal("2000"), salary_period_inferred=True))
    explicit = match(job=job(salary_min=Decimal("1000"), salary_max=Decimal("2000"), salary_period_inferred=False))
    assert inferred.components["salary"].score is None
    assert inferred.components["salary"].status == "unknown"
    assert inferred.conflicts == []
    assert explicit.components["salary"].score == 0
    assert explicit.conflicts[0].code == "salary_below_minimum"


def test_partial_role_has_honest_explanation() -> None:
    result = match(profile=profile(target_roles=["Software Data Engineer"]), job=job(title="Software QA Engineer"))
    assert result.components["role"].status == "partial"
    assert not any(reason.code == "role_matched" for reason in result.strengths)
    assert any(reason.code == "role_partial" for reason in result.gaps)


def test_salary_month_year_unknown_and_currency_rules() -> None:
    annual = match(job=job(salary_min=Decimal("36000"), salary_max=Decimal("48000"), salary_period="year"))
    unknown = match(job=job(salary_min=None, salary_max=None))
    currency = match(job=job(salary_currency="EUR"))
    assert annual.components["salary"].status == "matched"
    assert unknown.components["salary"].score is None
    assert currency.components["salary"].score is None


def test_missing_fields_empty_profile_and_partial_enrichment_are_unknown_not_mismatches() -> None:
    result = match(
        profile=profile(skills=[], languages=[], location=[], salary_min=None, salary_currency=None, salary_period="unknown"),
        job=job(ai_enrichment_status="failed", title=None, seniority="unknown", location=None, workplace_type="unknown"),
    )
    assert result.verdict == "insufficient_data"
    assert result.components["required_skills"].score is None
    assert result.components["languages"].score is None


def test_coverage_below_threshold_has_no_score() -> None:
    result = match(
        profile=profile(experience="unknown", workplace_preference="any", salary_min=None, salary_currency=None, salary_period="unknown"),
        job=job(ai_enrichment_status="failed", seniority="unknown", workplace_type="unknown"),
    )
    assert result.coverage == 20
    assert result.score is None
    assert result.verdict == "insufficient_data"


def test_unknown_core_components_override_sufficient_noncore_coverage() -> None:
    result = match(
        profile=profile(workplace_preference="onsite", location=["Yerevan"]),
        job=job(title=None, required_skills=[], workplace_type="onsite", location="Yerevan"),
    )
    assert result.coverage == 50
    assert result.components["role"].score is None
    assert result.components["required_skills"].score is None
    assert result.score is None
    assert result.verdict == "insufficient_data"
