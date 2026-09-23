import json
import socket
from datetime import datetime, timezone

import pytest

import app.services.trudvsem as trudvsem_module
from app.services.trudvsem import (
    MAX_RESPONSE_BYTES,
    TrudvsemBadResponseError,
    TrudvsemClient,
    TrudvsemError,
    TrudvsemRateLimitError,
    TrudvsemTimeoutError,
    TrudvsemVacancyNotFoundError,
    map_vacancy,
)


class FakeHTTPResponse:
    def __init__(self, body: bytes, *, content_length: str | None = None):
        self.status = 200
        self.body = body
        self.headers = {} if content_length is None else {"Content-Length": content_length}
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.body[:size]


def vacancy_payload(**changes: object) -> dict[str, object]:
    vacancy: dict[str, object] = {
        "id": "vacancy-1",
        "company": {"companycode": "company-1", "name": "Acme"},
        "job-name": "Backend Developer",
        "vac_url": "https://trudvsem.ru/vacancy/card/company-1/vacancy-1",
        "region": {"region_code": "77", "name": "Москва"},
        "addresses": {"address": [{"location": "Москва, ул. Тестовая"}]},
        "duty": "Build APIs",
        "requirements": "Python",
        "qualification": "Высшее образование",
        "employment": "Дистанционная (удаленная) работа",
        "salary": "от 0 до 200000",
        "salary_min": 0,
        "salary_max": 200000,
        "currency": "«руб.»",
        "date_modify": "2026-09-23T10:00:00+0300",
        "creation-date": "2026-09-01",
        "workPlaceType": {"workPlaceOrdinary": True},
        "skills": ["Python"],
    }
    vacancy.update(changes)
    return {"vacancy": vacancy}


def response(*items: dict[str, object], total: int | None = None) -> bytes:
    return json.dumps(
        {
            "status": "200",
            "meta": {"total": len(items) if total is None else total, "limit": 10},
            "results": {"vacancies": list(items)},
        }
    ).encode()


def test_search_maps_contract_and_pagination() -> None:
    calls: list[tuple[str, float]] = []

    def transport(url: str, timeout: float):
        calls.append((url, timeout))
        return 200, response(vacancy_payload(), total=25)

    page = TrudvsemClient(timeout_seconds=7, transport=transport).search(
        "Backend", limit=10, offset=20, region_code="7700000000000"
    )

    assert page.total == 25
    assert page.limit == 10
    assert page.offset == 20
    assert len(page.items) == 1
    assert "/region/7700000000000?" in calls[0][0]
    assert "text=Backend" in calls[0][0] and "limit=10" in calls[0][0] and "offset=20" in calls[0][0]
    assert calls[0][1] == 7


def test_default_timeout_is_25_seconds() -> None:
    calls: list[float] = []

    def transport(_url: str, timeout: float):
        calls.append(timeout)
        return 200, response(vacancy_payload())

    TrudvsemClient(transport=transport).search("Python", limit=1, offset=0)

    assert calls == [25.0]


def test_mapping_is_deterministic_and_conservative() -> None:
    fetched_at = datetime(2026, 9, 23, tzinfo=timezone.utc)
    vacancy = map_vacancy(vacancy_payload(), fetched_at=fetched_at)

    assert vacancy.external_id == "vacancy-1"
    assert vacancy.source_scope == "company-1"
    assert vacancy.title == "Backend Developer"
    assert vacancy.company == "Acme"
    assert vacancy.location == "Москва, ул. Тестовая"
    assert vacancy.workplace_type == "remote"
    assert vacancy.salary_min is None
    assert str(vacancy.salary_max) == "200000"
    assert vacancy.salary_currency == "RUB"
    assert vacancy.requirements_text == "Python\n\nКвалификация: Высшее образование"
    assert vacancy.source_updated_at == datetime.fromisoformat("2026-09-23T10:00:00+0300")
    assert fetched_at == vacancy.fetched_at
    assert not hasattr(vacancy, "published_at")
    assert not hasattr(vacancy, "skills")


