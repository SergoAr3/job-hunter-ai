"""Explicit short DB transactions around a cancellable, transaction-free scan."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Literal, TypeVar, cast
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models import DiscoverFeedChain as Chain, DiscoverFeedState as State, User, UserProfile
from app.services.discover import _discover_item, _identity_key, _saved_applications
from app.services.discover_feed_scan import FeedScanner, ScanStats
from app.services.discover_feed_types import (
    CHAIN_TTL_SECONDS, CLAIM_SECONDS, CLEANUP_BATCH, MAX_ACTIVE_CHAINS, MAX_CHAIN_PAGES,
    MAX_CHAIN_STATES, TOKEN_ATTEMPTS, TOKEN_PATTERN, VERSION,
    FeedError, FeedIdentity, FeedOut, FeedStart, StoredFeed,
)

logger = logging.getLogger(__name__)
# Keep expired tokens distinguishable for a day before bounded physical purge.
PURGE_AFTER_SECONDS = 86400
DB_LOCK_TIMEOUT_MS = 1000
DB_STATEMENT_TIMEOUT_MS = 3000
SessionFactory = Callable[[], Session]
PhaseResult = TypeVar("PhaseResult")


class _PhaseCancelled(Exception):
    """Rollback a worker transaction when cancellation arrived before commit."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class Snapshot:
    token: str
    identity: FeedIdentity
    stored: StoredFeed
    expires_at: datetime
    prev: str | None
    next: str | None
    result: Literal["page", "paused", "end"] | None
    fence: str | None = None
    next_action: Literal["next", "continue"] | None = None


