"""Inventory foundation only: synthetic evidence, no source or HTTP calls."""
import hashlib
import json
import warnings
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import IntegrityError

from app.models import (
    DiscoverInventoryGeneration as Generation, DiscoverInventoryItem as Item,
    DiscoverInventoryKey as Key, DiscoverInventoryView as View, User,
)
from app.services.discover_feed_types import Evidence
from app.services.inventory_types import (
    InventoryCapacityExceeded, InventoryError, InventoryGenerationConflict,
    InventoryGenerationExpired, InventoryGenerationNotFound, InventoryIdentity,
    InventoryIdentityInvalid, InventoryInvalidTransition, InventoryItemInput,
    InventoryPublicationInvalid, InventoryViewInvalid, MAX_ITEM_BYTES, MAX_PAYLOAD_BYTES,
    canonical_json, stored_utc, validate_json_payload,
)
from app.services.query_inventory import InventoryRepository
from conftest import TestSessionLocal
from test_discover_api import vacancy

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)
IDENTITY = InventoryIdentity("Программист")


def candidate(external_id="one", **changes):
    return InventoryItemInput(snapshot=Evidence.model_validate(replace(vacancy(external_id, workplace_type="remote"), **changes)))


def building(sessions=TestSessionLocal):
    with sessions() as session:
        repo = InventoryRepository(session)
        generation_id = repo.resolve_generation(IDENTITY, NOW).generation_id
        lease = repo.claim_generation(generation_id, NOW)
    return generation_id, lease.fence


def complete(sessions=TestSessionLocal, items=()):
    generation_id, fence = building(sessions)
    with sessions() as session:
        repo = InventoryRepository(session)
        if items:
            repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
                source_total=len(items), returned_count=len(items), items=items, now=NOW)
        counters = repo.commit_inventory_page(generation_id, expected_fence=fence,
            expected_page=1 if items else 0, source_total=len(items), returned_count=0, items=[], now=NOW)
    return generation_id, fence, counters


def publish(sessions=TestSessionLocal, items=()):
    generation_id, fence, counters = complete(sessions, items)
    with sessions() as session:
        InventoryRepository(session).publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
    return generation_id


INVALID_JSON_CASES = ("description", "nested_snapshot", "snapshot_key", "evidence", "nested_evidence")


def incompatible_candidate(case):
    item = candidate()
    if case == "description":
        return item.model_copy(update={"snapshot": item.snapshot.model_copy(update={"description": "before\x00after"})})
    if case in ("nested_snapshot", "snapshot_key"):
        # Deliberately bypass DTO construction to exercise repository revalidation.
        value = {"a": [{"b": "x\x00y"}]} if case == "nested_snapshot" else {"bad\x00key": "value"}
        return item.model_copy(update={"snapshot": item.snapshot.model_copy(update={"description": value})})
    value = "x\x00y" if case == "evidence" else {"a": [[{"b": "x\x00y"}]]}
    return item.model_copy(update={"remote_evidence": item.remote_evidence.model_copy(update={"value": value})})


def assert_payload_rejection(sessions, case):
    generation_id, fence = building(sessions)
    with sessions() as session:
        with session.begin():
            before = dict(session.execute(select(Generation.__table__).where(Generation.id == generation_id)).mappings().one())
        statements = []
        def observe(conn, cursor, statement, params, context, many):
            statements.append(statement)
        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", observe)
        try:
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always")
                with pytest.raises(InventoryError, match="^Invalid inventory evidence$") as error:
                    InventoryRepository(session).commit_inventory_page(generation_id, expected_fence=fence,
                        expected_page=0, source_total=2, returned_count=2,
                        items=[candidate("valid-first"), incompatible_candidate(case)], now=NOW)
            assert emitted == []
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert type(error.value) is InventoryError
        assert statements == []  # Rejection precedes even the first SQL read/lock.
        assert not session.in_transaction() and not session.new and not session.dirty
        with session.begin():
            after = dict(session.execute(select(Generation.__table__).where(Generation.id == generation_id)).mappings().one())
            assert after == before  # Includes every counter, checkpoint, lease/fence and status.
            assert session.scalar(select(func.count()).select_from(Item).where(Item.generation_id == generation_id)) == 0
        assert InventoryRepository(session).commit_inventory_page(generation_id, expected_fence=fence,
            expected_page=0, source_total=1, returned_count=1, items=[candidate()], now=NOW).item_count == 1


