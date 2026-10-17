"""Opt-in feed API; legacy offset routes and clients are unchanged."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import ASGIApp, Receive, Scope, Send

from app.database import get_session
from app.services.discover_feed import FeedService, SessionFactory
from app.services.discover_feed_scan import FeedScanner
from app.services.discover_feed_types import TOKEN_PATTERN, FeedAdvance, FeedError, FeedOut, FeedStart
from app.services.trudvsem_deadline import TrudvsemDeadlineProvider
from app.services.vacancy_sources import SourceError

router = APIRouter(prefix="/users/{user_id}/discover/feed", tags=["discover-feed"])


class FeedAccessLogRedaction:
    """Separate Uvicorn's logging scope from the router's real request scope."""
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        parts = scope.get("path", "").rstrip("/").split("/")
        if scope["type"] != "http" or len(parts) != 5 or parts[1] != "users" or parts[3:] != ["discover", "feed"]:
            await self.app(scope, receive, send)
            return
        request_scope = dict(scope)
        scope["query_string"] = b""  # Original scope is also used by outer 500/access logging.
        await self.app(request_scope, receive, send)


def get_feed_sessions(session: Session = Depends(get_session)) -> SessionFactory:
    # This sync dependency runs after existing authorization, in FastAPI's worker
    # pool. End its reads before source await; domain phases never reuse it.
    bind = session.get_bind()
    session.rollback()
    session.close()
    if not isinstance(bind, Engine):
        raise RuntimeError("Feed requires an engine-bound session factory")
    return sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)


def get_feed_service() -> FeedService:
    # Resolve the shared process-local PR1 cache, also used by legacy Discover.
    from app.main import discover_source_cache
    return FeedService(FeedScanner(discover_source_cache, TrudvsemDeadlineProvider()))


def feed_error(error: Exception, token: str | None = None) -> HTTPException:
    if isinstance(error, FeedError):
        status, code = error.status, error.code
    elif isinstance(error, SourceError):
        status = {"source_timeout": 504, "source_rate_limited": 429, "source_bad_response": 502}.get(error.code, 503)
        code = error.code
    else:
        status, code = 504, "source_timeout"
    detail = {"code": code}
    # Failed Start has no prior client token. Explicit retry never restarts search.
    if token is not None:
        detail["retry_cursor"] = token
    headers = {"Cache-Control": "no-store"}
    if code in {"FEED_ADVANCE_IN_PROGRESS", "FEED_DB_LOCK_TIMEOUT", "FEED_DB_QUERY_TIMEOUT"}:
        headers["Retry-After"] = "1"
    return HTTPException(status, detail=detail, headers=headers)


@router.post("", response_model=FeedOut)
async def advance_feed(user_id: int, payload: FeedStart | FeedAdvance, response: Response, request: Request,
                       sessions: SessionFactory = Depends(get_feed_sessions), service: FeedService = Depends(get_feed_service)) -> FeedOut:
    response.headers["Cache-Control"] = "no-store"
    token = payload.cursor if isinstance(payload, FeedAdvance) else None
    try:
        if request.query_params:
            raise FeedError("FEED_PARAMS_INVALID", 422)
        if isinstance(payload, FeedStart):
            token = await service.db_phase(sessions, service.start, user_id, payload)
        assert token is not None
        outcome = await service.advance(sessions, token)
        return await service.db_phase(sessions, service.response, user_id, outcome)
    except (FeedError, SourceError, TimeoutError) as error:
        raise feed_error(error, token) from None


@router.get("", response_model=FeedOut)
async def replay_feed(user_id: int, cursor: Annotated[str, Query(min_length=32, max_length=32, pattern=TOKEN_PATTERN)],
                response: Response, request: Request, sessions: SessionFactory = Depends(get_feed_sessions), service: FeedService = Depends(get_feed_service)) -> FeedOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        if set(request.query_params) != {"cursor"} or len(request.query_params.getlist("cursor")) != 1:
            raise FeedError("FEED_PARAMS_INVALID", 422)
        outcome = await service.db_phase(sessions, service.replay, cursor)
        return await service.db_phase(sessions, service.response, user_id, outcome)
    except FeedError as error:
        raise feed_error(error) from None
