"""Replay navigation comes from committed evidence, never another source action."""
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select

from app.models import DiscoverFeedState as State
from app.services.discover_feed_types import Checkpoint, Evidence, FeedOut, StoredFeed
from conftest import TestSessionLocal, engine
from test_discover_api import vacancy
from test_discover_feed import advance, feed, replay, start
from test_discover_feed_scan import page


def paused_history(context, count=1):
    user, service, cache, provider, _ = context
    provider.pages = {0: page(remote=[0]), **{offset: page(offset) for offset in range(10, (count + 1) * 10, 10)}}
    visible = start(user, limit=1).json()
    outcome = visible
    for _ in range(count):
        response = advance(user, outcome["next_cursor"])
        assert response.status_code == 200
        outcome = response.json()
        assert outcome["state"] == "paused" and outcome["next_action"] == "continue"
    return visible, outcome


def source_counts(context):
    _, _, cache, provider, _ = context
    return list(cache.fresh_reads), list(cache.cold_loads), list(provider.calls)


@pytest.mark.parametrize("count", [1, 2])
def test_one_replay_recovers_latest_paused_frontier_without_any_mutation_or_source(feed, monkeypatch, count):
    user, service, cache, provider, _ = feed
    visible, paused = paused_history(feed, count)
    before = source_counts(feed)
    with TestSessionLocal() as session:
        rows = list(session.execute(select(State.__table__).order_by(State.token)))
    def forbidden(*args, **kwargs):
        pytest.fail("Replay must not claim, advance, scan or access source/cache")
    for owner, name in ((service, "advance"), (service, "claim"), (service.scanner, "scan"),
                        (cache, "get_if_fresh"), (cache, "get_or_load"), (provider, "search_with_deadline")):
        monkeypatch.setattr(owner, name, forbidden)
    original = service.replay
    calls, statements = [], []
    def record_replay(session, token):
        calls.append(token)
        return original(session, token)
    monkeypatch.setattr(service, "replay", record_replay)
    def record_sql(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", record_sql)
    try:
        response = replay(user, visible["cursor"])
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)
    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed == {**visible, "next_cursor": paused["next_cursor"], "next_action": "continue"}
    assert calls == [visible["cursor"]]  # A single HTTP GET invokes replay exactly once.
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert source_counts(feed) == before
    with TestSessionLocal() as session:
        assert list(session.execute(select(State.__table__).order_by(State.token))) == rows


@pytest.mark.parametrize("paused_count", [0, 2])
def test_committed_end_clears_navigation_but_preserves_visible_page_and_post_repeat(feed, paused_count):
    user, service, cache, provider, _ = feed
    visible, previous = paused_history(feed, paused_count)
    provider.pages[(paused_count + 1) * 10] = page((paused_count + 1) * 10, total=(paused_count + 1) * 10, count=0)
    end = advance(user, previous["next_cursor"]).json()
    assert end["state"] == "end" and end["next_cursor"] is end["next_action"] is None
    before = source_counts(feed)
    assert replay(user, visible["cursor"]).json() == {**visible, "next_cursor": None, "next_action": None}
    assert replay(user, end["cursor"]).json() == end
    assert advance(user, end["cursor"]).json() == end
    if previous["state"] == "paused":
        # POST idempotency keeps its original continuation; GET navigation can evolve.
        assert advance(user, previous["cursor"]).json() == previous
        assert replay(user, previous["cursor"]).json()["next_action"] is None
    assert source_counts(feed) == before


@pytest.mark.parametrize("paused_count", [0, 2])
def test_completed_page_is_not_skipped_and_lost_post_response_remains_recoverable(feed, paused_count):
    user, service, cache, provider, _ = feed
    visible, previous = paused_history(feed, paused_count)
    offset = (paused_count + 1) * 10
    provider.pages[offset] = page(offset, remote=[0])
    following = advance(user, previous["next_cursor"]).json()
    assert following["state"] == "page" and following["prev_cursor"] == visible["cursor"]
    before = source_counts(feed)
    refreshed = replay(user, visible["cursor"]).json()
    assert refreshed == {**visible, "next_cursor": following["cursor"],
                         "next_action": "continue" if paused_count else "next"}
    assert advance(user, refreshed["next_cursor"]).json() == following
    assert replay(user, following["prev_cursor"]).json() == refreshed
    assert source_counts(feed) == before


@pytest.mark.parametrize("paused_count", [0, 2])
def test_claimed_frontier_does_not_wait_or_change_lease(feed, paused_count):
    user, service, cache, provider, _ = feed
    visible, previous = paused_history(feed, paused_count)
    with TestSessionLocal() as session:
        claimed = service.claim(session, previous["next_cursor"])
        node = session.get(State, claimed.token)
        lease = node.lease_token, node.lease_until
    before = source_counts(feed)
    response = replay(user, visible["cursor"])
    assert response.status_code == 200
    assert response.json() == {**visible, "next_cursor": claimed.token,
                               "next_action": "continue" if paused_count else "next"}
    with TestSessionLocal() as session:
        node = session.get(State, claimed.token)
        assert node.status == "claimed" and (node.lease_token, node.lease_until) == lease
    assert source_counts(feed) == before


