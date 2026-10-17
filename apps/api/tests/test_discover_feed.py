import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.database import get_session
from app.discover_feed_routes import get_feed_service
from app.main import app
from app.models import DiscoverFeedChain as Chain, DiscoverFeedState as State, Application, Job, User
from app.services import auth
from app.services.discover_feed import FeedService
from app.services.discover_feed_scan import FeedScanner
from app.services.discover_feed_types import FeedError, FeedStart, StoredFeed
from app.services.source_deadline import SourceDeadlineExceeded
from app.services.vacancy_sources import SourceError
from conftest import TestSessionLocal, client, engine
from source_deadline_helpers import run
from test_discover_api import create_user, put_profile, vacancy
from test_discover_feed_scan import Cache, Provider, page

START = {"query": "Backend", "market_country": "RU", "remote_only": True}
NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


@pytest.fixture
def feed():
    now = [NOW]
    cache, provider = Cache(), Provider()
    service = FeedService(FeedScanner(cache, provider), clock=lambda: now[0])
    app.dependency_overrides[get_feed_service] = lambda: service
    user_id = create_user(700)
    try:
        yield user_id, service, cache, provider, now
    finally:
        app.dependency_overrides.pop(get_feed_service, None)


def start(user_id, **changes):
    return client.post(f"/users/{user_id}/discover/feed", json={**START, **changes})


def advance(user_id, token):
    return client.post(f"/users/{user_id}/discover/feed", json={"cursor": token})


def replay(user_id, token):
    return client.get(f"/users/{user_id}/discover/feed", params={"cursor": token})


def test_navigation_replay_previous_stable_after_cache_expiry(feed):
    user_id, service, cache, provider, now = feed
    first = start(user_id, limit=2)
    assert first.status_code == 200
    first = first.json()
    assert first["state"] == "page" and first["returned_count"] == 2
    second = advance(user_id, first["next_cursor"]).json()
    assert second["prev_cursor"] == first["cursor"]
    assert [x["external_id"] for x in second["items"]] == ["2", "3"]
    cache._entries.clear()
    provider.error = AssertionError("replay cannot access source")
    now[0] += timedelta(minutes=20)
    assert replay(user_id, first["cursor"]).json() == first
    assert replay(user_id, second["cursor"]).json() == second
    assert replay(user_id, second["prev_cursor"]).json() == first
    assert advance(user_id, first["cursor"]).json() == first  # committed result repeat
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]
    assert set(first) == {"items", "returned_count", "cursor", "prev_cursor", "next_cursor", "next_action", "state", "expires_at"}


def test_same_advance_response_lost_repeat_returns_same_cursor_no_scan(feed):
    user_id, service, cache, provider, _ = feed
    first = start(user_id, limit=2).json()
    token = first["next_cursor"]
    lost = advance(user_id, token).json()
    assert advance(user_id, token).json() == lost
    assert lost["cursor"] == token
    with TestSessionLocal() as session:
        assert session.query(State).count() == 3  # root, next outcome, pending child
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


def test_paused_repeats_then_separate_next_advances_without_empty_prev_page(feed):
    user_id, service, cache, provider, _ = feed
    provider.pages = {0: page(), 10: page(10, remote=[0], total=20)}
    paused = start(user_id).json()
    assert paused["state"] == "paused" and paused["items"] == [] and paused["prev_cursor"] is None
    before = (list(cache.fresh_reads), list(cache.cold_loads), list(provider.calls))
    assert advance(user_id, paused["cursor"]).json() == paused
    assert (cache.fresh_reads, cache.cold_loads, provider.calls) == before
    result = advance(user_id, paused["next_cursor"]).json()
    assert result["state"] == "page" and result["prev_cursor"] is None and result["next_cursor"] is None
    assert cache.fresh_reads == [0, 10, 10] and cache.cold_loads == provider.calls == [0, 10]


