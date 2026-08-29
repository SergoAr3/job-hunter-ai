from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

from app.models import Job
from app.services.applications import _apply_ai_enrichment, _validated_salary_supplement, save_application_for_user
import app.services.job_ai_enrichment as job_ai_enrichment
from app.services.job_ai_enrichment import (
    MAX_OUTPUT_TOKENS,
    REASONING_EFFORT,
    AIEnrichmentResult,
    JobAIEnrichmentService,
    VacancyAIInput,
)
from app.services.job_normalizer import ExtractedJobData, normalize_job
from conftest import TestSessionLocal


def result(**changes) -> AIEnrichmentResult:
    values = {
        "required_skills": ["Python"],
        "nice_to_have_skills": ["Docker"],
        "experience_requirements": ["3+ years of experience"],
        "language_requirements": ["English B2"],
        "responsibilities": ["Build APIs"],
        "seniority": "senior",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": "unknown",
        "salary_period_evidence": "unknown",
        "location": "Yerevan",
        "workplace_type": "remote",
        "employment_type": "full_time",
    }
    values.update(changes)
    return AIEnrichmentResult.model_validate(values)


def test_service_uses_structured_response_and_normalizes_duplicates() -> None:
    parsed = result(required_skills=[" Python ", "python", "SQL"], responsibilities=[" Build APIs\n", "Build APIs"])

    class Responses:
        def parse(self, **kwargs):
            assert kwargs["model"] == "test-model"
            assert kwargs["text_format"] is AIEnrichmentResult
            assert "<vacancy_data>" in kwargs["input"][1]["content"]
            assert kwargs["reasoning"] == {"effort": REASONING_EFFORT}
            assert kwargs["max_output_tokens"] == MAX_OUTPUT_TOKENS
            return SimpleNamespace(output_parsed=parsed)

    service = JobAIEnrichmentService(model="test-model", client=SimpleNamespace(responses=Responses()))
    output, error = service.enrich(
        VacancyAIInput.model_validate({"description": "Need Python", "workplace_type": "unknown", "employment_type": "unknown"})
    )

    assert error is None
    assert output is not None
    assert output.required_skills == ["Python", "SQL"]
    assert output.responsibilities == ["Build APIs"]


