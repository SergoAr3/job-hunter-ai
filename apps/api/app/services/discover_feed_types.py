"""Versioned public-source evidence and bounded continuation contracts."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import DiscoverJobItemOut
from app.services.vacancy_sources import ExternalVacancy

VERSION = 1
CHAIN_TTL_SECONDS = 1800
CLAIM_SECONDS = 45
MAX_ACTIVE_CHAINS = 20
MAX_CHAIN_STATES = 64
MAX_CHAIN_PAGES = 32
MAX_STATE_BYTES = 256 * 1024
CLEANUP_BATCH = 10
TOKEN_ATTEMPTS = 4
TOKEN_PATTERN = r"^[A-Za-z0-9_-]{32}$"
SOURCE_PAGE_SIZE = 10
MAX_SOURCE_PAGES = 4
MAX_RECORDS = 40
SCAN_SECONDS = 15.0
COLD_SECONDS = 13.0


class FeedError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedStart(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    market_country: Literal["RU"] = "RU"
    market_region: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[0-9]+$")
    remote_only: Literal[True]
    limit: int = Field(default=10, ge=1, le=10)


class FeedAdvance(StrictModel):
    cursor: str = Field(pattern=TOKEN_PATTERN, min_length=32, max_length=32)


class FeedIdentity(FeedStart):
    provider: Literal["trudvsem"] = "trudvsem"
    algorithm: Literal["remote-feed-v1"] = "remote-feed-v1"


class Evidence(StrictModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    source: Literal["trudvsem"]
    source_scope: str = Field(min_length=1, max_length=255)
    external_id: str = Field(min_length=1, max_length=255)
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

    def external(self) -> ExternalVacancy:
        return ExternalVacancy(**self.model_dump())


class Checkpoint(StrictModel):
    offset: int = Field(default=0, ge=0, le=1_000_000_000)
    exhausted: bool = False
    remainder: list[Evidence] = Field(default_factory=list, max_length=SOURCE_PAGE_SIZE)


class StoredFeed(StrictModel):
    version: Literal[1] = 1
    checkpoint: Checkpoint = Field(default_factory=Checkpoint)
    items: list[Evidence] = Field(default_factory=list, max_length=10)

    def encoded(self) -> str:
        result = self.model_dump_json()
        if len(result.encode("utf-8")) > MAX_STATE_BYTES:
            raise FeedError("FEED_EVIDENCE_TOO_LARGE", 413)
        return result


class FeedOut(StrictModel):
    items: list[DiscoverJobItemOut]
    returned_count: int
    cursor: str
    prev_cursor: str | None
    next_cursor: str | None
    state: Literal["page", "paused", "end"]
    expires_at: datetime