def test_paused_checkpoint_preserves_last_visible_previous(feed):
    user_id, service, cache, provider, _ = feed
    provider.pages = {0: page(remote=[0]), 10: page(10), 20: page(20, remote=[0])}
    first = start(user_id, limit=1).json()
    paused = advance(user_id, first["next_cursor"]).json()
    assert paused["state"] == "paused" and paused["prev_cursor"] == first["cursor"]
    following = advance(user_id, paused["next_cursor"]).json()
    assert following["state"] == "page" and following["prev_cursor"] == first["cursor"]
    assert provider.calls == cache.cold_loads == [0, 10, 20]
    assert cache.fresh_reads == [0, 10, 20, 20]


def test_confirmed_end_is_stable_and_has_no_next(feed):
    user_id, service, cache, provider, _ = feed
    provider.pages = {0: page(total=0, count=0)}
    result = start(user_id).json()
    assert result["state"] == "end" and result["next_cursor"] is None and result["items"] == []
    assert advance(user_id, result["cursor"]).json() == replay(user_id, result["cursor"]).json() == result
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


@pytest.mark.parametrize("failure,status", [(SourceDeadlineExceeded(), 504), (SourceError(), 503), (TimeoutError(), 504)])
def test_whole_advance_error_retry_checkpoint_unchanged_and_old_page_replayable(feed, failure, status):
    user_id, service, cache, provider, _ = feed
    provider.pages = {0: page(remote=[0, 1])}
    first = start(user_id, limit=2).json()
    token = first["next_cursor"]
    cache.seed(page(10, remote=[0]))
    provider.error = failure
    response = advance(user_id, token)
    assert response.status_code == status
    assert response.json()["detail"]["retry_cursor"] == token
    with TestSessionLocal() as session:
        node = session.get(State, token)
        assert node.status == "ready" and node.lease_token is None
        checkpoint = StoredFeed.model_validate_json(node.payload).checkpoint
        assert checkpoint.offset == 10 and len(checkpoint.remainder) == 8
    assert replay(user_id, first["cursor"]).json() == first
    provider.error = None
    provider.pages[20] = page(20, remote=[0], total=30)
    retried = advance(user_id, token)
    assert retried.status_code == 200 and retried.json()["items"][0]["external_id"] == "10"
    assert cache.cold_loads == provider.calls == [0, 20, 20]
    assert cache.fresh_reads == [0, 10, 20, 10, 20]


def test_replay_current_application_and_profile_and_other_authenticated_user(feed):
    user_id, service, cache, provider, _ = feed
    provider.pages = {0: page(remote=[0]), 10: page(10)}
    first = start(user_id, limit=1).json()
    token = first["cursor"]
    paused = advance(user_id, first["next_cursor"]).json()
    assert paused["state"] == "paused"
    other = create_user(701)
    put_profile(other)
    with TestSessionLocal() as session:
        item = vacancy("0", workplace_type="remote")
        job = Job(source="trudvsem", ingestion_method="discover", source_url=item.source_url,
                  external_id=item.external_id, source_scope=item.source_scope)
        session.add(job)
        session.flush()
        application = Application(user_id=other, job_id=job.id, status="saved")
        session.add(application)
        auth.issue_session(session, other)
        session.commit()
        application_id = application.id
        login = auth.issue_session(session, other)
        session.commit()
        bearer = {"Authorization": "Bearer " + login.session_token}
    plain = TestClient(app)
    assert plain.get(f"/users/{other}/discover/feed", params={"cursor": token}).status_code == 401
    assert plain.get(f"/users/{user_id}/discover/feed", params={"cursor": token}, headers=bearer).status_code == 404
    current = plain.get(f"/users/{other}/discover/feed", params={"cursor": token}, headers=bearer).json()
    owner = replay(user_id, token).json()
    assert current["next_cursor"] == owner["next_cursor"] == paused["next_cursor"]
    assert current["next_action"] == owner["next_action"] == "continue"
    assert current["items"][0]["application_id"] == application_id
    assert current["items"][0]["preview_match"]["available"] is True
    assert first["items"][0]["preview_match"]["available"] is False
    with TestSessionLocal() as session:
        session.get(Application, application_id).status = "applied"
        session.commit()
    assert replay(other, token).json()["items"][0]["application_status"] == "applied"
    assert replay(user_id, token).json()["items"][0]["application_id"] is None
    put_profile(user_id)
    assert replay(user_id, token).json()["items"][0]["preview_match"]["available"] is True
    with TestSessionLocal() as session:
        raw = " ".join(node.payload for node in session.scalars(select(State)))
        for forbidden in ("preview_match", "application_id", "application_status", "profile", "already_saved", "score"):
            assert forbidden not in raw
    assert cache.cold_loads == provider.calls == [0, 10]
    assert cache.fresh_reads == [0, 10, 20]