@pytest.mark.parametrize("case", INVALID_JSON_CASES)
def test_jsonb_incompatible_payload_rejected_before_sql(case):
    assert_payload_rejection(TestSessionLocal, case)


@pytest.mark.parametrize("value", ["before\x00after", {"a": [{"b": "x\x00y"}]},
    {"bad\x00key": "ok"}, [{"ok": [["x\x00y"]]}]])
def test_json_compatibility_checks_all_string_positions(value):
    with pytest.raises(InventoryError, match="^Invalid inventory evidence$"):
        validate_json_payload(value)


def test_json_compatibility_preserves_types_and_unicode():
    value = {"ключ": ["Հայերեն 中文 😀", True, False, None, 1, 1.5, {"nested": []}]}
    before = canonical_json(value)
    validate_json_payload(value)
    assert canonical_json(value) == before
    assert type(value["ключ"][1]) is bool and type(value["ключ"][4]) is int


@pytest.mark.parametrize("value", [object(), {1: "no stringification"}, ("tuple",), {"set"}])
def test_json_compatibility_does_not_accept_non_json_objects(value):
    with pytest.raises(InventoryError):
        validate_json_payload(value)


@pytest.mark.parametrize("kind", ["deep", "cycle", "wide"])
def test_json_compatibility_has_bounded_traversal(kind):
    value = []
    if kind == "cycle":
        value.append(value)
    elif kind == "wide":
        value = [None] * (MAX_ITEM_BYTES + 1)
    else:
        for _ in range(4096):
            value = [value]
    with pytest.raises(InventoryError):
        validate_json_payload(value)


def assert_unicode_roundtrip(sessions):
    description = "Удалённая работа — Հայերեն 中文 😀"
    item = candidate(description=description)
    generation_id, fence, counters = complete(sessions, [item])
    expected = len(canonical_json({"snapshot": item.snapshot.model_dump(mode="json"),
        "remote_evidence": item.remote_evidence.model_dump(mode="json")}).encode("utf-8"))
    assert counters.payload_bytes == expected
    with sessions() as session:
        repo = InventoryRepository(session)
        repo.publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        assert repo.read_items(generation_id)[0].snapshot.description == description


def test_jsonb_valid_unicode_roundtrip_and_exact_bytes():
    assert_unicode_roundtrip(TestSessionLocal)


def sized_candidate(index, size=MAX_ITEM_BYTES):
    item = candidate(f"{index:03}", description="")
    overhead = InventoryRepository._payload(item)[2]
    return candidate(f"{index:03}", description="x" * (size - overhead))


def assert_payload_boundaries(sessions):
    generation_id, fence = building(sessions)
    with sessions() as session:
        repo = InventoryRepository(session)
        with pytest.raises(InventoryCapacityExceeded):
            repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
                source_total=129, returned_count=100, items=[sized_candidate(0, MAX_ITEM_BYTES + 1)], now=NOW)
        first = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
            source_total=129, returned_count=100, items=[sized_candidate(i) for i in range(100)], now=NOW)
        assert first.payload_bytes == 100 * MAX_ITEM_BYTES
        with pytest.raises(InventoryCapacityExceeded):
            repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=1,
                source_total=129, returned_count=29, items=[sized_candidate(i) for i in range(100, 129)], now=NOW)
        with session.begin():
            generation = session.get(Generation, generation_id)
            assert (generation.next_source_page, generation.item_count, generation.payload_bytes) == (1, 100, first.payload_bytes)
            assert session.scalar(select(func.count()).select_from(Item).where(Item.generation_id == generation_id)) == 100
        exact = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=1,
            source_total=129, returned_count=29, items=[sized_candidate(i) for i in range(100, 128)], now=NOW)
        assert exact.payload_bytes == MAX_PAYLOAD_BYTES and exact.item_count == 128
        final = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=2,
            source_total=129, returned_count=0, items=[], now=NOW)
        repo.publish_generation(generation_id, expected_fence=fence, expected=final, now=NOW)