def test_mapping_uses_region_fallback_and_does_not_infer_workplace() -> None:
    item = vacancy_payload(addresses={}, employment="Полная занятость", salary_max=0, currency="USD")
    vacancy = map_vacancy(item, fetched_at=datetime.now(timezone.utc))
    assert vacancy.location == "Москва"
    assert vacancy.workplace_type == "unknown"
    assert vacancy.salary_max is None
    assert vacancy.salary_currency is None


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (503, b"{}", TrudvsemError),
        (429, b"{}", TrudvsemRateLimitError),
        (200, b'{"status":"500","results":{}}', TrudvsemBadResponseError),
        (200, b"not-json", TrudvsemBadResponseError),
        (200, b'{"status":"200","meta":{"total":0}}', TrudvsemBadResponseError),
    ],
)
def test_search_errors(status: int, body: bytes, error: type[Exception]) -> None:
    client = TrudvsemClient(transport=lambda _url, _timeout: (status, body))
    with pytest.raises(error):
        client.search("Python", limit=10, offset=0)


def test_timeout_is_typed() -> None:
    def transport(_url: str, _timeout: float):
        raise socket.timeout

    with pytest.raises(TrudvsemTimeoutError):
        TrudvsemClient(transport=transport).search("Python", limit=10, offset=0)


def test_empty_detail_is_not_found() -> None:
    body = b'{"status":"200","meta":{"total":0},"results":{}}'
    with pytest.raises(TrudvsemVacancyNotFoundError):
        TrudvsemClient(transport=lambda _url, _timeout: (200, body)).get_detail(
            "company-1", "vacancy-1"
        )


def test_empty_search_is_a_valid_page() -> None:
    body = b'{"status":"200","meta":{"total":0},"results":{}}'
    page = TrudvsemClient(transport=lambda _url, _timeout: (200, body)).search(
        "NoSuchRole", limit=10, offset=0
    )
    assert page.items == []
    assert page.total == 0


def test_detail_validates_requested_identity() -> None:
    client = TrudvsemClient(transport=lambda _url, _timeout: (200, response(vacancy_payload())))
    detail = client.get_detail("company-1", "vacancy-1")
    assert detail.external_id == "vacancy-1"
    with pytest.raises(TrudvsemBadResponseError, match="identity"):
        client.get_detail("company-1", "different")


def test_response_rejects_declared_content_length_over_limit_without_reading(monkeypatch) -> None:
    source_response = FakeHTTPResponse(b"{}", content_length=str(MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(trudvsem_module, "urlopen", lambda *_args, **_kwargs: source_response)

    with pytest.raises(TrudvsemBadResponseError, match="too large"):
        TrudvsemClient().search("Python", limit=10, offset=0)

    assert source_response.read_sizes == []


@pytest.mark.parametrize("content_length", [None, "1"])
def test_response_rejects_actual_body_over_limit_even_if_length_is_missing_or_false(
    monkeypatch, content_length: str | None
) -> None:
    source_response = FakeHTTPResponse(b"x" * (MAX_RESPONSE_BYTES + 1), content_length=content_length)
    monkeypatch.setattr(trudvsem_module, "urlopen", lambda *_args, **_kwargs: source_response)
    parser_called = False

    def unexpected_parse(_body):
        nonlocal parser_called
        parser_called = True
        raise AssertionError("oversized response reached JSON parser")

    monkeypatch.setattr(trudvsem_module.json, "loads", unexpected_parse)
    with pytest.raises(TrudvsemBadResponseError, match="too large"):
        TrudvsemClient().search("Python", limit=10, offset=0)

    assert source_response.read_sizes == [MAX_RESPONSE_BYTES + 1]
    assert parser_called is False


def test_response_at_exact_size_limit_is_accepted(monkeypatch) -> None:
    normal = response(vacancy_payload())
    boundary_body = normal + b" " * (MAX_RESPONSE_BYTES - len(normal))
    source_response = FakeHTTPResponse(boundary_body, content_length=str(MAX_RESPONSE_BYTES))
    monkeypatch.setattr(trudvsem_module, "urlopen", lambda *_args, **_kwargs: source_response)

    page = TrudvsemClient().search("Python", limit=10, offset=0)

    assert len(page.items) == 1
    assert source_response.read_sizes == [MAX_RESPONSE_BYTES + 1]


def test_normal_default_transport_response_still_works(monkeypatch) -> None:
    body = response(vacancy_payload())
    source_response = FakeHTTPResponse(body, content_length=str(len(body)))
    monkeypatch.setattr(trudvsem_module, "urlopen", lambda *_args, **_kwargs: source_response)

    page = TrudvsemClient().search("Python", limit=10, offset=0)

    assert page.items[0].external_id == "vacancy-1"