def test_terminal_page_and_initial_paused_navigation(feed):
    user, service, cache, provider, _ = feed
    provider.pages = {0: page(total=1, count=1, remote=[0])}
    terminal = start(user, limit=1).json()
    assert terminal["state"] == "page" and terminal["items"]
    assert terminal["next_cursor"] is terminal["next_action"] is None
    before = source_counts(feed)
    assert replay(user, terminal["cursor"]).json() == terminal
    assert source_counts(feed) == before
    cache._entries.clear()
    provider.pages = {0: page(), 10: page(10)}
    initial = start(user).json()
    assert initial["state"] == "paused" and initial["prev_cursor"] is None
    second = advance(user, initial["next_cursor"]).json()
    assert second["state"] == "paused"
    before = source_counts(feed)
    assert replay(user, initial["cursor"]).json() == {**initial, "next_cursor": second["next_cursor"]}
    assert advance(user, initial["cursor"]).json() == initial
    assert source_counts(feed) == before


@pytest.mark.parametrize("corruption", ["cycle", "missing", "wrong_chain", "wrong_previous", "version", "payload", "terminal_paused", "no_next", "pending_link"])
def test_corrupted_paused_descendants_fail_with_controlled_error(feed, corruption):
    user, service, cache, provider, _ = feed
    visible, paused = paused_history(feed, 2)
    with TestSessionLocal() as session:
        first = session.get(State, visible["next_cursor"])
        last = session.get(State, first.next_token)
        target = session.get(State, paused["next_cursor"])
        if corruption == "cycle":
            last.next_token = first.token
        elif corruption == "missing":
            session.delete(target)  # FK SET NULL leaves a broken paused transition.
        elif corruption == "wrong_chain":
            # A real target exists but belongs to another valid chain.
            session.rollback()
            other = start(user, limit=1).json()
            session.get(State, paused["cursor"]).next_token = other["next_cursor"]
        elif corruption == "wrong_previous":
            last.prev_token = None
        elif corruption == "pending_link":
            target.next_token = first.token
        elif corruption in {"version", "payload", "terminal_paused"}:
            raw = json.loads(last.payload)
            if corruption == "version":
                raw["version"] = 2
            elif corruption == "payload":
                raw["items"] = [Evidence.model_validate(vacancy("bad", workplace_type="remote")).model_dump(mode="json")]
            else:
                raw["checkpoint"]["exhausted"] = True
                raw["checkpoint"]["remainder"] = []
            last.payload = json.dumps(raw)
        else:
            last.next_token = None
        session.commit()
    before = source_counts(feed)
    response = replay(user, visible["cursor"])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == ("FEED_VERSION_UNSUPPORTED" if corruption == "version" else "FEED_STATE_INVALID")
    assert source_counts(feed) == before


def test_maximum_valid_graph_is_resolved_and_oversized_graph_is_bounded(feed):
    user, service, cache, provider, _ = feed
    visible = start(user, limit=1).json()
    token = visible["next_cursor"]
    with TestSessionLocal() as session:
        for offset in range(20, 640, 10):  # 62 paused nodes + origin + pending = 64 states.
            claimed = service.claim(session, token)
            outcome = service.finalize(session, claimed, StoredFeed(checkpoint=Checkpoint(offset=offset)))
            token = outcome.next
        before = source_counts(feed)
        resolved = replay(user, visible["cursor"])
        assert resolved.status_code == 200
        assert resolved.json()["next_cursor"] == token and resolved.json()["next_action"] == "continue"
        assert source_counts(feed) == before
        # Deliberately corrupt storage beyond the real chain cap; do not change the bound.
        with service.transaction(session):
            pending = session.get(State, token)
            child = service.new_state(session, pending.chain_id, StoredFeed(checkpoint=Checkpoint(offset=640)), visible["cursor"])
            pending.status, pending.result_state, pending.next_token = "complete", "paused", child.token
        response = replay(user, visible["cursor"])
        assert response.status_code == 409 and response.json()["detail"]["code"] == "FEED_STATE_INVALID"


def test_normal_replay_query_count_is_small_and_action_enum_is_strict(feed):
    user, service, cache, provider, _ = feed
    visible = start(user, limit=1).json()
    statements = []
    def record(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        refreshed = replay(user, visible["cursor"])
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert refreshed.json() == visible and visible["next_action"] == "next"
    assert len(statements) <= 8  # Four graph reads plus existing user/profile/Application enrichment.
    assert len([sql for sql in statements if "applications" in sql.lower()]) == 1
    with pytest.raises(ValidationError):
        FeedOut.model_validate({**visible, "next_action": "claimed"})
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]