def test_jsonb_payload_exact_item_and_generation_boundaries():
    assert_payload_boundaries(TestSessionLocal)


def test_canonical_identity_format_and_trim():
    identity = InventoryIdentity("  Python  ", market_country="ru", market_region="0077")
    assert identity.query == "Python" and identity.market_country == "RU" and identity.market_region == "0077"
    assert identity.digest == InventoryIdentity("Python", market_region="0077").digest
    assert identity.canonical == '{"market_country":"RU","market_region":"0077","predicate_version":1,"query":"Python","query_normalization_version":1,"remote_only":true,"snapshot_schema_version":1,"source":"trudvsem","source_query_version":1}'
    assert json.loads(IDENTITY.canonical)["market_region"] is None
    assert json.loads(IDENTITY.canonical)["remote_only"] is True
    assert "page_size" not in json.loads(IDENTITY.canonical)
    assert "Python" not in repr(identity)


@pytest.mark.parametrize("query", ["", " ", "\t\n", "x" * 201])
def test_invalid_query(query):
    with pytest.raises(InventoryIdentityInvalid):
        InventoryIdentity(query)


@pytest.mark.parametrize("changes", [
    {"query": "программист"}, {"query": "Программист  Python"}, {"market_region": "77"},
    {"query_normalization_version": 2}, {"source_query_version": 2},
    {"predicate_version": 2}, {"snapshot_schema_version": 2},
])
def test_semantics_are_distinct(changes):
    assert replace(IDENTITY, **changes).digest != IDENTITY.digest
    assert InventoryIdentity("Python  API").digest != InventoryIdentity("Python API").digest


@pytest.mark.parametrize("changes", [{"market_region": ""}, {"market_region": "٧٧"}, {"market_region": "7a"},
    {"market_country": "AM"}, {"remote_only": False}, {"remote_only": 1}, {"predicate_version": True}, {"source": "other"}])
def test_invalid_identity_semantics(changes):
    with pytest.raises(InventoryIdentityInvalid):
        replace(IDENTITY, **changes)


def test_resolve_create_building_and_different_identity():
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        first = repo.resolve_generation(IDENTITY, NOW)
        assert first.outcome == "created_building"
        assert repo.resolve_generation(IDENTITY, NOW).outcome == "reused_building"
        assert repo.resolve_generation(InventoryIdentity("Python"), NOW).generation_id != first.generation_id
        assert not session.in_transaction()
        assert session.scalar(select(func.count()).select_from(Key)) == 2
        session.rollback()
        # Collision defence compares canonical text, not just SHA-256.
        with session.begin():
            session.execute(update(Key).where(Key.identity_digest == IDENTITY.digest).values(canonical_identity="different"))
        with pytest.raises(InventoryIdentityInvalid):
            repo.resolve_generation(IDENTITY, NOW)
        assert not session.in_transaction()


def test_ready_fresh_stale_and_failed_expired_resolve():
    generation_id = publish()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        assert repo.resolve_generation(IDENTITY, NOW).outcome == "reused_ready"
        later = NOW + timedelta(minutes=15)
        replacement = repo.resolve_generation(IDENTITY, later)
        assert replacement.outcome == "created_building" and replacement.generation_id != generation_id
        assert repo.read_items(generation_id) == ()  # stale is still readable
        lease = repo.claim_generation(replacement.generation_id, later)
        repo.fail_generation(replacement.generation_id, expected_fence=lease.fence, failure_code="TEST_FAILURE", now=later)
        repo.expire_generation(replacement.generation_id, later)
        assert repo.resolve_generation(IDENTITY, later).outcome == "created_building"


