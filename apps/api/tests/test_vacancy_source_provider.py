"""Provider contracts use injected transport or internal candidates, never HTTP."""

import socket
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

import app.main as main_module
from app.models import Application, Job, User
from app.services.discover import (
    SourceIdentityConflictError,
    save_discovered_job,
    search_discover_jobs,
)
from app.services.trudvsem import TrudvsemClient, TrudvsemProvider, map_vacancy
from app.services.vacancy_sources import DiscoverQuery, ExternalVacancy, SearchPage
from conftest import TestSessionLocal, client
from test_discover_api import create_user, vacancy
from test_trudvsem_client import response, vacancy_payload


class DisabledAI:
    configured = False


def test_real_trudvsem_provider_search_detail_and_mapping_parity() -> None:
    calls: list[tuple[str, float]] = []
    payload = vacancy_payload()

    def transport(url: str, timeout: float):
        calls.append((url, timeout))
        return 200, response(payload, total=25)

    provider = TrudvsemProvider(TrudvsemClient(timeout_seconds=7, transport=transport))
    page = provider.search(DiscoverQuery("Backend", "RU", "7700000000000", 10, 20))
    assert provider.source_key == "trudvsem"
    assert (page.total, page.limit, page.offset) == (25, 10, 20)
    item = page.items[0]
    assert item == map_vacancy(payload, fetched_at=item.fetched_at)
    assert item.source == "trudvsem"
    assert (item.source_scope, item.external_id) == ("company-1", "vacancy-1")
    assert item.source_url == "https://trudvsem.ru/vacancy/card/company-1/vacancy-1"
    assert urlsplit(calls[0][0]).path.endswith("/region/7700000000000")
    assert parse_qs(urlsplit(calls[0][0]).query) == {
        "text": ["Backend"], "limit": ["10"], "offset": ["20"]
    }
    detail = provider.get_detail(item.source_scope, item.external_id)
    assert replace(detail, fetched_at=item.fetched_at) == item
    assert calls[1][0].endswith("/vacancy/company-1/vacancy-1")
    assert [timeout for _, timeout in calls] == [7, 7]


def test_api_uses_real_provider_for_search_and_idempotent_save(monkeypatch) -> None:
    calls: list[str] = []

    def transport(url: str, _timeout: float):
        calls.append(url)
        return 200, response(vacancy_payload(), total=25)

    monkeypatch.setattr(main_module, "trudvsem_client", TrudvsemClient(transport=transport))
    monkeypatch.setattr(main_module, "ai_enrichment_service", DisabledAI())
    user_id = create_user(9001)
    result = client.get(
        f"/users/{user_id}/discover/jobs",
        params={"market_country": "ru", "query": " Backend ", "limit": 10, "offset": 10},
    )
    assert result.status_code == 200
    page = result.json()
    assert (page["source_total"], page["returned_count"], page["next_offset"]) == (25, 1, 20)
    assert (page["limit"], page["offset"], page["locally_filtered"]) == (10, 10, False)
    item = page["items"][0]
    assert item["source"] == "trudvsem"
    assert item["requirements_text"] == "Python\n\nКвалификация: Высшее образование"
    assert item["salary_min"] is None and item["salary_max"] == 200000
    assert parse_qs(urlsplit(calls[0]).query)["text"] == ["Backend"]
    identity = {key: item[key] for key in ("source", "source_scope", "external_id")}
    saved = client.post(f"/users/{user_id}/discover/jobs/save", json=identity)
    repeated = client.post(f"/users/{user_id}/discover/jobs/save", json=identity)
    assert saved.status_code == repeated.status_code == 200
    assert saved.json()["job"]["source_url"] == item["source_url"]
    assert saved.json()["job_created"] is True
    assert repeated.json()["job_created"] is False
    assert repeated.json()["application_created"] is False
    with TestSessionLocal() as session:
        assert session.query(Job).count() == session.query(Application).count() == 1


