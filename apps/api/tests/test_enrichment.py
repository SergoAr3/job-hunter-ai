from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.job_normalizer import ExtractedJobData, normalize_job
from app.services.job_posting_extractor import JobPostingExtractor
from app.services.job_sources import detect_job_source
from app.services.safe_http_fetcher import BlockedUrlError, FetchError, FetchTimeoutError, ResponseTooLargeError, SafeHttpFetcher, _resolve_public_ips, _validate_url
from app.schemas import JobOut


def test_detects_known_sources_and_keeps_indeed_as_company_site() -> None:
    assert detect_job_source("https://jobs.lever.co/acme/1") == "lever"
    assert detect_job_source("https://www.linkedin.com/jobs/view/1") == "linkedin"
    assert detect_job_source("https://indeed.com/viewjob?jk=1") == "company_site"


def test_normalizer_does_not_infer_salary_period() -> None:
    result = normalize_job(ExtractedJobData(title="Engineer", salary_min=Decimal("3000"), salary_max=Decimal("4000")))
    assert result.salary_period == "unknown"
    assert result.salary_period_inferred is False
    assert result.parsing_status == "partial"


def test_extracts_json_ld_and_ignores_malformed_script() -> None:
    html = '''<script type="application/ld+json">{bad}</script><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Backend Engineer","hiringOrganization":{"name":"Acme"},"description":"<p>Build APIs</p>","qualifications":"Python","employmentType":"FULL_TIME","jobLocationType":"TELECOMMUTE","jobLocation":{"address":{"addressLocality":"Yerevan","addressCountry":"AM"}},"baseSalary":{"currency":"USD","value":{"minValue":3000,"maxValue":4000,"unitText":"MONTH"}}}</script>'''
    raw = JobPostingExtractor().extract(html)
    result = normalize_job(raw)
    assert result.title == "Backend Engineer"
    assert result.company == "Acme"
    assert result.salary_period == "month"
    assert result.workplace_type == "remote"
    assert result.parsing_status == "success"


def test_extracts_embedded_vacancy_workplace_fixture() -> None:
    html = (Path(__file__).parent / "fixtures" / "vacancy_enrichment" / "hh_workplace_embedded.html").read_text()
    raw = JobPostingExtractor().extract(html)
    assert raw.workplace_raw == "ON_SITE"
    assert normalize_job(raw).workplace_type == "onsite"


def test_extracts_embedded_json_vacancy_workplace() -> None:
    html = '<script type="application/json">{"vacancy":{"name":"Engineer","description":"Build APIs","workFormats":["ON_SITE"]}}</script>'
    raw = JobPostingExtractor().extract(html)
    assert raw.workplace_raw == "ON_SITE"
    assert normalize_job(raw).workplace_type == "onsite"


def test_ignores_embedded_workplace_without_vacancy_context() -> None:
    html = '<script type="application/json">{"translations":{"workFormats":["ON_SITE"],"name":"work format"}}</script>'
    assert JobPostingExtractor().extract(html).workplace_raw is None


def test_ignores_embedded_workplace_from_ui_config() -> None:
    html = '<script type="application/json">{"ui_config":{"name":"Vacancy filters","employment":{"full_time":true},"workFormats":["ON_SITE"]}}</script>'
    assert JobPostingExtractor().extract(html).workplace_raw is None


def test_embedded_work_formats_skips_malformed_values_for_first_supported_one() -> None:
    html = '<script type="application/json">{"vacancy":{"name":"Engineer","description":"Build APIs","workFormats":[{}, "FIELD_WORK", "ON_SITE"]}}</script>'
    assert JobPostingExtractor().extract(html).workplace_raw == "ON_SITE"


def test_empty_embedded_work_formats_is_ignored() -> None:
    html = '<script type="application/json">{"vacancy":{"name":"Engineer","description":"Build APIs","workFormats":[]}}</script>'
    assert JobPostingExtractor().extract(html).workplace_raw is None