def test_failed_is_not_reused():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        repo.fail_generation(generation_id, expected_fence=fence, failure_code="TEST_FAILURE", now=NOW)
        assert repo.resolve_generation(IDENTITY, NOW).generation_id != generation_id
        with pytest.raises(InventoryInvalidTransition):
            repo.claim_generation(generation_id, NOW)


def test_page_checkpoint_dedupe_and_published_immutability():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        with pytest.raises(InventoryGenerationConflict):
            repo.read_items(generation_id)
        first = candidate("first")
        counters = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
            source_total=3, returned_count=3, items=[first, candidate("first", title="replacement"), candidate("last")], now=NOW)
        assert (counters.item_count, counters.records_examined, counters.next_source_page) == (2, 3, 1)
        with pytest.raises(InventoryPublicationInvalid):
            repo.publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        counters = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=1,
            source_total=3, returned_count=0, items=[], now=NOW)
        repo.publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        rows = repo.read_items(generation_id)
        assert [row.snapshot.external_id for row in rows] == ["first", "last"]
        assert rows[0].snapshot.title == first.snapshot.title
        for action in (
            lambda: repo.claim_generation(generation_id, NOW),
            lambda: repo.fail_generation(generation_id, expected_fence=fence, failure_code="TEST", now=NOW),
            lambda: repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=2,
                source_total=3, returned_count=0, items=[], now=NOW),
        ):
            with pytest.raises(InventoryInvalidTransition):
                action()
            assert not session.in_transaction()


def test_remote_far_in_source_final_partial_and_empty_confirmation():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        for page in range(3):
            counters = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=page,
                source_total=201, returned_count=100 if page < 2 else 1,
                items=[] if page < 2 else [candidate()], now=NOW)
        assert counters.records_examined == 201 and counters.item_count == 1
        with pytest.raises(InventoryPublicationInvalid):
            repo.publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        counters = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=3,
            source_total=201, returned_count=0, items=[], now=NOW)
        repo.publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        assert len(repo.read_items(generation_id)) == 1


def test_injected_flush_failure_rolls_back_items_and_checkpoint():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        def fail(session, context, instances):
            if any(isinstance(item, Item) for item in session.new):
                raise RuntimeError("injected")
        event.listen(session, "before_flush", fail)
        try:
            with pytest.raises(RuntimeError, match="injected"):
                repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
                    source_total=1, returned_count=1, items=[candidate()], now=NOW)
        finally:
            event.remove(session, "before_flush", fail)
        assert not session.in_transaction()
        with session.begin():
            generation = session.get(Generation, generation_id)
            assert (generation.next_source_page, generation.item_count, generation.payload_bytes) == (0, 0, 0)
            assert session.scalar(select(func.count()).select_from(Item)) == 0
        assert repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
            source_total=1, returned_count=1, items=[candidate()], now=NOW).item_count == 1


@pytest.mark.parametrize("changes", [
    {"expected_page": -1}, {"source_total": -1}, {"returned_count": -1},
    {"returned_count": 0}, {"expected_page": 1}, {"expected_fence": "wrong"},
])
def test_invalid_commit_cannot_advance(changes):
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        params = dict(expected_fence=fence, expected_page=0, source_total=1, returned_count=1, items=[candidate()], now=NOW)
        with pytest.raises(InventoryGenerationConflict):
            repo.commit_inventory_page(generation_id, **(params | changes))
        assert not session.in_transaction()
        assert session.get(Generation, generation_id).next_source_page == 0


