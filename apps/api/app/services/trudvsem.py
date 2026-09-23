from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


BASE_URL = "https://opendata.trudvsem.ru/api/v1/vacancies"
REMOTE_EMPLOYMENT = "Дистанционная (удаленная) работа"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class TrudvsemError(Exception):
    code = "source_unavailable"


class TrudvsemTimeoutError(TrudvsemError):
    code = "source_timeout"


class TrudvsemRateLimitError(TrudvsemError):
    code = "source_rate_limited"


class TrudvsemBadResponseError(TrudvsemError):
    code = "source_bad_response"


class TrudvsemVacancyNotFoundError(TrudvsemError):
    code = "vacancy_not_found"


@dataclass(frozen=True)
class ExternalVacancy:
    source_scope: str
    external_id: str
    source_url: str
    title: str
    company: str
    location: str | None
    description: str | None
    requirements_text: str | None
    workplace_type: str
    salary_text: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    source_updated_at: datetime | None
    fetched_at: datetime


@dataclass(frozen=True)
class SearchPage:
    items: list[ExternalVacancy]
    total: int
    limit: int
    offset: int


Transport = Callable[[str, float], tuple[int, bytes]]


class TrudvsemClient:
    def __init__(self, *, timeout_seconds: float = 25.0, transport: Transport | None = None) -> None:
        self._timeout_seconds = timeout_seconds
        self._transport = transport or _urlopen_transport

    def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int,
        region_code: str | None = None,
    ) -> SearchPage:
        if not query.strip() or not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid Trudvsem search parameters")
        path = BASE_URL
        if region_code is not None:
            if not region_code.isdigit():
                raise ValueError("region_code must contain digits only")
            path = f"{path}/region/{region_code}"
        payload = self._get_json(
            f"{path}?{urlencode({'text': query.strip(), 'limit': limit, 'offset': offset})}"
        )
        meta = payload.get("meta")
        if not isinstance(meta, dict) or not isinstance(meta.get("total"), int):
            raise TrudvsemBadResponseError("Missing search metadata")
        vacancies = _vacancy_list(payload, allow_empty=meta["total"] == 0)
        fetched_at = datetime.now(timezone.utc)
        return SearchPage(
            items=[map_vacancy(item, fetched_at=fetched_at) for item in vacancies],
            total=meta["total"],
            limit=limit,
            offset=offset,
        )

    def get_detail(self, source_scope: str, external_id: str) -> ExternalVacancy:
        if (
            re.fullmatch(r"[A-Za-z0-9-]+", source_scope) is None
            or re.fullmatch(r"[A-Za-z0-9-]+", external_id) is None
        ):
            raise ValueError("Invalid Trudvsem vacancy identity")
        payload = self._get_json(f"{BASE_URL}/vacancy/{source_scope}/{external_id}")
        vacancies = _vacancy_list(payload, allow_empty=True)
        if not vacancies:
            raise TrudvsemVacancyNotFoundError("Vacancy not found")
        if len(vacancies) != 1:
            raise TrudvsemBadResponseError("Unexpected detail result count")
        vacancy = map_vacancy(vacancies[0], fetched_at=datetime.now(timezone.utc))
        if vacancy.source_scope != source_scope or vacancy.external_id != external_id:
            raise TrudvsemBadResponseError("Detail identity does not match request")
        return vacancy

    def _get_json(self, url: str) -> dict[str, object]:
        try:
            status_code, body = self._transport(url, self._timeout_seconds)
        except (TimeoutError, socket.timeout) as error:
            raise TrudvsemTimeoutError("Source request timed out") from error
        except HTTPError as error:
            if error.code == 429:
                raise TrudvsemRateLimitError("Source rate limited the request") from error
            raise TrudvsemError(f"Source returned HTTP {error.code}") from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise TrudvsemTimeoutError("Source request timed out") from error
            raise TrudvsemError("Source request failed") from error
        except OSError as error:
            raise TrudvsemError("Source request failed") from error

        if status_code == 429:
            raise TrudvsemRateLimitError("Source rate limited the request")
        if not 200 <= status_code < 300:
            raise TrudvsemError(f"Source returned HTTP {status_code}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TrudvsemBadResponseError("Source returned invalid JSON") from error
        if not isinstance(payload, dict) or str(payload.get("status")) != "200":
            raise TrudvsemBadResponseError("Source returned a logical error")
        return payload


def map_vacancy(item: object, *, fetched_at: datetime) -> ExternalVacancy:
    if not isinstance(item, dict) or not isinstance(item.get("vacancy"), dict):
        raise TrudvsemBadResponseError("Malformed vacancy wrapper")
    vacancy = item["vacancy"]
    company_value = vacancy.get("company")
    if not isinstance(company_value, dict):
        raise TrudvsemBadResponseError("Malformed vacancy company")

    source_scope = _required_text(company_value.get("companycode"), "companycode")
    external_id = _required_text(vacancy.get("id"), "id")
    source_url = _source_url(vacancy.get("vac_url"))
    title = _required_text(vacancy.get("job-name"), "job-name")
    company = _required_text(company_value.get("name"), "company.name")

    address = _first_address(vacancy.get("addresses"))
    region = vacancy.get("region")
    region_name = _optional_text(region.get("name")) if isinstance(region, dict) else None
    location = address or region_name
    requirements = _merge_requirements(
        _optional_text(vacancy.get("requirements")),
        _optional_text(vacancy.get("qualification")),
    )
    salary_min = _positive_decimal(vacancy.get("salary_min"))
    salary_max = _positive_decimal(vacancy.get("salary_max"))
    if salary_min is not None and salary_max is not None and salary_max < salary_min:
        salary_min = salary_max = None
    return ExternalVacancy(
        source_scope=source_scope,
        external_id=external_id,
        source_url=source_url,
        title=title,
        company=company,
        location=location,
        description=_optional_text(vacancy.get("duty")),
        requirements_text=requirements,
        workplace_type="remote" if vacancy.get("employment") == REMOTE_EMPLOYMENT else "unknown",
        salary_text=_optional_text(vacancy.get("salary")),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=_currency(vacancy.get("currency")),
        source_updated_at=_datetime(vacancy.get("date_modify")),
        fetched_at=fetched_at,
    )


def _urlopen_transport(url: str, timeout_seconds: float) -> tuple[int, bytes]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "JobHunterAI/1.0"})
    with urlopen(request, timeout=timeout_seconds) as response:
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > MAX_RESPONSE_BYTES:
                raise TrudvsemBadResponseError("Source response is too large")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise TrudvsemBadResponseError("Source response is too large")
        return response.status, body


