from dataclasses import asdict
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from openai import APIError, APIResponseValidationError, APITimeoutError
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import ValidationError

from app.models import Job
from app.services.applications import _apply_ai_enrichment, _validated_salary_supplement, save_application_for_user
import app.services.job_ai_enrichment as job_ai_enrichment
from app.services.job_ai_enrichment import (
    MAX_OUTPUT_TOKENS,
    REASONING_EFFORT,
    AIEnrichmentResult,
    AIEnrichmentTransportResult,
    JobAIEnrichmentService,
    VacancyAIInput,
    convert_transport_enrichment_result,
)
from app.services.job_normalizer import ExtractedJobData, normalize_job
from app.services.job_posting_extractor import JobPostingExtractor
from app.services.vacancy_enrichment import VacancyEnrichmentService
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


def transport_result(**changes) -> AIEnrichmentTransportResult:
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
    return AIEnrichmentTransportResult.model_validate(values)


def test_service_uses_structured_response_and_normalizes_duplicates() -> None:
    parsed = transport_result(required_skills=[" Python ", "python", "SQL"], responsibilities=[" Build APIs\n", "Build APIs"])

    class Responses:
        def parse(self, **kwargs):
            assert kwargs["model"] == "test-model"
            assert kwargs["text_format"] is AIEnrichmentTransportResult
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


def test_transport_salary_schema_contains_only_string_or_null() -> None:
    schema = type_to_text_format_param(AIEnrichmentTransportResult)["schema"]

    for field in ("salary_min", "salary_max"):
        branches = schema["properties"][field]["anyOf"]
        assert {branch.get("type") for branch in branches} == {"string", "null"}
        assert all("pattern" not in branch for branch in branches)
        assert all(branch.get("type") != "number" for branch in branches)


@pytest.mark.parametrize(
    ("salary_min", "salary_max", "expected_min", "expected_max"),
    [
        ("150000", "150000.00", Decimal("150000"), Decimal("150000.00")),
        (None, None, None, None),
    ],
)
def test_transport_salary_strings_convert_to_exact_domain_decimals(
    salary_min: str | None,
    salary_max: str | None,
    expected_min: Decimal | None,
    expected_max: Decimal | None,
) -> None:
    converted = convert_transport_enrichment_result(
        transport_result(salary_min=salary_min, salary_max=salary_max)
    )

    assert converted.salary_min == expected_min
    assert converted.salary_max == expected_max


@pytest.mark.parametrize("salary_min", ["-1", "one hundred", "150000.001", "123456789012345"])
def test_invalid_transport_salary_is_graceful_invalid_output(salary_min: str) -> None:
    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=transport_result(salary_min=salary_min))

    service = JobAIEnrichmentService(model="test-model", client=SimpleNamespace(responses=Responses()))
    output, error = service.enrich(
        VacancyAIInput.model_validate({"description": "Need Python", "workplace_type": "unknown", "employment_type": "unknown"})
    )

    assert output is None
    assert error == "invalid_output"


def test_service_converts_transport_salary_before_returning_domain_result() -> None:
    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(
                output_parsed=transport_result(
                    salary_min="150000.00",
                    salary_max="200000",
                    salary_currency="usd",
                    salary_period="month",
                    salary_period_evidence="explicit",
                )
            )

    service = JobAIEnrichmentService(model="test-model", client=SimpleNamespace(responses=Responses()))
    output, error = service.enrich(
        VacancyAIInput.model_validate({"description": "Need Python", "workplace_type": "unknown", "employment_type": "unknown"})
    )

    assert error is None
    assert output is not None
    assert output.salary_min == Decimal("150000.00")
    assert output.salary_max == Decimal("200000")
    assert output.salary_currency == "USD"
    assert output.salary_period.value == "month"


def test_vacancy_ai_output_budget_defaults_to_1536_tokens() -> None:
    result = _load_vacancy_output_budget()

    assert result.returncode == 0
    assert result.stdout.strip() == "1536"


def test_vacancy_ai_output_budget_reads_explicit_environment_override() -> None:
    result = _load_vacancy_output_budget({"VACANCY_AI_MAX_OUTPUT_TOKENS": "4096"})

    assert result.returncode == 0
    assert result.stdout.strip() == "4096"


def test_vacancy_ai_output_budget_rejects_non_positive_configuration() -> None:
    result = _load_vacancy_output_budget({"VACANCY_AI_MAX_OUTPUT_TOKENS": "0"})

    assert result.returncode != 0
    assert "VACANCY_AI_MAX_OUTPUT_TOKENS must be positive" in result.stderr