def test_total_change_and_repeated_page_rejected():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
            source_total=1, returned_count=1, items=[candidate()], now=NOW)
        for page, total in [(0, 1), (1, 2)]:
            with pytest.raises(InventoryGenerationConflict):
                repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=page,
                    source_total=total, returned_count=0, items=[], now=NOW)


def test_expired_claim_and_old_fence_rejected():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        with pytest.raises(InventoryGenerationConflict):
            repo.claim_generation(generation_id, NOW)
        later = NOW + timedelta(seconds=60)
        new = repo.claim_generation(generation_id, later)
        assert new.fence != fence
        with pytest.raises(InventoryGenerationConflict):
            repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
                source_total=0, returned_count=0, items=[], now=later)


@pytest.mark.parametrize("kind", ["source", "item", "items", "generation"])
def test_caps_rollback(kind, monkeypatch):
    import app.services.query_inventory as module
    generation_id, fence = building()
    if kind == "items": monkeypatch.setattr(module, "MAX_ITEMS", 1)
    if kind == "generation": monkeypatch.setattr(module, "MAX_PAYLOAD_BYTES", 1)
    items = [candidate(), candidate("two")] if kind == "items" else [candidate(description="x" * (128 * 1024))] if kind == "item" else [candidate()]
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        with pytest.raises(InventoryCapacityExceeded):
            repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
                source_total=10001 if kind == "source" else len(items), returned_count=len(items), items=items, now=NOW)
        assert not session.in_transaction()
        assert session.get(Generation, generation_id).next_source_page == 0


@pytest.mark.parametrize("corruption", ["count", "ordinal", "bytes", "checkpoint", "snapshot"])
def test_publication_rejects_corruption(corruption):
    generation_id, fence, counters = complete(items=[candidate()])
    with TestSessionLocal() as session:
        with session.begin():
            if corruption == "ordinal":
                session.execute(update(Item).where(Item.generation_id == generation_id).values(ordinal=1))
            elif corruption == "snapshot":
                row = session.get(Item, (generation_id, 0))
                row.snapshot = row.snapshot | {"application_status": "private"}
            else:
                values = {"item_count": 0} if corruption == "count" else {"payload_bytes": 1} if corruption == "bytes" else {"pages_committed": 3, "next_source_page": 3}
                session.execute(update(Generation).where(Generation.id == generation_id).values(**values))
                counters = replace(counters, **values)
        with pytest.raises(InventoryPublicationInvalid):
            InventoryRepository(session).publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        assert not session.in_transaction()
        assert session.get(Generation, generation_id).status == "building"


def test_ready_empty_publication_and_expired_terminal():
    generation_id = publish()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        assert repo.read_items(generation_id) == ()
        with pytest.raises(InventoryGenerationConflict): repo.expire_generation(generation_id, NOW)
        repo.expire_generation(generation_id, NOW + timedelta(minutes=15))
        with pytest.raises(InventoryGenerationExpired): repo.read_items(generation_id)
        with pytest.raises(InventoryInvalidTransition): repo.claim_generation(generation_id, NOW)
        with pytest.raises(InventoryInvalidTransition): repo.expire_generation(generation_id, NOW)


def test_view_digest_randomness_pinning_fixed_expiry_and_creator_delete():
    generation_id = publish()
    with TestSessionLocal() as session:
        with session.begin():
            user = User(telegram_id=8181)
            session.add(user)
            session.flush()
            user_id = user.id
        repo = InventoryRepository(session)
        one = repo.create_view(generation_id, creator_user_id=user_id, now=NOW)
        two = repo.create_view(generation_id, page_size=3, now=NOW)
        assert one.token != two.token and len(one.token) == len(two.token) == 32
        assert one.generation_id == two.generation_id
        assert repo.read_view(one.token, NOW + timedelta(minutes=20)).expires_at == one.expires_at
        assert repo.has_live_views(generation_id, NOW)
        with pytest.raises(InventoryGenerationConflict): repo.expire_generation(generation_id, NOW + timedelta(minutes=20))
        with session.begin():
            view = session.scalar(select(View).where(View.token_digest == hashlib.sha256(one.token.encode()).hexdigest()))
            assert view is not None and one.token not in repr(vars(view))
            session.execute(delete(User).where(User.id == user_id))
        with session.begin():
            assert session.get(View, view.id, populate_existing=True).creator_user_id is None
        assert not repo.has_live_views(generation_id, one.expires_at)
        with pytest.raises(InventoryGenerationExpired): repo.read_view(one.token, one.expires_at)
        repo.expire_generation(generation_id, one.expires_at)