def _vacancy_list(payload: dict[str, object], *, allow_empty: bool = False) -> list[object]:
    results = payload.get("results")
    if not isinstance(results, dict):
        raise TrudvsemBadResponseError("Missing results")
    vacancies = results.get("vacancies")
    if vacancies is None and allow_empty and not results:
        return []
    if not isinstance(vacancies, list):
        raise TrudvsemBadResponseError("Missing vacancies")
    return vacancies


def _required_text(value: object, field: str) -> str:
    result = _optional_text(value)
    if result is None:
        raise TrudvsemBadResponseError(f"Missing {field}")
    return result


def _source_url(value: object) -> str:
    url = _required_text(value, "vac_url")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "trudvsem.ru":
        raise TrudvsemBadResponseError("Invalid vac_url")
    return url


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _first_address(value: object) -> str | None:
    if not isinstance(value, dict) or not isinstance(value.get("address"), list):
        return None
    for item in value["address"]:
        if isinstance(item, dict):
            location = _optional_text(item.get("location"))
            if location is not None:
                return location[:512]
    return None


def _merge_requirements(requirements: str | None, qualification: str | None) -> str | None:
    if requirements and qualification and qualification.casefold() not in requirements.casefold():
        return f"{requirements}\n\nКвалификация: {qualification}"
    return requirements or qualification


def _positive_decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result > 0 else None


def _currency(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.casefold().replace("«", "").replace("»", "").strip().rstrip(".")
    return "RUB" if normalized == "руб" else None


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)