def test_json_ld_known_workplace_has_priority_over_embedded_data() -> None:
    html = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer","description":"Build APIs","jobLocationType":"TELECOMMUTE"}</script><script type="application/json">{"vacancy":{"name":"Engineer","description":"Build APIs","workFormats":["ON_SITE"]}}</script>'''
    assert normalize_job(JobPostingExtractor().extract(html)).workplace_type == "remote"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("onsite", "onsite"), ("on-site", "onsite"), ("on site", "onsite"), ("ON_SITE", "onsite"),
        ("in person", "onsite"), ("на месте работодателя", "onsite"), ("в офисе", "onsite"),
        ("remote", "remote"), ("удалённо", "remote"), ("удаленно", "remote"), ("дистанционно", "remote"),
        ("hybrid", "hybrid"), ("гибрид", "hybrid"), ("гибридный", "hybrid"),
    ],
)
def test_normalizes_structured_workplace_aliases(raw: str, expected: str) -> None:
    assert normalize_job(ExtractedJobData(workplace_raw=raw)).workplace_type == expected


@pytest.mark.parametrize("raw", [None, "", "office", "ON_SITE_UNKNOWN"])
def test_unknown_or_malformed_workplace_is_unknown(raw: str | None) -> None:
    assert normalize_job(ExtractedJobData(workplace_raw=raw)).workplace_type == "unknown"


def test_description_office_mention_is_not_workplace_evidence() -> None:
    html = '<meta name="description" content="Наш офис находится рядом с метро.">'
    raw = JobPostingExtractor().extract(html)
    assert raw.workplace_raw is None
    assert normalize_job(raw).workplace_type == "unknown"


def test_fetcher_rejects_private_resolved_address() -> None:
    def private_resolver(hostname: str, port: int):
        raise BlockedUrlError("Non-public address")

    fetcher = SafeHttpFetcher(resolver=private_resolver)
    try:
        fetcher.fetch("https://example.com/job")
    except BlockedUrlError:
        pass
    else:
        raise AssertionError("private address must be rejected")


@pytest.mark.parametrize("url", ["http://user@example.com/", "http://example.com:8080/"])
def test_fetcher_rejects_unsafe_url_forms(url: str) -> None:
    with pytest.raises(BlockedUrlError):
        _validate_url(url)


