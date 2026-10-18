"""One public operation = one transaction on a caller-owned, idle Session.

All generation mutations take key -> generation locks. No network, scheduler,
route, cleanup loop, or process-local correctness locks live here.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import exists, insert, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    DiscoverInventoryGeneration as Generation, DiscoverInventoryItem as Item,
    DiscoverInventoryKey as Key, DiscoverInventoryView as View, User,
)
from app.services.inventory_types import (
    FRESHNESS, LEASE_LIFETIME, MAX_ITEM_BYTES, MAX_ITEMS, MAX_PAYLOAD_BYTES,
    MAX_SOURCE_RECORDS, SOURCE_PAGE_SIZE, TOKEN_PATTERN, VIEW_LIFETIME,
    InventoryCapacityExceeded, InventoryCounters, InventoryError,
    InventoryGenerationConflict, InventoryGenerationExpired, InventoryGenerationNotFound,
    InventoryIdentity, InventoryIdentityInvalid, InventoryInvalidTransition,
    InventoryItemInput, InventoryLease, InventoryPublicationInvalid,
    InventoryViewHandle, InventoryViewInfo, InventoryViewInvalid, Resolution,
    canonical_json, stored_utc, utc, utcnow, validate_json_payload,
)


class InventoryRepository:
    def __init__(self, session: Session, *, clock: Callable[[], datetime] = utcnow,
                 token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24)):
        self.session, self.clock, self.token_factory = session, clock, token_factory

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self.session.in_transaction():
            raise RuntimeError("Inventory operation requires an idle Session")
        if self.session.new or self.session.dirty or self.session.deleted:
            raise RuntimeError("Inventory operation requires a clean Session")
        try:
            with self.session.begin():
                if self.session.get_bind().dialect.name == "sqlite":
                    connection = self.session.connection()
                    driver = connection.connection.driver_connection
                    if driver is not None and not driver.in_transaction:
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                yield
                self.session.flush()
        except DBAPIError as error:
            # Canonical identity and token digests must not enter SQL tracebacks.
            error.hide_parameters = True
            raise

    def _now(self, now: datetime | None) -> datetime:
        return utc(now if now is not None else self.clock())

    def _key(self, identity: InventoryIdentity, at: datetime) -> Key:
        key = self.session.scalar(select(Key).where(Key.identity_digest == identity.digest).with_for_update()
                                  .execution_options(populate_existing=True))
        if key is None:
            try:
                with self.session.begin_nested():
                    self.session.execute(insert(Key).values(id=uuid4().hex, identity_digest=identity.digest,
                        canonical_identity=identity.canonical, created_at=at, updated_at=at))
            except IntegrityError:
                # A competing insertion may have won. Only recover that digest
                # collision; a savepoint keeps the outer transaction usable.
                if self.session.scalar(select(Key.id).where(Key.identity_digest == identity.digest)) is None:
                    raise
            key = self.session.scalar(select(Key).where(Key.identity_digest == identity.digest).with_for_update()
                                      .execution_options(populate_existing=True))
        if key is None:
            raise InventoryGenerationConflict("Key unavailable")
        if key.canonical_identity != identity.canonical:
            raise InventoryIdentityInvalid("Identity digest collision")
        return key

    def _generation(self, generation_id: str) -> Generation:
        # The first lookup acquires no lock. Key identity is immutable. Expiry
        # never deletes in PR1; future cleanup must use the same lock order.
        key_id = self.session.scalar(select(Generation.key_id).where(Generation.id == generation_id))
        if key_id is None:
            raise InventoryGenerationNotFound("Generation not found")
        if self.session.scalar(select(Key.id).where(Key.id == key_id).with_for_update()) is None:
            raise InventoryGenerationNotFound("Generation key not found")
        generation = self.session.scalar(select(Generation).where(Generation.id == generation_id).with_for_update()
                                         .execution_options(populate_existing=True))
        if generation is None:
            raise InventoryGenerationNotFound("Generation not found")
        return generation

    def resolve_generation(self, identity: InventoryIdentity, now: datetime | None = None) -> Resolution:
        with self._transaction():
            key = self._key(identity, self._now(now))
            at = self._now(now)  # Sample after a possibly contended key lock.
            key.updated_at = max(stored_utc(key.updated_at), at)
            ready = self.session.scalar(select(Generation).where(Generation.key_id == key.id,
                Generation.status == "ready", Generation.fresh_until > at)
                .order_by(Generation.completed_at.desc(), Generation.id).limit(1))
            if ready is not None:
                return Resolution(ready.id, "reused_ready")
            building = self.session.scalar(select(Generation).where(Generation.key_id == key.id,
                Generation.status == "building"))
            if building is not None:
                return Resolution(building.id, "reused_building")
            generation_id = uuid4().hex
            self.session.add(Generation(id=generation_id, key_id=key.id, created_at=at, next_attempt_at=at))
            return Resolution(generation_id, "created_building")

    @staticmethod
    def _building(generation: Generation) -> None:
        if generation.status != "building":
            raise InventoryInvalidTransition("Generation is not building")

    @staticmethod
    def _owned(generation: Generation, fence: str, at: datetime) -> None:
        InventoryRepository._building(generation)
        if (not fence or generation.lease_token != fence or generation.lease_until is None
                or stored_utc(generation.lease_until) <= at):
            raise InventoryGenerationConflict("Builder ownership lost")
        if at < stored_utc(generation.last_worked_at or generation.created_at):
            raise InventoryGenerationConflict("Clock precedes builder progress")

    @staticmethod
    def _counters(generation: Generation) -> InventoryCounters:
        return InventoryCounters(generation.next_source_page, generation.pages_committed,
            generation.records_examined, generation.item_count, generation.payload_bytes)

    def claim_generation(self, generation_id: str, now: datetime | None = None) -> InventoryLease:
        """Ownership foundation only; does not schedule or execute a build."""
        with self._transaction():
            generation = self._generation(generation_id)
            at = self._now(now)
            self._building(generation)
            if at < stored_utc(generation.created_at):
                raise InventoryGenerationConflict("Clock precedes generation")
            if generation.lease_until is not None and stored_utc(generation.lease_until) > at:
                raise InventoryGenerationConflict("Builder already owns generation")
            fence, until = uuid4().hex, at + LEASE_LIFETIME
            generation.lease_token, generation.lease_until = fence, until
            generation.started_at = generation.started_at or at
            generation.last_worked_at = at
            return InventoryLease(generation_id, fence, until)

    @staticmethod
    def _payload(item: InventoryItemInput) -> tuple[dict, dict, int]:
        # Revalidate nested mutable models and serialize a detached copy. Evidence
        # rejects extra keys, including viewer-specific fields and raw payloads.
        try:
            # Malformed mutated DTOs must not emit serializer warnings carrying
            # evidence. Convert serialization failures to the safe domain error.
            validated = InventoryItemInput.model_validate(item.model_dump(warnings="error"))
            snapshot = validated.snapshot.model_dump(mode="json", warnings="error")
            evidence = validated.remote_evidence.model_dump(mode="json", warnings="error")
            utc(validated.snapshot.fetched_at)
            if validated.snapshot.source_updated_at is not None:
                utc(validated.snapshot.source_updated_at)
            payload = {"snapshot": snapshot, "remote_evidence": evidence}
            validate_json_payload(payload)
            size = len(canonical_json(payload).encode("utf-8"))
        except (ValidationError, ValueError, TypeError, AttributeError, UnicodeError):
            raise InventoryError("Invalid inventory evidence") from None
        if validated.snapshot.workplace_type != "remote" or any(
            not value.strip() or "\x00" in value for value in
            (validated.snapshot.source_scope, validated.snapshot.external_id)
        ):
            raise InventoryError("Invalid remote identity")
        if size > MAX_ITEM_BYTES:
            raise InventoryCapacityExceeded("Item payload capacity exceeded")
        return snapshot, evidence, size

    def commit_inventory_page(self, generation_id: str, *, expected_fence: str, expected_page: int,
                              source_total: int, returned_count: int,
                              items: Sequence[InventoryItemInput], now: datetime | None = None) -> InventoryCounters:
        """Commit one observed source page, including the terminal empty page.

        returned_count includes skipped/non-remote source records. Only remote
        candidates are supplied in items. No source records are fetched here.
        """
        if any(type(value) is not int or value < 0 for value in (expected_page, source_total, returned_count)):
            raise InventoryGenerationConflict("Invalid source counters")
        if source_total > MAX_SOURCE_RECORDS:
            raise InventoryCapacityExceeded("Source record capacity exceeded")
        if len(items) > returned_count or returned_count > SOURCE_PAGE_SIZE:
            raise InventoryGenerationConflict("Invalid source page size")
        prepared = [self._payload(item) for item in items]
        with self._transaction():
            generation = self._generation(generation_id)
            at = self._now(now)
            self._owned(generation, expected_fence, at)
            key = self.session.get(Key, generation.key_id)
            if key is None:
                raise InventoryGenerationNotFound("Generation key not found")
            semantics = json.loads(key.canonical_identity)
            if any(semantics.get(name) != 1 for name in ("query_normalization_version", "source_query_version",
                                                        "predicate_version", "snapshot_schema_version")):
                raise InventoryGenerationConflict("Unsupported builder semantics")
            if generation.source_exhausted or expected_page != generation.next_source_page:
                raise InventoryGenerationConflict("Source checkpoint changed")
            if generation.source_total is not None and generation.source_total != source_total:
                raise InventoryGenerationConflict("Source total changed")
            expected_count = min(SOURCE_PAGE_SIZE, max(0, source_total - expected_page * SOURCE_PAGE_SIZE))
            if returned_count != expected_count:
                raise InventoryGenerationConflict("Source page contradicts total")
            # Refuse corrupted foundation data before assigning any new ordinal.
            self._validate_items(generation)
            seen = {tuple(row) for row in self.session.execute(select(Item.source, Item.source_scope, Item.external_id)
                                                              .where(Item.generation_id == generation_id))}
            for item, (snapshot, evidence, size) in zip(items, prepared, strict=True):
                identity = (snapshot["source"], snapshot["source_scope"], snapshot["external_id"])
                if identity in seen:
                    continue
                seen.add(identity)
                if generation.item_count >= MAX_ITEMS or generation.payload_bytes + size > MAX_PAYLOAD_BYTES:
                    raise InventoryCapacityExceeded("Generation capacity exceeded")
                self.session.add(Item(generation_id=generation_id, ordinal=generation.item_count,
                    source=identity[0], source_scope=identity[1], external_id=identity[2],
                    snapshot=snapshot, remote_evidence=evidence,
                    source_updated_at=utc(item.snapshot.source_updated_at) if item.snapshot.source_updated_at else None,
                    fetched_at=utc(item.snapshot.fetched_at)))
                generation.item_count += 1
                generation.payload_bytes += size
            generation.source_total = source_total
            generation.next_source_page += 1
            generation.pages_committed += 1
            generation.records_examined += returned_count
            generation.source_exhausted = returned_count == 0
            generation.last_successful_fetch_at = generation.last_worked_at = at
            generation.page_attempts = 0
            return self._counters(generation)

    def _validate_items(self, generation: Generation) -> None:
        counters = self._counters(generation)
        if (any(type(value) is not int or value < 0 for value in vars(counters).values())
                or counters.records_examined > MAX_SOURCE_RECORDS or counters.item_count > MAX_ITEMS
                or counters.payload_bytes > MAX_PAYLOAD_BYTES or counters.item_count > counters.records_examined):
            raise InventoryPublicationInvalid("Invalid generation counters")
        rows = list(self.session.scalars(select(Item).where(Item.generation_id == generation.id)
                                        .order_by(Item.ordinal).limit(MAX_ITEMS + 1)))
        if len(rows) != generation.item_count or any(row.ordinal != index for index, row in enumerate(rows)):
            raise InventoryPublicationInvalid("Item count or ordinal sequence invalid")
        size = 0
        for row in rows:
            try:
                item = InventoryItemInput.model_validate({"snapshot": row.snapshot, "remote_evidence": row.remote_evidence})
                _, _, item_size = self._payload(item)
            except (ValidationError, InventoryError):
                raise InventoryPublicationInvalid("Stored evidence invalid") from None
            if (row.source, row.source_scope, row.external_id) != (
                item.snapshot.source, item.snapshot.source_scope, item.snapshot.external_id
            ) or stored_utc(row.fetched_at) != utc(item.snapshot.fetched_at) or (
                stored_utc(row.source_updated_at) if row.source_updated_at else None
            ) != (utc(item.snapshot.source_updated_at) if item.snapshot.source_updated_at else None):
                raise InventoryPublicationInvalid("Stored evidence identity or timestamps differ")
            size += item_size
        if size != generation.payload_bytes:
            raise InventoryPublicationInvalid("Payload accounting differs")

    def publish_generation(self, generation_id: str, *, expected_fence: str,
                           expected: InventoryCounters, now: datetime | None = None,
                           fresh_until: datetime | None = None) -> None:
        with self._transaction():
            generation = self._generation(generation_id)
            at = self._now(now)
            self._owned(generation, expected_fence, at)
            if expected != self._counters(generation):
                raise InventoryPublicationInvalid("Publication checkpoint changed")
            self._validate_items(generation)
            total = generation.source_total
            if (not generation.source_exhausted or total is None or generation.records_examined != total
                    or generation.pages_committed != (total + SOURCE_PAGE_SIZE - 1) // SOURCE_PAGE_SIZE + 1
                    or generation.next_source_page != generation.pages_committed
                    or generation.last_successful_fetch_at is None or generation.started_at is None
                    or at < stored_utc(generation.last_successful_fetch_at)):
                raise InventoryPublicationInvalid("Source traversal is incomplete")
            until = utc(fresh_until) if fresh_until is not None else at + FRESHNESS
            if until <= at or until > at + FRESHNESS:
                raise InventoryPublicationInvalid("Invalid freshness interval")
            generation.status, generation.completed_at, generation.fresh_until = "ready", at, until
            generation.lease_token = generation.lease_until = None
            generation.next_attempt_at = None

    def fail_generation(self, generation_id: str, *, expected_fence: str, failure_code: str,
                        now: datetime | None = None) -> None:
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", failure_code) is None:
            raise InventoryGenerationConflict("Invalid failure code")
        with self._transaction():
            generation = self._generation(generation_id)
            at = self._now(now)
            self._owned(generation, expected_fence, at)
            generation.status, generation.failed_at, generation.failure_code = "failed", at, failure_code
            generation.lease_token = generation.lease_until = None
            generation.next_attempt_at = None

    def _live_views(self, generation_id: str, at: datetime) -> bool:
        return bool(self.session.scalar(select(exists().where(View.generation_id == generation_id, View.expires_at > at))))

    def has_live_views(self, generation_id: str, now: datetime | None = None) -> bool:
        with self._transaction():
            self._generation(generation_id)
            return self._live_views(generation_id, self._now(now))

    def expire_generation(self, generation_id: str, now: datetime | None = None) -> None:
        """Bounded retirement foundation; never deletes rows or runs cleanup."""
        with self._transaction():
            generation = self._generation(generation_id)
            at = self._now(now)
            if generation.status not in {"ready", "failed"}:
                raise InventoryInvalidTransition("Generation cannot expire")
            if (generation.status == "ready" and generation.fresh_until is not None
                    and stored_utc(generation.fresh_until) > at) or self._live_views(generation_id, at):
                raise InventoryGenerationConflict("Generation is fresh or pinned")
            generation.status = "expired"

    def create_view(self, generation_id: str, *, page_size: int = 10, creator_user_id: int | None = None,
                    expires_at: datetime | None = None, now: datetime | None = None) -> InventoryViewHandle:
        if type(page_size) is not int or not 1 <= page_size <= 10:
            raise InventoryViewInvalid("Invalid page size")
        with self._transaction():
            generation = self._generation(generation_id)
            at = self._now(now)
            if generation.status == "expired":
                raise InventoryGenerationExpired("Generation expired")
            if generation.status not in {"building", "ready"}:
                raise InventoryInvalidTransition("Cannot create view")
            if generation.status == "ready" and (generation.fresh_until is None or stored_utc(generation.fresh_until) <= at):
                raise InventoryGenerationExpired("Generation is no longer eligible for new views")
            until = utc(expires_at) if expires_at is not None else at + VIEW_LIFETIME
            if at < stored_utc(generation.created_at) or until <= at or until > at + VIEW_LIFETIME:
                raise InventoryViewInvalid("Invalid view lifetime")
            if creator_user_id is not None and self.session.get(User, creator_user_id) is None:
                raise InventoryViewInvalid("Creator not found")
            for _ in range(4):
                token = self.token_factory()
                if re.fullmatch(TOKEN_PATTERN, token) is None:
                    raise InventoryViewInvalid("Invalid token factory")
                digest = hashlib.sha256(token.encode("ascii")).hexdigest()
                try:
                    with self.session.begin_nested():
                        self.session.execute(insert(View).values(id=uuid4().hex, token_digest=digest,
                            generation_id=generation_id, page_size=page_size, creator_user_id=creator_user_id,
                            created_at=at, expires_at=until))
                    return InventoryViewHandle(generation_id, token, page_size, until)
                except IntegrityError:
                    if self.session.scalar(select(View.id).where(View.token_digest == digest)) is None:
                        raise
            raise InventoryCapacityExceeded("View token capacity exceeded")

    def read_view(self, token: str, now: datetime | None = None) -> InventoryViewInfo:
        if re.fullmatch(TOKEN_PATTERN, token) is None:
            raise InventoryViewInvalid("Invalid view token")
        with self._transaction():
            view = self.session.scalar(select(View).where(View.token_digest == hashlib.sha256(token.encode("ascii")).hexdigest()))
            if view is None:
                raise InventoryViewInvalid("View not found")
            generation = self._generation(view.generation_id)
            if stored_utc(view.expires_at) <= self._now(now) or generation.status == "expired":
                raise InventoryGenerationExpired("View expired")
            return InventoryViewInfo(generation.id, generation.status, view.page_size, stored_utc(view.expires_at))

    def read_items(self, generation_id: str, *, start: int = 0, limit: int = 10) -> tuple[InventoryItemInput, ...]:
        """Internal bounded read, not an authorization or public page API."""
        if type(start) is not int or not 0 <= start <= MAX_ITEMS or type(limit) is not int or not 1 <= limit <= 10:
            raise InventoryViewInvalid("Invalid item range")
        with self._transaction():
            generation = self._generation(generation_id)
            if generation.status == "expired":
                raise InventoryGenerationExpired("Generation expired")
            if generation.status != "ready":
                raise InventoryGenerationConflict("Snapshot is not published")
            rows = self.session.scalars(select(Item).where(Item.generation_id == generation_id, Item.ordinal >= start)
                                        .order_by(Item.ordinal).limit(limit))
            return tuple(InventoryItemInput.model_validate({"snapshot": row.snapshot, "remote_evidence": row.remote_evidence})
                         for row in rows)
