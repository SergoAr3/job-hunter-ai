"""Internal source contract; no external API schemas or persistence behavior."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class SourceError(Exception):
    code = "source_unavailable"


@dataclass(frozen=True)
class DiscoverQuery:
    keywords: str
    market_country: str
    # Administrative region selection within the search market, not residence
    # or vacancy location text. Currently supplied by the existing RU selector.
    market_region: str | None
    limit: int
    offset: int


@dataclass(frozen=True)
class ExternalVacancy:
    source: str
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


class VacancySourceProvider(Protocol):
    @property
    def source_key(self) -> str: ...

    def supports_market(self, market_country: str) -> bool: ...

    def search(self, query: DiscoverQuery) -> SearchPage: ...

    def get_detail(self, source_scope: str, external_id: str) -> ExternalVacancy: ...