@pytest.mark.parametrize("address", ["10.0.0.1", "169.254.1.1", "::1", "fe80::1"])
def test_resolver_rejects_non_public_addresses(monkeypatch, address: str) -> None:
    monkeypatch.setattr("app.services.safe_http_fetcher.socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(BlockedUrlError):
        _resolve_public_ips("example.com", 443)


def test_resolver_rejects_mixed_public_and_private_addresses(monkeypatch) -> None:
    monkeypatch.setattr("app.services.safe_http_fetcher.socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(BlockedUrlError):
        _resolve_public_ips("example.com", 443)


def test_fetcher_passes_verified_ip_to_connection(monkeypatch) -> None:
    fetcher = SafeHttpFetcher(resolver=lambda host, port: [(2, "8.8.8.8")])
    seen = {}
    class Sock:
        def close(self): pass
    response = SimpleNamespace(status=200, getheader=lambda name: "text/html" if name == "Content-Type" else None, headers=SimpleNamespace(get_content_charset=lambda: "utf-8"), read=lambda size: b"")
    def request(parsed, address, deadline):
        seen["address"] = address
        return response, Sock()
    monkeypatch.setattr(fetcher, "_request", request)
    fetcher.fetch("https://example.com/job")
    assert seen["address"] == (2, "8.8.8.8")


def test_fetcher_enforces_total_deadline(monkeypatch) -> None:
    now = [0.0]
    fetcher = SafeHttpFetcher(resolver=lambda host, port: [(2, "8.8.8.8")], deadline_seconds=1, clock=lambda: now[0])
    class Sock:
        def close(self): pass
    def read(size):
        now[0] = 2.0
        return b"x"
    response = SimpleNamespace(status=200, getheader=lambda name: "text/html" if name == "Content-Type" else None, headers=SimpleNamespace(get_content_charset=lambda: "utf-8"), read=read)
    monkeypatch.setattr(fetcher, "_request", lambda *args: (response, Sock()))
    with pytest.raises(FetchTimeoutError): fetcher.fetch("https://example.com/job")


def test_normalizer_discards_invalid_salary_and_truncates_columns() -> None:
    result = normalize_job(ExtractedJobData(title="x" * 600, company="y" * 600, location="z" * 600, salary_min=Decimal("10"), salary_max=Decimal("5")))
    assert result.salary_min is None and result.salary_max is None
    assert len(result.title or "") == 512
    assert len(result.company or "") == 512
    assert len(result.location or "") == 512


def test_normalizer_discards_negative_salary() -> None:
    result = normalize_job(ExtractedJobData(salary_min=Decimal("-1"), salary_max=Decimal("10")))
    assert result.salary_min is None and result.salary_max is None


def test_job_out_preserves_float_salary_contract_for_decimal_orm_values() -> None:
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id=1,
        source="company_site",
        ingestion_method="manual",
        source_url="https://example.com/job",
        title="Engineer",
        company="Acme",
        description=None,
        requirements_text=None,
        salary_text=None,
        salary_min=Decimal("1000.50"),
        salary_max=Decimal("2000.00"),
        salary_currency="USD",
        salary_period="month",
        salary_period_inferred=False,
        location=None,
        workplace_type="remote",
        employment_type="full_time",
        parsing_status="success",
        parsing_error=None,
        required_skills=[],
        nice_to_have_skills=[],
        experience_requirements=[],
        language_requirements=[],
        responsibilities=[],
        seniority="unknown",
        ai_enrichment_status="not_attempted",
        ai_enrichment_error=None,
        created_at=now,
        updated_at=now,
    )

    result = JobOut.model_validate(job)

    assert result.salary_min == 1000.5
    assert result.salary_max == 2000.0
    assert result.model_dump(mode="json")["salary_min"] == 1000.5


def test_redirect_to_private_address_is_blocked(monkeypatch) -> None:
    def resolver(host, port):
        if host == "127.0.0.1": raise BlockedUrlError("Non-public address")
        return [(2, "8.8.8.8")]
    fetcher = SafeHttpFetcher(resolver=resolver)
    class Sock:
        def close(self): pass
    redirect = SimpleNamespace(status=302, getheader=lambda name: "//127.0.0.1/admin" if name == "Location" else None)
    monkeypatch.setattr(fetcher, "_request", lambda *args: (redirect, Sock()))
    with pytest.raises(BlockedUrlError): fetcher.fetch("https://example.com/job")


def test_rejects_non_html_and_oversized_body(monkeypatch) -> None:
    fetcher = SafeHttpFetcher(resolver=lambda host, port: [(2, "8.8.8.8")], max_body_bytes=2)
    class Sock:
        def close(self): pass
    non_html = SimpleNamespace(status=200, getheader=lambda name: "application/pdf" if name == "Content-Type" else None)
    monkeypatch.setattr(fetcher, "_request", lambda *args: (non_html, Sock()))
    with pytest.raises(Exception): fetcher.fetch("https://example.com/job")
    body = iter([b"abc", b""])
    html = SimpleNamespace(status=200, getheader=lambda name: "text/html" if name == "Content-Type" else None, headers=SimpleNamespace(get_content_charset=lambda: "utf-8"), read=lambda size: next(body))
    monkeypatch.setattr(fetcher, "_request", lambda *args: (html, Sock()))
    with pytest.raises(ResponseTooLargeError): fetcher.fetch("https://example.com/job")


def test_request_closes_socket_when_http_protocol_is_malformed(monkeypatch) -> None:
    closed = []
    class Socket:
        def settimeout(self, value): pass
        def connect(self, value): pass
        def sendall(self, value): pass
        def close(self): closed.append(True)
    class BrokenResponse:
        def __init__(self, sock): pass
        def begin(self): raise __import__("http.client").client.BadStatusLine("bad")
    monkeypatch.setattr("app.services.safe_http_fetcher.socket.socket", lambda *args: Socket())
    monkeypatch.setattr("app.services.safe_http_fetcher.http.client.HTTPResponse", BrokenResponse)
    fetcher = SafeHttpFetcher()
    with pytest.raises(FetchError): fetcher._request(_validate_url("http://example.com/"), (2, "8.8.8.8"), 9999999999)
    assert closed == [True]