@pytest.mark.parametrize("method", ["replay", "advance", "previous"])
def test_fixed_expiry_is_410_in_all_navigation_modes(feed, method):
    user_id, service, cache, provider, now = feed
    first = start(user_id, limit=1).json()
    second = advance(user_id, first["next_cursor"]).json()
    now[0] += timedelta(minutes=30)
    token = second["prev_cursor"] if method == "previous" else second["cursor"]
    result = advance(user_id, token) if method == "advance" else replay(user_id, token)
    assert result.status_code == 410 and result.json()["detail"]["code"] == "FEED_CURSOR_EXPIRED"
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


@pytest.mark.parametrize("token,status,code", [("!" * 32, 422, None), ("x" * 1000, 422, None), ("x" * 32, 404, "FEED_CURSOR_UNKNOWN")])
def test_client_invalid_tokens_controlled(feed, token, status, code):
    user_id, service, cache, provider, _ = feed
    for response in (replay(user_id, token), advance(user_id, token)):
        assert response.status_code == status
        if code:
            assert response.json()["detail"]["code"] == code
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


@pytest.mark.parametrize("changes", [{"remote_only": False}, {"market_country": "AM"}, {"market_region": "bad-region"}, {"provider": "evil"}, {"query": " "}, {"limit": 11}])
def test_invalid_identity_rejected_without_source(feed, changes):
    user_id, service, cache, provider, _ = feed
    assert start(user_id, **changes).status_code == 422
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_malformed_source_error_is_not_a_committed_end_or_paused(feed):
    user_id, service, cache, provider, _ = feed
    provider.pages = {0: page(total=0, count=1)}
    response = start(user_id)
    assert response.status_code == 502 and response.json()["detail"]["code"] == "source_bad_response"
    with TestSessionLocal() as session:
        node = session.get(State, response.json()["detail"]["retry_cursor"])
        assert node.status == "ready" and node.result_state is None and node.next_token is None
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


def test_cursor_does_not_accept_identity_override_or_get_pending(feed):
    user_id, service, cache, provider, _ = feed
    first = start(user_id, limit=1).json()
    assert client.post(f"/users/{user_id}/discover/feed", json={"cursor": first["next_cursor"], "query": "another"}).status_code == 422
    pending = replay(user_id, first["next_cursor"])
    assert pending.status_code == 409 and pending.json()["detail"]["code"] == "FEED_CURSOR_NOT_MATERIALIZED"
    assert client.get(f"/users/{user_id}/discover/feed", params={"cursor": first["cursor"], "query": "other"}).status_code == 422
    assert client.post(f"/users/{user_id}/discover/feed?query=other", json={"cursor": first["cursor"]}).status_code == 422
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


@pytest.mark.parametrize("corruption", ["version", "json", "payload_version", "wrong_items", "cross_chain"])
def test_incompatible_and_corrupted_state_is_controlled(feed, corruption):
    user_id, service, cache, provider, _ = feed
    first = start(user_id, limit=1).json()
    with TestSessionLocal() as session:
        node = session.get(State, first["cursor"])
        if corruption == "version":
            session.get(Chain, node.chain_id).version = 2
        elif corruption == "json":
            node.payload = "{}"
        elif corruption == "payload_version":
            raw = json.loads(node.payload)
            raw["version"] = 2
            node.payload = json.dumps(raw)
        elif corruption == "wrong_items":
            raw = json.loads(node.payload)
            raw["items"] = []
            node.payload = json.dumps(raw)
        else:
            session.rollback()
            other = service.start(session, user_id, FeedStart(**START))
            session.get(State, first["cursor"]).next_token = other
        session.commit()
    response = replay(user_id, first["cursor"])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == ("FEED_VERSION_UNSUPPORTED" if corruption in {"version", "payload_version"} else "FEED_STATE_INVALID")
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