def _load_vacancy_output_budget(
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("VACANCY_AI_MAX_OUTPUT_TOKENS", None)
    environment["PYTHONPATH"] = "."
    environment.update(overrides or {})
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.config import VACANCY_AI_MAX_OUTPUT_TOKENS; print(VACANCY_AI_MAX_OUTPUT_TOKENS)",
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_service_disables_sdk_automatic_retries(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(job_ai_enrichment, "OpenAI", FakeOpenAI)
    JobAIEnrichmentService(api_key="test-key", timeout_seconds=15)

    assert captured == {"api_key": "test-key", "timeout": 15, "max_retries": 0}


def test_ai_parse_error_taxonomy_and_safe_telemetry(caplog: pytest.LogCaptureFixture) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(502, request=request, headers={"x-request-id": "req_safe"})
    with pytest.raises(ValidationError) as validation_error:
        AIEnrichmentResult.model_validate({})
    cases: list[tuple[Exception, str]] = [
        (APITimeoutError(request=request), "timeout"),
        (APIResponseValidationError(response=response, body={"unsafe": "provider payload"}), "invalid_output"),
        (validation_error.value, "invalid_output"),
        (APIError("unsafe provider payload", request=request, body={"unsafe": "provider payload"}), "provider_error"),
        (RuntimeError("unsafe provider payload"), "processing_failed"),
    ]

    for error, expected in cases:
        class Responses:
            def parse(self, **kwargs):
                raise error

        service = JobAIEnrichmentService(model="safe-test-model", client=SimpleNamespace(responses=Responses()))
        caplog.clear()
        caplog.set_level(logging.INFO, logger=job_ai_enrichment.__name__)
        output, code = service.enrich(
            VacancyAIInput.model_validate(
                {
                    "title": "private title",
                    "description": "private vacancy text",
                    "workplace_type": "unknown",
                    "employment_type": "unknown",
                }
            )
        )

        assert output is None
        assert code == expected
        record = next(record for record in caplog.records if "AI enrichment parse" in record.getMessage())
        message = record.getMessage()
        assert "model=safe-test-model" in message
        duration = re.search(r"duration_seconds=(\d+\.\d+)", message)
        assert duration is not None and float(duration.group(1)) >= 0
        assert f"exception_class={type(error).__name__}" in message
        assert "private vacancy text" not in message
        assert "private title" not in message
        assert "unsafe provider payload" not in message
        assert "<vacancy_data>" not in message
        if isinstance(error, APIResponseValidationError):
            assert "request_id=req_safe" in message
            assert "status_code=502" in message
        else:
            assert "request_id=" not in message
            assert "status_code=" not in message


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        (SimpleNamespace(output_parsed=None, status="completed", output=[], _request_id="req_success"), "parsed_missing"),
        (SimpleNamespace(output_parsed=SimpleNamespace(required_skills=["Python"]), status="completed", output=[], _request_id="req_success"), "parsed_wrong_type"),
        (
            SimpleNamespace(
                output_parsed=None,
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                output=[],
                usage=SimpleNamespace(
                    input_tokens=123,
                    output_tokens=1536,
                    output_tokens_details=SimpleNamespace(
                        reasoning_tokens=1400, model_fields_set={"reasoning_tokens"}
                    ),
                    input_tokens_details=SimpleNamespace(
                        cached_tokens=50, model_fields_set={"cached_tokens"}
                    ),
                    model_fields_set={
                        "input_tokens",
                        "output_tokens",
                        "input_tokens_details",
                        "output_tokens_details",
                    },
                ),
                _request_id="req_success",
            ),
            "incomplete",
        ),
        (SimpleNamespace(output_parsed=None, status="completed", output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal", refusal="private refusal text")])], _request_id="req_success"), "refusal"),
    ],
)
def test_ai_invalid_parsed_output_is_classified_and_logged_safely(
    response: object, expected_reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    class Responses:
        def parse(self, **kwargs):
            return response

    service = JobAIEnrichmentService(model="safe-test-model", client=SimpleNamespace(responses=Responses()))
    caplog.set_level(logging.INFO, logger=job_ai_enrichment.__name__)
    output, code = service.enrich(
        VacancyAIInput.model_validate({"description": "private vacancy text", "workplace_type": "unknown", "employment_type": "unknown"})
    )

    assert output is None
    assert code == "invalid_output"
    message = next(record.getMessage() for record in caplog.records if "AI enrichment parse" in record.getMessage())
    assert "result=error" in message
    assert "error_code=invalid_output" in message
    assert "request_id=req_success" in message
    assert f"response_reason={expected_reason}" in message
    assert "response_status=" in message
    assert "has_output_parsed=" in message
    assert "has_refusal=" in message
    assert "output_item_types=" in message
    assert "private vacancy text" not in message
    assert "private refusal text" not in message
    if expected_reason == "incomplete":
        assert f"max_output_tokens={MAX_OUTPUT_TOKENS}" in message
        assert "input_tokens=123" in message
        assert "output_tokens=1536" in message
        assert "reasoning_tokens=1400" in message
        assert "cached_input_tokens=50" in message
        assert "input_tokens_field_present=True" in message
        assert "output_tokens_field_present=True" in message
        assert "input_details_present=True" in message
        assert "output_details_present=True" in message
        assert "reasoning_tokens_field_present=True" in message
        assert "cached_tokens_field_present=True" in message


def test_ai_success_telemetry_has_duration_and_available_request_id(caplog: pytest.LogCaptureFixture) -> None:
    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=transport_result(), _request_id="req_success")

    service = JobAIEnrichmentService(model="safe-test-model", client=SimpleNamespace(responses=Responses()))
    caplog.set_level(logging.INFO, logger=job_ai_enrichment.__name__)
    output, code = service.enrich(
        VacancyAIInput.model_validate({"description": "private vacancy text", "workplace_type": "unknown", "employment_type": "unknown"})
    )

    assert output is not None
    assert code is None
    message = next(record.getMessage() for record in caplog.records if "AI enrichment parse" in record.getMessage())
    assert "result=success" in message
    duration = re.search(r"duration_seconds=(\d+\.\d+)", message)
    assert duration is not None and float(duration.group(1)) >= 0
    assert "request_id=req_success" in message
    assert "status_code=" not in message
    assert "private vacancy text" not in message


def test_ai_parse_duration_excludes_post_processing(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = [0.0]

    class Responses:
        def parse(self, **kwargs):
            clock[0] = 1.0
            return SimpleNamespace(output_parsed=transport_result())

    def delayed_validation(parsed: AIEnrichmentResult) -> AIEnrichmentResult:
        clock[0] = 6.0
        return parsed

    monkeypatch.setattr(job_ai_enrichment.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(job_ai_enrichment, "validate_enrichment_result", delayed_validation)
    service = JobAIEnrichmentService(model="safe-test-model", client=SimpleNamespace(responses=Responses()))
    caplog.set_level(logging.INFO, logger=job_ai_enrichment.__name__)
    output, code = service.enrich(
        VacancyAIInput.model_validate({"description": "private vacancy text", "workplace_type": "unknown", "employment_type": "unknown"})
    )

    assert output is not None
    assert code is None
    message = next(record.getMessage() for record in caplog.records if "AI enrichment parse" in record.getMessage())
    assert "duration_seconds=1.000" in message


def test_ai_post_processing_validation_failure_is_invalid_output(caplog: pytest.LogCaptureFixture) -> None:
    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=transport_result(salary_min="2000", salary_max="1000"))

    service = JobAIEnrichmentService(model="safe-test-model", client=SimpleNamespace(responses=Responses()))
    caplog.set_level(logging.INFO, logger=job_ai_enrichment.__name__)
    output, code = service.enrich(
        VacancyAIInput.model_validate({"description": "private vacancy text", "workplace_type": "unknown", "employment_type": "unknown"})
    )

    assert output is None
    assert code == "invalid_output"
    message = next(record.getMessage() for record in caplog.records if "AI enrichment parse" in record.getMessage())
    assert "error_code=invalid_output" in message
    assert "exception_class=ValueError" in message
    assert "private vacancy text" not in message


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


def test_ai_supplements_unknown_deterministic_workplace() -> None:
    job = Job(source="company_site", source_url="https://example.com/unknown-workplace", workplace_type="unknown")
    _apply_ai_enrichment(job, result(workplace_type="onsite"))
    assert job.workplace_type == "onsite"


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


def test_new_application_persists_embedded_deterministic_workplace() -> None:
    fixture = (Path(__file__).parent / "fixtures" / "vacancy_enrichment" / "hh_workplace_embedded.html").read_text()

    class DeterministicService:
        def preflight(self, url: str) -> None:
            return None

        def enrich(self, url: str):
            return normalize_job(JobPostingExtractor().extract(fixture)), None

        @staticmethod
        def values(data):
            return asdict(data)

    class DisabledAI:
        configured = False

    with TestSessionLocal() as session:
        from app.models import User

        user = User(telegram_id=504, first_name="Anna")
        session.add(user)
        session.commit()
        job, _, created, _ = save_application_for_user(
            session,
            user.id,
            "https://example.com/embedded-workplace",
            cast(VacancyEnrichmentService, DeterministicService()),
            cast(JobAIEnrichmentService, DisabledAI()),
        )
        assert created is True
        assert job.workplace_type == "onsite"


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
