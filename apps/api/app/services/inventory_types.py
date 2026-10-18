"""Inventory v1 identity and bounded source-only evidence; no HTTP or routing."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.discover_feed_types import Evidence

MAX_SOURCE_RECORDS = 10_000
MAX_ITEMS = 1_000
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_ITEM_BYTES = 128 * 1024
SOURCE_PAGE_SIZE = 100
FRESHNESS = timedelta(minutes=15)
VIEW_LIFETIME = timedelta(minutes=30)
LEASE_LIFETIME = timedelta(seconds=60)
TOKEN_PATTERN = r"^[A-Za-z0-9_-]{32}$"


class InventoryError(Exception):
    """Internal domain errors deliberately contain no identity/token/evidence."""


class InventoryIdentityInvalid(InventoryError):
    pass


class InventoryGenerationConflict(InventoryError):
    pass


class InventoryGenerationNotFound(InventoryError):
    pass


class InventoryGenerationExpired(InventoryError):
    pass


class InventoryInvalidTransition(InventoryError):
    pass


class InventoryPublicationInvalid(InventoryError):
    pass


class InventoryViewInvalid(InventoryError):
    pass


class InventoryCapacityExceeded(InventoryError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InventoryError("UTC-aware timestamp required")
    return value.astimezone(timezone.utc)


def stored_utc(value: datetime) -> datetime:
    # SQLite strips the offset of timezone-aware DateTime columns. Only use on
    # values read from our own UTC-only storage, never on caller input.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else utc(value)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_json_payload(value: object) -> None:
    """Reject JSONB-incompatible strings without recursion or copying containers.

    This checks serialized evidence, not an alternative to the typed DTOs.
    Bound both nesting and traversal work, including malformed/cyclic input.
    """
    def members(mapping: dict) -> Iterator[object]:
        for key, child in mapping.items():
            if not isinstance(key, str):
                raise InventoryError("Invalid inventory evidence")
            yield key
            yield child

    stack: list[Iterator[object]] = [iter((value,))]
    visited = 0
    while stack:
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        visited += 1
        if visited > MAX_ITEM_BYTES:
            raise InventoryError("Invalid inventory evidence")
        if isinstance(node, str):
            if "\x00" in node:
                raise InventoryError("Invalid inventory evidence")
        elif isinstance(node, (dict, list)):
            if len(stack) > 64:
                raise InventoryError("Invalid inventory evidence")
            if isinstance(node, dict):
                stack.append(members(node))
            else:
                stack.append(iter(node))
        elif node is not None and type(node) not in (bool, int, float):
            raise InventoryError("Invalid inventory evidence")


@dataclass(frozen=True)
class InventoryIdentity:
    query: str = field(repr=False)
    market_country: str = "RU"
    market_region: str | None = None
    remote_only: Literal[True] = True
    source: Literal["trudvsem"] = "trudvsem"
    query_normalization_version: int = 1
    source_query_version: int = 1
    predicate_version: int = 1
    snapshot_schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not 1 <= len(self.query.strip()) <= 200:
            raise InventoryIdentityInvalid("Invalid query")
        try:
            self.query.encode("utf-8")
        except UnicodeError:
            raise InventoryIdentityInvalid("Invalid query encoding") from None
        if not isinstance(self.market_country, str) or self.market_country.upper() != "RU":
            raise InventoryIdentityInvalid("Unsupported market")
        if self.market_region is not None and (
            not isinstance(self.market_region, str) or re.fullmatch(r"[0-9]{1,32}", self.market_region) is None
        ):
            raise InventoryIdentityInvalid("Invalid region")
        if self.source != "trudvsem" or self.remote_only is not True:
            raise InventoryIdentityInvalid("Unsupported inventory semantics")
        for version in (self.query_normalization_version, self.source_query_version,
                        self.predicate_version, self.snapshot_schema_version):
            if type(version) is not int or version < 1:
                raise InventoryIdentityInvalid("Invalid semantic version")
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "market_country", "RU")

    @property
    def canonical(self) -> str:
        return canonical_json({
            "source": self.source, "query": self.query, "market_country": self.market_country,
            "market_region": self.market_region, "remote_only": self.remote_only,
            "query_normalization_version": self.query_normalization_version,
            "source_query_version": self.source_query_version, "predicate_version": self.predicate_version,
            "snapshot_schema_version": self.snapshot_schema_version,
        })

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()


class RemoteEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    field: Literal["employment"] = "employment"
    value: Literal["Дистанционная (удаленная) работа"] = "Дистанционная (удаленная) работа"
    predicate_version: Literal[1] = 1


class InventoryItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot: Evidence
    remote_evidence: RemoteEvidence = RemoteEvidence()


@dataclass(frozen=True)
class InventoryCounters:
    next_source_page: int
    pages_committed: int
    records_examined: int
    item_count: int
    payload_bytes: int


@dataclass(frozen=True)
class Resolution:
    generation_id: str
    outcome: Literal["reused_ready", "reused_building", "created_building"]


@dataclass(frozen=True)
class InventoryLease:
    generation_id: str
    fence: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class InventoryViewHandle:
    generation_id: str
    token: str = field(repr=False)
    page_size: int
    expires_at: datetime


@dataclass(frozen=True)
class InventoryViewInfo:
    generation_id: str
    status: str
    page_size: int
    expires_at: datetime