@pytest.mark.parametrize("size", [0, 11, True, -1])
def test_invalid_view_page_size(size):
    generation_id, _ = building()
    with TestSessionLocal() as session:
        with pytest.raises(InventoryViewInvalid): InventoryRepository(session).create_view(generation_id, page_size=size, now=NOW)


def test_building_and_failed_view_replay_no_new_failed_view():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        handle = repo.create_view(generation_id, now=NOW)
        assert repo.read_view(handle.token, NOW).status == "building"
        repo.fail_generation(generation_id, expected_fence=fence, failure_code="TEST", now=NOW)
        assert repo.read_view(handle.token, NOW).status == "failed"
        with pytest.raises(InventoryInvalidTransition): repo.create_view(generation_id, now=NOW)


def test_view_collision_savepoint_and_no_plaintext_storage():
    generation_id, _ = building()
    tokens = iter(["a" * 32, "a" * 32, "b" * 32])
    with TestSessionLocal() as session:
        repo = InventoryRepository(session, token_factory=lambda: next(tokens))
        assert repo.create_view(generation_id, now=NOW).token == "a" * 32
        assert repo.create_view(generation_id, now=NOW).token == "b" * 32
        assert not session.in_transaction()
        with session.begin():
            assert session.scalar(select(func.count()).select_from(View)) == 2
        repo.token_factory = lambda: "a" * 32
        with pytest.raises(InventoryCapacityExceeded): repo.create_view(generation_id, now=NOW)
        assert not session.in_transaction()


def test_idle_session_and_aware_clock_required():
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        with pytest.raises(InventoryError): repo.resolve_generation(IDENTITY, NOW.replace(tzinfo=None))
        assert not session.in_transaction()
        with session.begin():
            with pytest.raises(RuntimeError, match="idle Session"): repo.resolve_generation(IDENTITY, NOW)
        with pytest.raises(InventoryGenerationNotFound): repo.claim_generation("missing", NOW)


def test_actual_thousand_item_cap_preserves_previous_pages():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        for page in range(10):
            counters = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=page,
                source_total=1001, returned_count=100,
                items=[candidate(str(page * 100 + index)) for index in range(100)], now=NOW)
        assert counters.item_count == 1000
        with pytest.raises(InventoryCapacityExceeded):
            repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=10,
                source_total=1001, returned_count=1, items=[candidate("overflow")], now=NOW)
        with session.begin():
            generation = session.get(Generation, generation_id)
            assert (generation.item_count, generation.next_source_page, generation.records_examined) == (1000, 10, 1000)
            assert session.scalar(select(func.count()).select_from(Item)) == 1000


def test_duplicates_across_pages_preserve_first_snapshot_and_byte_count():
    generation_id, fence = building()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        first = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=0,
            source_total=101, returned_count=100, items=[candidate()], now=NOW)
        second = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=1,
            source_total=101, returned_count=1, items=[candidate(title="changed")], now=NOW)
        assert second.payload_bytes == first.payload_bytes and second.item_count == 1
        final = repo.commit_inventory_page(generation_id, expected_fence=fence, expected_page=2,
            source_total=101, returned_count=0, items=[], now=NOW)
        repo.publish_generation(generation_id, expected_fence=fence, expected=final, now=NOW)
        rows = repo.read_items(generation_id)
        assert rows[0].snapshot.title != "changed"
        rows[0].snapshot.title = "locally changed detached result"
        assert repo.read_items(generation_id)[0].snapshot.title != rows[0].snapshot.title
        with pytest.raises(InventoryInvalidTransition):
            repo.publish_generation(generation_id, expected_fence=fence, expected=final, now=NOW)