@pytest.mark.parametrize("operation", ["search", "save"])
@pytest.mark.parametrize(
    ("failure", "http_status", "code"),
    [
        (socket.timeout(), 504, "source_timeout"),
        (URLError(socket.timeout()), 504, "source_timeout"),
        (URLError("unreachable"), 503, "source_unavailable"),
        (HTTPError("https://example.test", 429, "limited", {}, None), 429, "source_rate_limited"),
        (HTTPError("https://example.test", 503, "unavailable", {}, None), 503, "source_unavailable"),
        ((429, b"{}"), 429, "source_rate_limited"),
        ((503, b"{}"), 503, "source_unavailable"),
        ((200, b"not-json"), 502, "source_bad_response"),
        ((200, response({"vacancy": {}}, total=1)), 502, "source_bad_response"),
    ],
)
def test_transport_errors_keep_api_status_and_code(monkeypatch, operation, failure, http_status, code):
    def transport(_url: str, _timeout: float):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(main_module, "trudvsem_client", TrudvsemClient(transport=transport))
    user_id = create_user(9002)
    if operation == "search":
        result = client.get(
            f"/users/{user_id}/discover/jobs", params={"market_country": "RU", "query": "Python"}
        )
    else:
        result = client.post(
            f"/users/{user_id}/discover/jobs/save",
            json={"source": "trudvsem", "source_scope": "company-1", "external_id": "vacancy-1"},
        )
    assert result.status_code == http_status
    assert result.json() == {"detail": {"code": code}}
    with TestSessionLocal() as session:
        assert session.query(Job).count() == session.query(Application).count() == 0


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"market_country": "AM", "query": "Python"}, "unsupported_market"),
        ({"market_country": "RU", "query": "   "}, "INVALID_DISCOVER_QUERY"),
        ({"market_country": "RU", "query": "Python", "region_code": "not-a-code"}, "INVALID_DISCOVER_QUERY"),
    ],
)
def test_invalid_search_does_not_reach_transport(monkeypatch, params, code):
    def unexpected_transport(_url: str, _timeout: float):
        pytest.fail("Invalid request reached external transport")

    monkeypatch.setattr(main_module, "trudvsem_client", TrudvsemClient(transport=unexpected_transport))
    user_id = create_user(9003)
    result = client.get(f"/users/{user_id}/discover/jobs", params=params)
    assert result.status_code == 422
    assert result.json() == {"detail": {"code": code}}


class InternalCandidateProvider:
    """Test double over internal DTOs only, not a speculative external integration."""

    def __init__(self, item: ExternalVacancy):
        self.item = item
        self.source_key = item.source
        self.queries: list[DiscoverQuery] = []

    def supports_market(self, market_country: str) -> bool:
        return market_country == "RU"

    def search(self, query: DiscoverQuery) -> SearchPage:
        self.queries.append(query)
        return SearchPage([self.item], 15, query.limit, query.offset)

    def get_detail(self, source_scope: str, external_id: str) -> ExternalVacancy:
        assert (source_scope, external_id) == (self.item.source_scope, self.item.external_id)
        return self.item


def test_generic_service_scopes_search_and_persistence_identity_by_source() -> None:
    first = vacancy()
    other = replace(first, source="company_site", source_url="https://example.test/jobs/1")
    with TestSessionLocal() as session:
        user = User(telegram_id=9004)
        session.add(user)
        session.commit()
        for item in (first, other):
            provider = InternalCandidateProvider(item)
            page = search_discover_jobs(
                session, user.id, market_country="ru", query=" Backend ", limit=10, offset=0,
                region_code="77", remote_only=False, provider=provider,
            )
            assert provider.queries == [DiscoverQuery("Backend", "RU", "77", 10, 0)]
            assert page.items[0].source == item.source
            assert page.items[0].already_saved_for_user is False
            saved = save_discovered_job(
                session, user.id, source=item.source, source_scope=item.source_scope,
                external_id=item.external_id, provider=provider, ai_service=DisabledAI(),
            )
            assert saved.job_created is True
            assert saved.job.source == item.source
            page = search_discover_jobs(
                session, user.id, market_country="RU", query="Backend", limit=10, offset=0,
                region_code=None, remote_only=False, provider=provider,
            )
            assert page.items[0].already_saved_for_user is True
        assert session.query(Job).count() == session.query(Application).count() == 2


def test_same_url_and_ids_from_different_source_are_a_conflict() -> None:
    first = vacancy()
    other = replace(first, source="company_site")
    with TestSessionLocal() as session:
        user = User(telegram_id=9005)
        session.add(user)
        session.commit()
        for item in (first, other):
            provider = InternalCandidateProvider(item)
            if item is first:
                save_discovered_job(
                    session, user.id, source=item.source, source_scope=item.source_scope,
                    external_id=item.external_id, provider=provider, ai_service=DisabledAI(),
                )
            else:
                with pytest.raises(SourceIdentityConflictError):
                    save_discovered_job(
                        session, user.id, source=item.source, source_scope=item.source_scope,
                        external_id=item.external_id, provider=provider, ai_service=DisabledAI(),
                    )
        assert session.query(Job).one().source == "trudvsem"
        assert session.query(Application).count() == 1