class FeedService:
    def __init__(self, scanner: FeedScanner, *, clock: Callable[[], datetime] = utcnow,
                 token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24)):
        self.scanner, self.clock, self.token_factory = scanner, clock, token_factory

    @contextmanager
    def transaction(self, session: Session, *, write: bool = True) -> Iterator[None]:
        # Entry points must end authentication's read transaction explicitly.
        if session.in_transaction():
            raise RuntimeError("Feed transaction requires an idle session")
        def check_cancelled() -> None:
            cancelled = session.info.get("feed_cancelled")
            if cancelled is not None and cancelled.is_set():
                raise _PhaseCancelled()

        try:
            with session.begin():
                check_cancelled()
                dialect = session.get_bind().dialect.name
                if dialect == "postgresql":
                    # Transaction-local only; reset by commit/rollback and never
                    # change another route's pooled-connection configuration.
                    session.execute(text("SELECT set_config('lock_timeout', :lock, true), "
                                         "set_config('statement_timeout', :statement, true)"),
                                    {"lock": f"{DB_LOCK_TIMEOUT_MS}ms", "statement": f"{DB_STATEMENT_TIMEOUT_MS}ms"})
                elif write and dialect == "sqlite":
                    connection = session.connection()
                    driver = connection.connection.driver_connection
                    if driver is not None and not driver.in_transaction:
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                yield
                check_cancelled()
                if write:
                    session.flush()  # Flush can itself wait on a row lock.
                check_cancelled()  # Last safe rollback point before COMMIT.
        except DBAPIError as error:
            error.hide_parameters = True  # Cursor/evidence must not enter SQL tracebacks.
            code = getattr(error.orig, "sqlstate", None)
            if isinstance(error, OperationalError) and code == "55P03":
                raise FeedError("FEED_DB_LOCK_TIMEOUT", 409) from None
            if isinstance(error, OperationalError) and code == "57014":
                raise FeedError("FEED_DB_QUERY_TIMEOUT", 503) from None
            raise

    async def db_phase(
        self, sessions: SessionFactory, operation: Callable[..., PhaseResult], *args,
        on_cancel: Callable[[Session, PhaseResult], None] | None = None, cleanup: bool = False,
    ) -> PhaseResult:
        """Session creation, all SQL/commit/rollback and close stay in one worker."""
        cancelled = Event()

        def execute() -> PhaseResult:
            with sessions() as session:
                session.info["feed_cancelled"] = cancelled
                return operation(session, *args)

        task = asyncio.create_task(run_in_threadpool(execute))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not cleanup:
                cancelled.set()
            # A sync DB call cannot be force-cancelled. Join it, including repeated
            # task cancellation; timeout/rollback/session close still run inside it.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if cleanup:
                return task.result()
            try:
                result = task.result()
            except Exception:
                pass  # Retrieve the worker error, preserving caller cancellation.
            else:
                if on_cancel is not None:
                    # Cancellation may race COMMIT or result delivery. A committed
                    # claim must be conditionally released before the caller leaves.
                    try:
                        await self.db_phase(sessions, on_cancel, result, cleanup=True)
                    except Exception:
                        logger.warning("discover_feed action=cancel_release_failed recovery=lease")
            raise

    def cleanup(self, session: Session, now: datetime) -> None:
        # Indexed expiry lookup; no worker or unbounded delete. Caller owns tx.
        cutoff = now - timedelta(seconds=PURGE_AFTER_SECONDS)
        ids = list(session.scalars(select(Chain.id).where(Chain.expires_at <= cutoff)
                                   .order_by(Chain.expires_at).limit(CLEANUP_BATCH)
                                   .with_for_update(skip_locked=True)))
        if ids:
            session.execute(delete(Chain).where(Chain.id.in_(ids)))

    def new_state(self, session: Session, chain_id: str, stored: StoredFeed, prev: str | None) -> State:
        payload = stored.encoded()
        for _ in range(TOKEN_ATTEMPTS):
            token = self.token_factory()
            if re.fullmatch(TOKEN_PATTERN, token) is None:
                raise RuntimeError("Invalid server token factory")
            try:
                with session.begin_nested():
                    # Core insert avoids ORM identity-conflict warnings (which
                    # include the full token) on an injected/real collision.
                    session.execute(insert(State).values(token=token, chain_id=chain_id,
                        status="ready", payload=payload, prev_token=prev))
                node = session.get(State, token)
                if node is None:
                    raise RuntimeError("Inserted feed state disappeared")
                return node
            except IntegrityError:
                # Retry only an actual token collision, not a broken FK/schema.
                if session.get(State, token) is None:
                    raise
        raise FeedError("FEED_TOKEN_CAPACITY", 503)

    def start(self, session: Session, user_id: int, request: FeedStart) -> str:
        identity = FeedIdentity(**request.model_dump())
        identity.query = identity.query.strip()
        if not identity.query:
            raise FeedError("INVALID_DISCOVER_QUERY", 422)
        now = self.clock()
        with self.transaction(session):
            # Per-user quota check and insertion serialized only for this tx.
            user = session.scalar(select(User.id).where(User.id == user_id).with_for_update())
            if user is None:
                raise FeedError("USER_NOT_FOUND", 404)
            self.cleanup(session, now)
            active = session.scalar(select(func.count()).select_from(Chain).where(Chain.creator_id == user_id, Chain.expires_at > now)) or 0
            if active >= MAX_ACTIVE_CHAINS:
                raise FeedError("FEED_CHAIN_CAPACITY", 429)
            chain = Chain(id=uuid4().hex, creator_id=user_id, version=VERSION,
                          identity_json=identity.model_dump_json(), created_at=now,
                          expires_at=now + timedelta(seconds=CHAIN_TTL_SECONDS))
            session.add(chain)
            session.flush()
            token = self.new_state(session, chain.id, StoredFeed(), None).token
        self.log("start", token)
        return token

    def load(self, session: Session, token: str, *, lock: bool = False) -> tuple[Chain, State, Snapshot]:
        if re.fullmatch(TOKEN_PATTERN, token) is None:
            raise FeedError("FEED_CURSOR_MALFORMED", 422)
        # Chain is always locked before state, consistently in claim/finalize.
        statement = select(Chain).join(State, State.chain_id == Chain.id).where(State.token == token)
        if lock:
            statement = statement.with_for_update(of=Chain)
        chain = session.scalar(statement.execution_options(populate_existing=True))
        if chain is None:
            raise FeedError("FEED_CURSOR_UNKNOWN", 404)
        if aware(chain.expires_at) <= self.clock():
            raise FeedError("FEED_CURSOR_EXPIRED", 410)
        if chain.version != VERSION:
            raise FeedError("FEED_VERSION_UNSUPPORTED")
        node = session.get(State, token, populate_existing=True)
        if node is None:
            raise FeedError("FEED_STATE_INVALID")
        outcome = self.snapshot(chain, node)
        links = [value for value in (node.prev_token, node.next_token) if value is not None]
        if links:
            linked = list(session.scalars(select(State).where(State.token.in_(links))))
            if len(linked) != len(set(links)) or any(item.chain_id != chain.id or item.token == token for item in linked):
                raise FeedError("FEED_STATE_INVALID")
            previous = next((item for item in linked if item.token == node.prev_token), None)
            if previous is not None and (previous.status != "complete" or previous.result_state != "page"):
                raise FeedError("FEED_STATE_INVALID")
        return chain, node, outcome

    @staticmethod
    def snapshot(chain: Chain, node: State) -> Snapshot:
        """Validate evidence consistently for direct loads and navigation hops."""
        if any(re.fullmatch(TOKEN_PATTERN, token) is None for token in
               (node.token, node.prev_token, node.next_token) if token is not None):
            raise FeedError("FEED_STATE_INVALID")
        try:
            identity = FeedIdentity.model_validate_json(chain.identity_json)
            raw = json.loads(node.payload)
            if not isinstance(raw, dict) or set(raw) != {"version", "checkpoint", "items"}:
                raise FeedError("FEED_STATE_INVALID")
            if raw["version"] != VERSION:
                raise FeedError("FEED_VERSION_UNSUPPORTED")
            stored = StoredFeed.model_validate(raw)
        except (ValidationError, ValueError) as error:
            raise FeedError("FEED_STATE_INVALID") from error
        if (node.status not in {"ready", "claimed", "complete"}
                or (node.status == "complete" and (
                    node.result_state not in {"page", "paused", "end"}
                    or (node.result_state == "page") != bool(stored.items)
                    or len(stored.items) > identity.limit))
                or (node.status != "complete" and (node.result_state is not None or stored.items))):
            raise FeedError("FEED_STATE_INVALID")
        if stored.checkpoint.offset % 10 or any(item.workplace_type != "remote" for item in stored.items):
            raise FeedError("FEED_STATE_INVALID")
        terminal = stored.checkpoint.exhausted and not stored.checkpoint.remainder
        if ((node.result_state == "end" and (node.next_token is not None or not terminal))
                or (node.result_state == "paused" and (node.next_token is None or terminal))
                or (node.result_state == "page" and (node.next_token is None) != terminal)
                or (node.status != "complete" and node.next_token is not None)
                or ((node.status == "claimed") != (node.lease_token is not None and node.lease_until is not None))
                or (node.status != "claimed" and (node.lease_token is not None or node.lease_until is not None))):
            raise FeedError("FEED_STATE_INVALID")
        result = cast(Literal["page", "paused", "end"] | None, node.result_state)
        action = ("continue" if result == "paused" else "next") if node.next_token is not None else None
        return Snapshot(node.token, identity, stored, aware(chain.expires_at), node.prev_token, node.next_token,
                        result, next_action=action)

    def navigation(self, session: Session, chain: Chain, origin: Snapshot) -> Snapshot:
        """Collapse committed paused transitions without changing stored links."""
        token, action = origin.next, origin.next_action
        previous = origin.token if origin.result == "page" else origin.prev
        visited = {origin.token}
        # The origin and every visited descendant count toward the chain cap.
        for _ in range(MAX_CHAIN_STATES - 1):
            if token is None:
                return replace(origin, next=None, next_action=None)
            if token in visited:
                raise FeedError("FEED_STATE_INVALID")
            visited.add(token)
            node = session.get(State, token, populate_existing=True)
            if node is None or node.chain_id != chain.id or node.prev_token != previous:
                raise FeedError("FEED_STATE_INVALID")
            step = self.snapshot(chain, node)
            if step.result == "end":
                return replace(origin, next=None, next_action=None)
            if step.result != "paused":
                # Ready/claimed is actionable; a completed page is recovered by
                # idempotent POST of this same token, never skipped on GET.
                return replace(origin, next=token, next_action=action)
            token, action = step.next, "continue"
        raise FeedError("FEED_STATE_INVALID")

    def claim(self, session: Session, token: str) -> Snapshot:
        with self.transaction(session):
            chain, node, snapshot = self.load(session, token, lock=True)
            now = self.clock()  # Lock waiting must not consume the new lease.
            if node.status == "complete":
                return snapshot
            if node.status == "claimed" and node.lease_until is not None and aware(node.lease_until) > now:
                self.log("claim_busy", token)
                raise FeedError("FEED_ADVANCE_IN_PROGRESS")
            if chain.page_count >= MAX_CHAIN_PAGES or chain.state_count >= MAX_CHAIN_STATES:
                raise FeedError("FEED_STATE_CAPACITY", 429)
            reclaim = node.status == "claimed"
            fence = uuid4().hex
            node.status, node.lease_token = "claimed", fence
            node.lease_until = now + timedelta(seconds=CLAIM_SECONDS)
            self.cleanup(session, now)
            claimed = Snapshot(**{**snapshot.__dict__, "fence": fence})
        self.log("reclaim" if reclaim else "claim", token)
        return claimed

    def finalize(self, session: Session, claimed: Snapshot, stored: StoredFeed) -> Snapshot:
        payload = stored.encoded()
        with self.transaction(session):
            chain, node, _ = self.load(session, claimed.token, lock=True)
            if (node.status != "claimed" or node.lease_token != claimed.fence
                    or node.lease_until is None or aware(node.lease_until) <= self.clock()):
                raise FeedError("FEED_CLAIM_FENCED")
            if len(stored.items) > claimed.identity.limit:
                raise FeedError("FEED_STATE_INVALID")
            exhausted = stored.checkpoint.exhausted and not stored.checkpoint.remainder
            result = "page" if stored.items else "end" if exhausted else "paused"
            if result == "page":
                if chain.page_count >= MAX_CHAIN_PAGES:
                    raise FeedError("FEED_STATE_CAPACITY", 429)
                chain.page_count += 1
            if not exhausted:
                if chain.state_count >= MAX_CHAIN_STATES:
                    raise FeedError("FEED_STATE_CAPACITY", 429)
                child = self.new_state(session, chain.id, StoredFeed(checkpoint=stored.checkpoint),
                                       node.token if result == "page" else node.prev_token)
                chain.state_count += 1
                node.next_token = child.token
            node.payload, node.result_state, node.status = payload, result, "complete"
            node.lease_token = node.lease_until = None
            session.flush()
            _, _, outcome = self.load(session, claimed.token)
        return outcome

    def release(self, session: Session, claimed: Snapshot) -> None:
        with self.transaction(session):
            session.execute(update(State).where(State.token == claimed.token,
                State.status == "claimed", State.lease_token == claimed.fence)
                .values(status="ready", lease_token=None, lease_until=None))

    async def advance(self, sessions: SessionFactory, token: str) -> Snapshot:
        started = time.monotonic()
        claimed = await self.db_phase(sessions, self.claim, token, on_cancel=self.release)
        if claimed.result is not None:
            self.log("repeat", token, result=claimed.result, returned=len(claimed.stored.items))
            return claimed
        stats = ScanStats()
        try:
            # The claim worker has closed its Session/returned its connection.
            stored = await self.scanner.scan(claimed.identity, claimed.stored.checkpoint, stats)
            outcome = await self.db_phase(sessions, self.finalize, claimed, stored)
        except BaseException as error:
            try:
                await self.db_phase(sessions, self.release, claimed, cleanup=True)
            except Exception as cleanup_error:
                error.add_note(f"Claim release failed ({type(cleanup_error).__name__}); lease recovery applies")
            self.log("advance", token, result="error", stats=stats, elapsed=time.monotonic() - started)
            raise
        self.log("advance", token, result=outcome.result, stats=stats, elapsed=time.monotonic() - started, returned=len(outcome.stored.items))
        return outcome

    def replay(self, session: Session, token: str) -> Snapshot:
        with self.transaction(session, write=False):
            chain, node, outcome = self.load(session, token)
            if node.status != "complete":
                raise FeedError("FEED_CURSOR_NOT_MATERIALIZED")
            outcome = self.navigation(session, chain, outcome)
        self.log("replay", token, result=outcome.result, returned=len(outcome.stored.items))
        return outcome

    def response(self, session: Session, user_id: int, outcome: Snapshot) -> FeedOut:
        # Fresh user-specific reads only after continuation locks are released.
        vacancies = [item.external() for item in outcome.stored.items]
        with self.transaction(session, write=False):
            if session.get(User, user_id) is None:
                raise FeedError("USER_NOT_FOUND", 404)
            profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id).execution_options(populate_existing=True))
            saved = _saved_applications(session, user_id, vacancies)
            items = [_discover_item(item, profile, saved.get(_identity_key(item))) for item in vacancies]
        if outcome.result is None:
            raise FeedError("FEED_CURSOR_NOT_MATERIALIZED")
        return FeedOut(items=items, returned_count=len(items), cursor=outcome.token,
                       prev_cursor=outcome.prev, next_cursor=outcome.next,
                       next_action=outcome.next_action, state=outcome.result, expires_at=outcome.expires_at)

    @staticmethod
    def log(action: str, token: str, *, result: str | None = None, stats: ScanStats | None = None, elapsed: float = 0, returned: int = 0) -> None:
        stats = stats or ScanStats()
        logger.info("discover_feed action=%s cursor_hash=%s result=%s pages=%d records=%d hits=%d cold=%s returned_count=%s elapsed=%.3f",
                    action, hashlib.sha256(token.encode()).hexdigest()[:12], result,
                    stats.pages, stats.records, stats.hits, stats.cold, returned, elapsed)