def test_publication_cap_and_expected_counter_guard(monkeypatch):
    import app.services.query_inventory as module
    generation_id, fence, counters = complete(items=[candidate()])
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        with pytest.raises(InventoryPublicationInvalid):
            repo.publish_generation(generation_id, expected_fence=fence,
                expected=replace(counters, records_examined=-1), now=NOW)
        monkeypatch.setattr(module, "MAX_PAYLOAD_BYTES", counters.payload_bytes - 1)
        with pytest.raises(InventoryPublicationInvalid):
            repo.publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        assert not session.in_transaction()


def test_publication_commit_failure_leaves_building_and_retryable_evidence():
    generation_id, fence, counters = complete(items=[candidate()])
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        def fail(session):
            raise RuntimeError("publication commit failure")
        event.listen(session, "before_commit", fail)
        try:
            with pytest.raises(RuntimeError, match="publication commit failure"):
                repo.publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        finally:
            event.remove(session, "before_commit", fail)
        assert not session.in_transaction()
        with pytest.raises(InventoryGenerationConflict): repo.read_items(generation_id)
        repo.publish_generation(generation_id, expected_fence=fence, expected=counters, now=NOW)
        assert len(repo.read_items(generation_id)) == 1


@pytest.mark.parametrize("version", ["predicate_version", "snapshot_schema_version"])
def test_future_identity_cannot_accept_v1_builder_evidence(version):
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        generation_id = repo.resolve_generation(replace(IDENTITY, **{version: 2}), NOW).generation_id
        lease = repo.claim_generation(generation_id, NOW)
        with pytest.raises(InventoryGenerationConflict, match="semantics"):
            repo.commit_inventory_page(generation_id, expected_fence=lease.fence, expected_page=0,
                source_total=0, returned_count=0, items=[], now=NOW)


@pytest.mark.parametrize("changes", [{"expires_at": NOW}, {"expires_at": NOW + timedelta(hours=1)},
    {"creator_user_id": 999999}])
def test_view_lifetime_and_creator_validation(changes):
    generation_id, _ = building()
    with TestSessionLocal() as session:
        with pytest.raises(InventoryViewInvalid):
            InventoryRepository(session).create_view(generation_id, now=NOW, **changes)
        assert not session.in_transaction()


def test_new_stale_or_expired_view_rejected_existing_pin_readable():
    generation_id = publish()
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        with pytest.raises(InventoryGenerationExpired):
            repo.create_view(generation_id, now=NOW + timedelta(minutes=15))
        repo.expire_generation(generation_id, NOW + timedelta(minutes=15))
        with pytest.raises(InventoryGenerationExpired):
            repo.create_view(generation_id, now=NOW + timedelta(minutes=15))


def test_no_sensitive_logging_on_success_or_conflict(caplog):
    with TestSessionLocal() as session:
        repo = InventoryRepository(session)
        result = repo.resolve_generation(InventoryIdentity("private search text"), NOW)
        handle = repo.create_view(result.generation_id, now=NOW)
        with pytest.raises(InventoryGenerationConflict): repo.read_items(result.generation_id)
        assert "private search text" not in caplog.text and handle.token not in caplog.text


@pytest.mark.parametrize("values", [{"records_examined": -1}, {"payload_bytes": 16777217},
    {"item_count": 1001}, {"source_total": 10001}, {"status": "ready"}, {"lease_until": None}])
def test_db_constraints_reject_invalid_generation(values):
    generation_id, _ = building()
    with TestSessionLocal() as session:
        with pytest.raises(IntegrityError):
            with session.begin(): session.execute(update(Generation).where(Generation.id == generation_id).values(**values))
        assert not session.in_transaction()