def test_token_collision_retry_capacity_and_no_partial_chain(feed):
    user_id, service, cache, provider, _ = feed
    with TestSessionLocal() as session:
        first = service.start(session, user_id, FeedStart(**START))
        tokens = iter([first, "n" * 32])
        service.token_factory = lambda: next(tokens)
        assert service.start(session, user_id, FeedStart(**START)) == "n" * 32
        service.token_factory = lambda: first
        with pytest.raises(FeedError, match="") as error:
            service.start(session, user_id, FeedStart(**START))
        assert error.value.code == "FEED_TOKEN_CAPACITY" and not session.in_transaction()
        assert session.query(Chain).count() == 2 and session.query(State).count() == 2
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_chain_and_page_caps_are_controlled_no_eviction(feed):
    user_id, service, cache, provider, _ = feed
    with TestSessionLocal() as session:
        tokens = [service.start(session, user_id, FeedStart(**START)) for _ in range(20)]
        with pytest.raises(FeedError) as error:
            service.start(session, user_id, FeedStart(**START))
        assert error.value.code == "FEED_CHAIN_CAPACITY"
        node = session.get(State, tokens[0])
        session.get(Chain, node.chain_id).page_count = 32
        session.commit()
    assert advance(user_id, tokens[0]).status_code == 429
    with TestSessionLocal() as session:
        assert session.query(Chain).count() == 20
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_state_cap_rejects_advance_and_token_uses_192_bit_factory(feed, monkeypatch):
    user_id, service, cache, provider, _ = feed
    calls = []
    def token_urlsafe(size):
        calls.append(size)
        return "z" * 32
    monkeypatch.setattr("app.services.discover_feed.secrets.token_urlsafe", token_urlsafe)
    with TestSessionLocal() as session:
        token = service.start(session, user_id, FeedStart(**START))
        assert calls == [24]
        node = session.get(State, token)
        session.get(Chain, node.chain_id).state_count = 64
        session.commit()
    assert advance(user_id, token).status_code == 429
    assert cache.fresh_reads == cache.cold_loads == provider.calls == []


def test_caller_cancellation_releases_claim_without_committing_partial_result(feed):
    import asyncio
    user_id, service, cache, provider, _ = feed
    async def scenario():
        provider.release = asyncio.Event()
        with TestSessionLocal() as session:
            token = service.start(session, user_id, FeedStart(**START))
            task = asyncio.create_task(service.advance(TestSessionLocal, token))
            await provider.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not session.in_transaction()
            with session.begin():
                node = session.get(State, token)
                assert node.status == "ready" and node.result_state is None
                assert node.lease_token is None
    run(scenario())
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


def test_evidence_size_error_leaves_recoverable_state(feed):
    from dataclasses import replace
    user_id, service, cache, provider, _ = feed
    provider.pages[0] = replace(provider.pages[0], items=[replace(vacancy("big", workplace_type="remote"), description="я" * 140_000)])
    response = start(user_id)
    assert response.status_code == 413 and response.json()["detail"]["code"] == "FEED_EVIDENCE_TOO_LARGE"
    with TestSessionLocal() as session:
        node = session.get(State, response.json()["detail"]["retry_cursor"])
        assert node.status == "ready" and node.lease_token is None
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


def test_logs_and_batch_enrichment_queries(feed, caplog):
    user_id, service, cache, provider, _ = feed
    caplog.set_level(logging.INFO, logger="app.services.discover_feed")
    result = start(user_id).json()
    statements = []
    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert replay(user_id, result["cursor"]).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    application_reads = [sql for sql in statements if "applications" in sql.lower()]
    assert len(application_reads) == 1  # identity and URL fallback in one batch
    assert result["cursor"] not in caplog.text and "Backend" not in caplog.text
    assert "cold=True" in caplog.text and "returned_count=10" in caplog.text
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]