def test_service_disables_sdk_automatic_retries(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(job_ai_enrichment, "OpenAI", FakeOpenAI)
    JobAIEnrichmentService(api_key="test-key", timeout_seconds=15)

    assert captured == {"api_key": "test-key", "timeout": 15, "max_retries": 0}


def test_input_requires_semantic_text_and_obeys_limits() -> None:
    empty = SimpleNamespace(description=None, requirements_text=None)
    assert VacancyAIInput.from_job(empty) is None
    job = SimpleNamespace(
        title="title",
        company="company",
        description="x" * 12_000,
        requirements_text="y" * 7_000,
        salary_text=None,
        location=None,
        workplace_type="unknown",
        employment_type="unknown",
    )
    payload = VacancyAIInput.from_job(job)
    assert payload is not None
    assert len(payload.description or "") == 11_000
    assert len(payload.requirements_text or "") == 6_000


def test_ai_does_not_overwrite_deterministic_fields_or_salary_block() -> None:
    job = Job(
        source="company_site",
        source_url="https://example.com/existing",
        location="Moscow",
        workplace_type="onsite",
        employment_type="contract",
        salary_text="from $1000",
        salary_min=Decimal("1000"),
        salary_currency="USD",
        salary_period="month",
    )
    ai_result = result(
        salary_min=Decimal("2000"),
        salary_max=Decimal("3000"),
        salary_currency="EUR",
        salary_period="year",
        salary_period_evidence="explicit",
    )

    _apply_ai_enrichment(job, ai_result)

    assert job.required_skills == ["Python"]
    assert job.location == "Moscow"
    assert job.workplace_type == "onsite"
    assert job.employment_type == "contract"
    assert job.salary_min == Decimal("1000")
    assert job.salary_currency == "USD"
    assert job.salary_period == "month"


def test_salary_supplement_is_atomic_and_requires_currency() -> None:
    job = Job(source="company_site", source_url="https://example.com/no-salary")
    valid = result(
        salary_min=Decimal("1000"),
        salary_max=Decimal("2000"),
        salary_currency="USD",
        salary_period="month",
        salary_period_evidence="explicit",
    )
    assert _validated_salary_supplement(job, valid) == {
        "salary_min": Decimal("1000"),
        "salary_max": Decimal("2000"),
        "salary_currency": "USD",
        "salary_period": "month",
        "salary_period_inferred": False,
    }
    assert _validated_salary_supplement(job, result(salary_min=Decimal("1000"))) is None


def test_salary_supplement_can_complete_compatible_deterministic_block() -> None:
    job = Job(
        source="company_site",
        source_url="https://example.com/partial-salary",
        salary_min=Decimal("1000"),
        salary_currency="USD",
        salary_period="unknown",
    )
    compatible = result(
        salary_min=Decimal("1000"),
        salary_max=Decimal("2000"),
        salary_currency="USD",
        salary_period="year",
        salary_period_evidence="inferred",
    )
    assert _validated_salary_supplement(job, compatible) == {
        "salary_max": Decimal("2000"),
        "salary_period": "year",
        "salary_period_inferred": True,
    }
    conflict = compatible.model_copy(update={"salary_min": Decimal("900")})
    assert _validated_salary_supplement(job, conflict) is None


def test_invalid_ai_currency_rejects_only_salary_supplement() -> None:
    job = Job(source="company_site", source_url="https://example.com/invalid-currency")
    ai_result = result(
        salary_min=Decimal("1000"),
        salary_currency="ABC",
        salary_period="month",
        salary_period_evidence="explicit",
    )

    _apply_ai_enrichment(job, ai_result)

    assert job.required_skills == ["Python"]
    assert job.seniority == "senior"
    assert job.salary_min is None
    assert job.salary_currency is None


def test_out_of_taxonomy_seniority_is_forced_to_unknown() -> None:
    for title, ai_seniority in (
        ("Staff Engineer", "lead"),
        ("Principal Engineer", "senior"),
        ("Head of Platform", "lead"),
        ("Engineering Director", "senior"),
        ("Директор по разработке", "senior"),
        ("Руководитель платформы", "lead"),
        ("Главный инженер", "senior"),
    ):
        job = Job(source="company_site", source_url=f"https://example.com/{title.replace(' ', '-')}", title=title)
        _apply_ai_enrichment(job, result(seniority=ai_seniority))
        assert job.seniority == "unknown"


def test_in_taxonomy_seniority_is_preserved() -> None:
    for title, seniority in (
        ("Intern QA", "intern"),
        ("Junior Developer", "junior"),
        ("Mid Python Engineer", "middle"),
        ("Senior Engineer", "senior"),
        ("Tech Lead", "lead"),
        ("Middle Python-разработчик", "middle"),
        ("Senior Python-разработчик", "senior"),
        ("Тимлид платформы", "lead"),
    ):
        job = Job(source="company_site", source_url=f"https://example.com/{title.replace(' ', '-')}", title=title)
        _apply_ai_enrichment(job, result(seniority=seniority))
        assert job.seniority == seniority


def test_ai_runs_after_database_commit_and_failure_keeps_job() -> None:
    class DeterministicService:
        def preflight(self, url: str) -> None:
            return None

        def enrich(self, url: str):
            return normalize_job(ExtractedJobData(description="Required Python")), None

        @staticmethod
        def values(data):
            return asdict(data)

    class SuccessfulAI:
        configured = True

        def enrich(self, vacancy):
            assert session.in_transaction() is False
            return result(), None

    with TestSessionLocal() as session:
        from app.models import User

        user = User(telegram_id=501, first_name="Anna")
        session.add(user)
        session.commit()
        job, application, created, application_created = save_application_for_user(
            session, user.id, "https://example.com/ai", DeterministicService(), SuccessfulAI()
        )
        assert created is True and application_created is True
        assert application.id is not None
        assert job.ai_enrichment_status == "success"
        assert job.required_skills == ["Python"]


def test_ai_failure_is_independent_from_saved_job_and_deterministic_data() -> None:
    class DeterministicService:
        def preflight(self, url: str) -> None:
            return None

        def enrich(self, url: str):
            return normalize_job(ExtractedJobData(description="Required Python")), None

        @staticmethod
        def values(data):
            return asdict(data)

    class FailingAI:
        configured = True

        def enrich(self, vacancy):
            return None, "timeout"

    with TestSessionLocal() as session:
        from app.models import User

        user = User(telegram_id=502, first_name="Anna")
        session.add(user)
        session.commit()
        job, application, _, _ = save_application_for_user(
            session, user.id, "https://example.com/ai-failure", DeterministicService(), FailingAI()
        )
        assert application.id is not None
        assert job.description == "Required Python"
        assert job.parsing_status == "partial"
        assert job.ai_enrichment_status == "failed"
        assert job.ai_enrichment_error == "timeout"


def test_existing_job_does_not_call_ai_again() -> None:
    class DeterministicService:
        def preflight(self, url: str) -> None:
            return None

        def enrich(self, url: str):
            return normalize_job(ExtractedJobData(description="Required Python")), None

        @staticmethod
        def values(data):
            return asdict(data)

    class CountingAI:
        configured = True
        calls = 0

        def enrich(self, vacancy):
            self.calls += 1
            return result(), None

    with TestSessionLocal() as session:
        from app.models import User

        user = User(telegram_id=503, first_name="Anna")
        session.add(user)
        session.commit()
        ai = CountingAI()
        save_application_for_user(session, user.id, "https://example.com/repeated", DeterministicService(), ai)
        _, _, created, _ = save_application_for_user(
            session, user.id, "https://example.com/repeated", DeterministicService(), ai
        )
        assert created is False
        assert ai.calls == 1


def test_manual_fixture_inputs_are_stable_and_valid() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "ai_enrichment" / "cases.json"
    cases = json.loads(fixture_path.read_text())
    assert {"internship/junior", "senior", "remote", "hybrid", "onsite", "explicit_salary_period", "salary_without_period", "no_salary", "russian", "english", "prompt_injection"}.issubset(
        {tag for case in cases for tag in case["tags"]}
    )
    for case in cases:
        assert VacancyAIInput.model_validate(case["input"])
