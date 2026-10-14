from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
from threading import Event, local

import pytest

from sqlalchemy import event, select

import app.main as main_module
import app.services.discover as discover_service
from app.models import Application, ApplicationReminder, ApplicationStatus, ApplicationStatusHistory, Job, User
from app.services.discover import save_discovered_job
from app.services.trudvsem import (
    TrudvsemProvider,
    TrudvsemTimeoutError,
    TrudvsemVacancyNotFoundError,
)
from app.services.vacancy_sources import ExternalVacancy, SearchPage
from conftest import TestSessionLocal, client, engine


def create_user(telegram_id: int) -> int:
    response = client.post(
        "/users/telegram", json={"telegram_id": telegram_id, "first_name": "Anna"}
    )
    assert response.status_code == 200
    return response.json()["id"]


def put_profile(user_id: int, **changes: object) -> None:
    payload: dict[str, object] = {
        "target_roles": ["Backend Developer"],
        "skills": ["Python"],
        "experience": "middle",
        "location": ["Москва"],
        "workplace_preference": "any",
    }
    payload.update(changes)
    assert client.put(f"/users/{user_id}/profile", json=payload).status_code == 200


def vacancy(
    external_id: str = "vacancy-1",
    *,
    source_scope: str = "company-1",
    workplace_type: str = "unknown",
    location: str | None = "Москва",
    source_url: str | None = None,
) -> ExternalVacancy:
    return ExternalVacancy(
        source="trudvsem",
        source_scope=source_scope,
        external_id=external_id,
        source_url=source_url
        or f"https://trudvsem.ru/vacancy/card/{source_scope}/{external_id}",
        title="Backend Developer",
        company="Acme",
        location=location,
        description="Build APIs",
        requirements_text="Python",
        workplace_type=workplace_type,
        salary_text="от 100000",
        salary_min=Decimal("100000"),
        salary_max=None,
        salary_currency="RUB",
        source_updated_at=datetime(2026, 9, 23, 10, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 9, 23, 11, tzinfo=timezone.utc),
    )


class StubClient:
    def __init__(self, items: list[ExternalVacancy] | None = None, total: int | None = None):
        self.items = [vacancy()] if items is None else items
        self.total = len(self.items) if total is None else total
        self.search_calls: list[dict[str, object]] = []
        self.detail_calls: list[tuple[str, str]] = []

    def search(self, query: str, *, limit: int, offset: int, region_code: str | None = None):
        self.search_calls.append(
            {"query": query, "limit": limit, "offset": offset, "region_code": region_code}
        )
        return SearchPage(self.items, self.total, limit, offset)

    def get_detail(self, source_scope: str, external_id: str):
        self.detail_calls.append((source_scope, external_id))
        for item in self.items:
            if item.source_scope == source_scope and item.external_id == external_id:
                return item
        raise TrudvsemVacancyNotFoundError


def test_search_is_ephemeral_and_missing_profile_keeps_results(monkeypatch) -> None:
    user_id = create_user(1)
    source = StubClient(total=15)
    monkeypatch.setattr(main_module, "trudvsem_client", source)

    response = client.get(
        f"/users/{user_id}/discover/jobs",
        params={"market_country": "RU", "query": "Backend", "limit": 10, "offset": 0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_total"] == 15
    assert payload["next_offset"] == 10
    item = payload["items"][0]
    assert item["description"] == "Build APIs"
    assert item["requirements_text"] == "Python"
    assert "raw_payload" not in item
    assert item["application_id"] is None
    assert item["application_status"] is None
    assert item["already_saved_for_user"] is False
    assert "job_id" not in item
    assert payload["items"][0]["preview_match"] == {
        "available": False,
        "unavailable_reason": "profile_missing_for_preview",
        "algorithm_version": None,
        "score": None,
        "verdict": None,
        "coverage": None,
        "confidence": None,
        "components": {},
        "strengths": [],
        "gaps": [],
        "unknowns": [],
        "conflicts": [],
        "recommendation": None,
    }
    with TestSessionLocal() as session:
        assert session.query(Job).count() == 0
        assert session.query(Application).count() == 0


def test_search_description_and_requirements_are_nullable(monkeypatch) -> None:
    user_id = create_user(101)
    source = StubClient([replace(vacancy(), description=None, requirements_text=None)])
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    response = client.get(
        f"/users/{user_id}/discover/jobs",
        params={"market_country": "RU", "query": "Backend"},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["description"] is None
    assert item["requirements_text"] is None
    with TestSessionLocal() as session:
        assert session.query(Job).count() == 0
        assert session.query(Application).count() == 0


def test_preview_uses_transient_job_without_fake_ids_or_ai_success(monkeypatch) -> None:
    user_id = create_user(2)
    put_profile(user_id)
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient())

    item = client.get(
        f"/users/{user_id}/discover/jobs",
        params={"market_country": "ru", "query": "Backend"},
    ).json()["items"][0]

    preview = item["preview_match"]
    assert preview["available"] is True
    assert preview["components"]["role"]["score"] == 100
    assert preview["components"]["location"]["score"] == 100
    assert preview["components"]["required_skills"]["score"] is None
    assert preview["verdict"] == "insufficient_data"
    assert "application_id" not in preview and "job_id" not in preview
    with TestSessionLocal() as session:
        assert session.query(Job).count() == 0


def test_unsupported_market_does_not_call_source(monkeypatch) -> None:
    user_id = create_user(3)
    source = StubClient()
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    response = client.get(
        f"/users/{user_id}/discover/jobs",
        params={"market_country": "AM", "query": "Backend"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "unsupported_market"}}
    assert source.search_calls == []


def test_remote_filter_is_local_and_pagination_remains_source_based(monkeypatch) -> None:
    user_id = create_user(4)
    source = StubClient(
        [vacancy("onsite-unknown"), vacancy("remote", workplace_type="remote")], total=12
    )
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    response = client.get(
        f"/users/{user_id}/discover/jobs",
        params={
            "market_country": "RU",
            "query": "DevOps",
            "remote_only": True,
            "limit": 2,
            "offset": 4,
            "region_code": "7700000000000",
        },
    )
    payload = response.json()
    assert [item["external_id"] for item in payload["items"]] == ["remote"]
    assert payload["returned_count"] == 1
    assert payload["next_offset"] == 6
    assert payload["locally_filtered"] is True
    assert source.search_calls[0]["region_code"] == "7700000000000"


def test_already_saved_is_user_scoped_and_queried_without_per_item_lookup(monkeypatch) -> None:
    owner = create_user(5)
    other = create_user(6)
    items = [vacancy("one"), vacancy("two")]
    with TestSessionLocal() as session:
        first = Job(
            source="trudvsem",
            ingestion_method="discover",
            source_url=items[0].source_url,
            source_scope=items[0].source_scope,
            external_id=items[0].external_id,
        )
        second = Job(
            source="trudvsem",
            ingestion_method="discover",
            source_url=items[1].source_url,
            source_scope=items[1].source_scope,
            external_id=items[1].external_id,
        )
        session.add_all([first, second])
        session.flush()
        owned = Application(user_id=owner, job_id=first.id, status="offer")
        session.add_all([owned, Application(user_id=other, job_id=second.id)])
        session.commit()
        owned_id = owned.id
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient(items))

    payload = client.get(
        f"/users/{owner}/discover/jobs",
        params={"market_country": "RU", "query": "Backend"},
    ).json()
    assert [item["already_saved_for_user"] for item in payload["items"]] == [True, False]
    assert [item["application_id"] for item in payload["items"]] == [owned_id, None]
    assert [item["application_status"] for item in payload["items"]] == ["offer", None]


def test_saved_lookup_query_count_does_not_grow_with_results(monkeypatch) -> None:
    user_id = create_user(61)
    counts: list[int] = []

    def run(items: list[ExternalVacancy]) -> None:
        statements: list[str] = []
        # Exercise populated saved state so ORM lazy loads cannot hide behind
        # the no-results path.
        for item in items:
            body = {key: getattr(item, key) for key in ("source", "source_scope", "external_id")}
            monkeypatch.setattr(main_module, "trudvsem_client", StubClient([item]))
            assert client.post(f"/users/{user_id}/discover/jobs/save", json=body).status_code == 200

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        monkeypatch.setattr(main_module, "trudvsem_client", StubClient(items))
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = client.get(
                f"/users/{user_id}/discover/jobs",
                params={"market_country": "RU", "query": "Backend"},
            )
            assert response.status_code == 200
            assert all(item["application_id"] is not None for item in response.json()["items"])
        finally:
            event.remove(engine, "before_cursor_execute", record)
        counts.append(len(statements))
        lookup = [statement for statement in statements if "JOIN applications" in statement]
        assert len(lookup) == 1

    run([vacancy("one")])
    run([vacancy(str(index)) for index in range(5)])
    assert counts[0] == counts[1]


def test_save_creates_once_and_preserves_advanced_application_status(monkeypatch) -> None:
    user_id = create_user(7)
    source = StubClient()
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    body = {"source": "trudvsem", "source_scope": "company-1", "external_id": "vacancy-1"}

    first = client.post(f"/users/{user_id}/discover/jobs/save", json=body)
    assert first.status_code == 200
    assert first.json()["job_created"] is True
    assert first.json()["application_created"] is True
    app_id = first.json()["application"]["id"]
    assert first.json()["job"]["salary_period"] == "unknown"
    assert first.json()["job"]["ingestion_method"] == "discover"
    assert first.json()["application"]["status"] == "saved"
    for field in ("source", "source_scope", "external_id", "source_url", "title", "company", "description", "requirements_text"):
        assert first.json()["job"][field] == getattr(vacancy(), field)

    with TestSessionLocal() as session:
        application = session.get(Application, app_id)
        assert application is not None
        application.status = "interview"
        application.note = "Keep this note"
        application.next_action = "Contact recruiter"
        application.next_action_due_on = date(2026, 11, 1)
        reminder_time = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
        generation = uuid4()
        session.add(ApplicationReminder(
            application_id=app_id, remind_at=reminder_time, timezone="Asia/Yerevan",
            generation=generation, next_attempt_at=reminder_time,
        ))
        session.commit()

    repeated = client.post(f"/users/{user_id}/discover/jobs/save", json=body)
    assert repeated.status_code == 200
    assert repeated.json()["job_created"] is False
    assert repeated.json()["application_created"] is False
    assert repeated.json()["application"]["status"] == "interview"
    assert repeated.json()["application"]["id"] == app_id
    assert repeated.json()["application"]["note"] == "Keep this note"
    assert repeated.json()["application"]["next_action"] == "Contact recruiter"
    assert repeated.json()["application"]["next_action_due_on"] == "2026-11-01"
    assert repeated.json()["application"]["reminder_delivery_state"] == "pending"
    with TestSessionLocal() as session:
        assert session.query(Job).count() == 1
        assert session.query(Application).count() == 1
        assert session.query(ApplicationStatusHistory).count() == 1
        reminder = session.get(ApplicationReminder, app_id)
        assert reminder.generation == generation
        assert reminder.attempt_count == 0
        assert reminder.timezone == "Asia/Yerevan"
        assert reminder.remind_at == reminder_time.replace(tzinfo=None)


@pytest.mark.parametrize("status", list(ApplicationStatus))
def test_discover_reports_current_application_after_status_change(monkeypatch, status) -> None:
    user_id = create_user(701)
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient())
    body = {"source": "trudvsem", "source_scope": "company-1", "external_id": "vacancy-1"}
    saved = client.post(f"/users/{user_id}/discover/jobs/save", json=body).json()
    app_id = saved["application"]["id"]
    response = client.put(f"/users/{user_id}/applications/{app_id}/status", json={"status": status.value})
    assert response.status_code == 200
    item = client.get(f"/users/{user_id}/discover/jobs", params={"market_country": "RU", "query": "Backend"}).json()["items"][0]
    assert item["already_saved_for_user"] is True
    assert item["application_id"] == app_id
    assert item["application_status"] == status.value


def test_save_reloads_application_after_enrichment(monkeypatch) -> None:
    user_id = create_user(703)
    def update_status(_session, job, _ai_service, **_kwargs):
        with TestSessionLocal() as concurrent:
            application = concurrent.scalar(select(Application).where(Application.job_id == job.id))
            application.status = "applied"
            concurrent.commit()
    monkeypatch.setattr(discover_service, "run_job_ai_enrichment", update_status)
    with TestSessionLocal() as session:
        result = save_discovered_job(
            session, user_id, source="trudvsem", source_scope="company-1", external_id="vacancy-1",
            provider=TrudvsemProvider(StubClient()), ai_service=main_module.ai_enrichment_service,
        )
        assert result.application.status == "applied"


@pytest.mark.parametrize("explicit_transaction", [False, True])
def test_application_failure_rolls_back_new_job_and_leaves_session_usable(monkeypatch, explicit_transaction) -> None:
    user_id = create_user(704)
    def fail(*_args):
        raise RuntimeError("application creation failed")
    monkeypatch.setattr(discover_service, "get_or_create_application", fail)
    def begin(connection):
        connection.exec_driver_sql("BEGIN")
    if explicit_transaction:
        event.listen(engine, "begin", begin)
    try:
        with TestSessionLocal() as session:
            with pytest.raises(RuntimeError, match="application creation failed"):
                save_discovered_job(
                    session, user_id, source="trudvsem", source_scope="company-1", external_id="vacancy-1",
                    provider=TrudvsemProvider(StubClient()), ai_service=main_module.ai_enrichment_service,
                )
            assert session.query(Job).count() == 0
            assert session.query(Application).count() == 0
            assert session.get(User, user_id) is not None
    finally:
        if explicit_transaction:
            event.remove(engine, "begin", begin)


def test_saved_lookup_uses_legacy_url_but_not_a_different_attached_identity(monkeypatch) -> None:
    user_id = create_user(702)
    original = vacancy()
    different = replace(original, source_scope="different-scope")
    with TestSessionLocal() as session:
        manual = Job(source="company_site", source_url=original.source_url)
        session.add(manual)
        session.flush()
        application = Application(user_id=user_id, job_id=manual.id, status="applied")
        session.add(application)
        session.commit()
        app_id = application.id
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([original]))
    params = {"market_country": "RU", "query": "Backend"}
    item = client.get(f"/users/{user_id}/discover/jobs", params=params).json()["items"][0]
    assert (item["application_id"], item["application_status"]) == (app_id, "applied")
    body = {key: getattr(original, key) for key in ("source", "source_scope", "external_id")}
    assert client.post(f"/users/{user_id}/discover/jobs/save", json=body).status_code == 200
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([different]))
    item = client.get(f"/users/{user_id}/discover/jobs", params=params).json()["items"][0]
    assert item["already_saved_for_user"] is False
    assert item["application_id"] is None and item["application_status"] is None


@pytest.mark.parametrize("identity_application_owner", ["none", "other", "current"])
def test_split_identity_and_manual_url_have_no_handoff_for_any_owner(monkeypatch, identity_application_owner) -> None:
    owner = create_user(705)
    other = create_user(706)
    item = vacancy()
    with TestSessionLocal() as session:
        identity = Job(
            source=item.source, source_scope=item.source_scope, external_id=item.external_id,
            source_url="https://trudvsem.ru/vacancy/card/previous-url", ingestion_method="discover",
        )
        manual = Job(source="company_site", source_url=item.source_url, ingestion_method="manual")
        session.add_all([identity, manual])
        session.flush()
        session.add(Application(user_id=owner, job_id=manual.id, status="interview"))
        identity_application = None
        if identity_application_owner != "none":
            identity_application = Application(
                user_id=owner if identity_application_owner == "current" else other,
                job_id=identity.id, status="offer",
            )
            session.add(identity_application)
        session.commit()
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([item]))
    response = client.get(f"/users/{owner}/discover/jobs", params={"market_country": "RU", "query": "Backend"})
    assert response.status_code == 200
    actual = response.json()["items"][0]
    assert (actual["already_saved_for_user"], actual["application_id"], actual["application_status"]) == (False, None, None)
    body = {key: getattr(item, key) for key in ("source", "source_scope", "external_id")}
    assert client.post(f"/users/{owner}/discover/jobs/save", json=body).status_code == 409


@pytest.mark.parametrize(
    ("identity_owner", "url_target", "other_source", "conflict", "handoff"),
    [
        pytest.param("current", "manual", False, True, None, id="A-owned-identity-split-manual-url"),
        pytest.param("current", "attached", False, True, None, id="A-owned-identity-split-attached-url"),
        pytest.param("none", "manual", False, True, None, id="B-unowned-identity-split-manual-url"),
        pytest.param("current", "identity", False, False, "identity", id="C-identity-and-url-same-job"),
        pytest.param("current", "unclaimed", False, False, "identity", id="D-identity-can-move-to-unclaimed-url"),
        pytest.param("absent", "manual", False, False, "url", id="E-manual-url-fallback"),
        pytest.param("absent", "attached", False, True, None, id="F-url-attached-to-another-source"),
        pytest.param("current", "identity", True, False, "identity", id="G-same-id-from-different-source"),
    ],
)
def test_discover_handoff_and_save_share_identity_conflict_semantics(
    monkeypatch, identity_owner, url_target, other_source, conflict, handoff,
) -> None:
    user_id = create_user(708)
    item = vacancy(source_url=vacancy().source_url + "#description")
    canonical_url = vacancy().source_url
    application_ids: dict[str, int] = {}
    with TestSessionLocal() as session:
        identity = None
        if identity_owner != "absent":
            identity = Job(
                source=item.source, source_scope=item.source_scope, external_id=item.external_id,
                source_url=canonical_url if url_target == "identity" else "https://trudvsem.ru/vacancy/card/previous-url",
                ingestion_method="discover",
            )
            session.add(identity)
            session.flush()
            if identity_owner == "current":
                application = Application(user_id=user_id, job_id=identity.id, status="offer")
                session.add(application)
                session.flush()
                application_ids["identity"] = application.id
        if url_target in {"manual", "attached"}:
            url_job = Job(
                source="company_site", source_url=canonical_url,
                ingestion_method="manual" if url_target == "manual" else "discover",
                source_scope=item.source_scope if url_target == "attached" else None,
                external_id=item.external_id if url_target == "attached" else None,
            )
            session.add(url_job)
            session.flush()
            application = Application(user_id=user_id, job_id=url_job.id, status="interview")
            session.add(application)
            session.flush()
            application_ids["url"] = application.id
        if other_source:
            independent = Job(
                source="company_site", source_scope=item.source_scope, external_id=item.external_id,
                source_url="https://example.test/jobs/independent", ingestion_method="discover",
            )
            session.add(independent)
            session.flush()
            session.add(Application(user_id=user_id, job_id=independent.id, status="rejected"))
        session.commit()
        jobs_before = session.query(Job).count()
        applications_before = session.query(Application).count()

    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([item]))
    class DisabledAI:
        configured = False
    monkeypatch.setattr(main_module, "ai_enrichment_service", DisabledAI())
    response = client.get(f"/users/{user_id}/discover/jobs", params={"market_country": "RU", "query": "Backend"})
    assert response.status_code == 200
    actual = response.json()["items"][0]
    if handoff is None:
        assert (actual["already_saved_for_user"], actual["application_id"], actual["application_status"]) == (False, None, None)
    else:
        assert actual["already_saved_for_user"] is True
        assert actual["application_id"] == application_ids[handoff]
        assert actual["application_status"] == ("offer" if handoff == "identity" else "interview")

    body = {key: getattr(item, key) for key in ("source", "source_scope", "external_id")}
    saved = client.post(f"/users/{user_id}/discover/jobs/save", json=body)
    if conflict:
        assert saved.status_code == 409
        assert saved.json() == {"detail": {"code": "source_identity_conflict"}}
    else:
        assert saved.status_code == 200
        assert saved.json()["job_created"] is False
        assert saved.json()["application_created"] is False
        assert saved.json()["application"]["id"] == actual["application_id"]
        assert saved.json()["application"]["status"] == actual["application_status"]
    with TestSessionLocal() as session:
        assert session.query(Job).count() == jobs_before
        assert session.query(Application).count() == applications_before


@pytest.mark.parametrize("explicit_transaction", [False, True])
def test_sqlite_concurrent_save_serializes_before_identity_read(concurrent_database, explicit_transaction) -> None:
    sessions = concurrent_database
    database = sessions.kw["bind"]
    with sessions() as session:
        session.add(User(id=1, telegram_id=707))
        session.commit()
    first_read = Event()
    release_first = Event()
    second_attempt = Event()
    second_read = Event()
    request = local()

    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    def before(_conn, _cursor, statement, _parameters, _context, _executemany):
        if getattr(request, "name", None) == "second":
            if statement.startswith("BEGIN") or statement.startswith("UPDATE jobs"):
                second_attempt.set()
            if statement.startswith("SELECT") and "FROM jobs" in statement:
                second_read.set()

    def after(_conn, _cursor, statement, _parameters, _context, _executemany):
        if getattr(request, "name", None) == "first" and statement.startswith("SELECT") and "FROM jobs" in statement:
            first_read.set()
            assert release_first.wait(timeout=5)

    if explicit_transaction:
        event.listen(database, "begin", begin)
    event.listen(database, "before_cursor_execute", before)
    event.listen(database, "after_cursor_execute", after)
    class DisabledAI:
        configured = False
    def save(name):
        request.name = name
        with sessions() as session:
            result = save_discovered_job(
                session, 1, source="trudvsem", source_scope="company-1", external_id="vacancy-1",
                provider=TrudvsemProvider(StubClient()), ai_service=DisabledAI(),
            )
            return result.job.id, result.application.id, result.job_created, result.application_created
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(save, "first")
            try:
                assert first_read.wait(timeout=5)
                second = pool.submit(save, "second")
                assert second_attempt.wait(timeout=5)
                assert not second_read.wait(timeout=0.2)
            finally:
                release_first.set()
            first_result = first.result(timeout=5)
            second_result = second.result(timeout=5)
        assert first_result[:2] == second_result[:2]
        assert first_result[2:] == (True, True)
        assert second_result[2:] == (False, False)
        with sessions() as session:
            assert session.query(Job).count() == 1
            assert session.query(Application).count() == 1
            assert session.query(ApplicationStatusHistory).count() == 1
    finally:
        release_first.set()
        event.remove(database, "before_cursor_execute", before)
        event.remove(database, "after_cursor_execute", after)
        if explicit_transaction:
            event.remove(database, "begin", begin)


def test_save_reuses_manual_job_and_attaches_identity(monkeypatch) -> None:
    user_id = create_user(8)
    item = vacancy()
    with TestSessionLocal() as session:
        manual = Job(source="company_site", ingestion_method="manual", source_url=item.source_url)
        session.add(manual)
        session.commit()
        manual_id = manual.id
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([item]))

    response = client.post(
        f"/users/{user_id}/discover/jobs/save",
        json={"source": "trudvsem", "source_scope": "company-1", "external_id": "vacancy-1"},
    )

    assert response.status_code == 200
    assert response.json()["job"]["id"] == manual_id
    assert response.json()["job"]["source"] == "trudvsem"
    assert response.json()["job"]["ingestion_method"] == "manual"
    assert response.json()["job"]["external_id"] == "vacancy-1"


def test_manual_job_attachment_and_repeated_partial_save_preserve_known_fields(monkeypatch) -> None:
    user_id = create_user(81)
    item = vacancy()
    with TestSessionLocal() as session:
        manual = Job(
            source="company_site",
            ingestion_method="manual",
            source_url=item.source_url,
            title="Detailed manual title",
            description="Detailed manual description",
            requirements_text="Detailed manual requirements",
            location="Detailed manual location",
            salary_text="150000–200000 RUB",
            salary_min=Decimal("150000"),
            salary_max=Decimal("200000"),
            salary_currency="RUB",
            salary_period="month",
            salary_period_inferred=True,
        )
        session.add(manual)
        session.commit()
        manual_id = manual.id

    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([item]))
    body = {"source": "trudvsem", "source_scope": "company-1", "external_id": "vacancy-1"}
    assert client.post(f"/users/{user_id}/discover/jobs/save", json=body).status_code == 200

    partial = replace(
        item,
        title="Updated source title",
        description=None,
        requirements_text=None,
        location=None,
        workplace_type="unknown",
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        source_updated_at=None,
    )
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([partial]))
    assert client.post(f"/users/{user_id}/discover/jobs/save", json=body).status_code == 200

    with TestSessionLocal() as session:
        saved = session.get(Job, manual_id)
        assert saved is not None
        assert saved.title == "Updated source title"
        assert saved.company == "Acme"
        assert saved.description == "Detailed manual description"
        assert saved.requirements_text == "Detailed manual requirements"
        assert saved.location == "Detailed manual location"
        assert saved.salary_text == "150000–200000 RUB"
        assert saved.salary_min == Decimal("150000")
        assert saved.salary_max == Decimal("200000")
        assert saved.salary_currency == "RUB"
        assert saved.salary_period == "month"
        assert saved.salary_period_inferred is True


def test_identified_job_refreshes_confirmed_values_and_preserves_unknowns(monkeypatch) -> None:
    user_id = create_user(82)
    original = vacancy()
    with TestSessionLocal() as session:
        job = Job(
            source="trudvsem",
            ingestion_method="discover",
            source_url=original.source_url,
            source_scope=original.source_scope,
            external_id=original.external_id,
            title="Old title",
            company="Old company",
            description="Old description",
            requirements_text="Old requirements",
            location="Old location",
            workplace_type="remote",
            salary_text="Old salary",
            salary_min=Decimal("100000"),
            salary_max=Decimal("150000"),
            salary_currency="RUB",
            salary_period="year",
            salary_period_inferred=False,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    refreshed = replace(
        original,
        title="New title",
        company="New company",
        description="New description",
        requirements_text="New requirements",
        location="New location",
        salary_text="200000–250000 RUB",
        salary_min=Decimal("200000"),
        salary_max=Decimal("250000"),
    )
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([refreshed]))
    body = {"source": "trudvsem", "source_scope": "company-1", "external_id": "vacancy-1"}
    assert client.post(f"/users/{user_id}/discover/jobs/save", json=body).status_code == 200

    unknowns = replace(
        refreshed,
        description=None,
        requirements_text=None,
        location=None,
        workplace_type="unknown",
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
    )
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([unknowns]))
    assert client.post(f"/users/{user_id}/discover/jobs/save", json=body).status_code == 200

    with TestSessionLocal() as session:
        saved = session.get(Job, job_id)
        assert saved is not None
        assert (saved.title, saved.company) == ("New title", "New company")
        assert saved.description == "New description"
        assert saved.requirements_text == "New requirements"
        assert saved.location == "New location"
        assert saved.workplace_type == "remote"
        assert saved.salary_text == "200000–250000 RUB"
        assert saved.salary_min == Decimal("200000")
        assert saved.salary_max == Decimal("250000")
        assert saved.salary_currency == "RUB"
        assert saved.salary_period == "year"
        assert saved.salary_period_inferred is False


def test_same_identity_can_move_to_unowned_canonical_url(monkeypatch) -> None:
    user_id = create_user(83)
    original = vacancy(source_url="https://trudvsem.ru/vacancy/card/company-1/old")
    with TestSessionLocal() as session:
        job = Job(
            source="trudvsem",
            ingestion_method="discover",
            source_url=original.source_url,
            source_scope=original.source_scope,
            external_id=original.external_id,
        )
        session.add(job)
        session.commit()
        job_id = job.id
    moved = replace(original, source_url="https://trudvsem.ru/vacancy/card/company-1/new")
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([moved]))

    response = client.post(
        f"/users/{user_id}/discover/jobs/save",
        json={"source": "trudvsem", "source_scope": "company-1", "external_id": "vacancy-1"},
    )

    assert response.status_code == 200
    with TestSessionLocal() as session:
        saved = session.get(Job, job_id)
        assert saved is not None
        assert saved.source_url == moved.source_url


def test_different_identity_cannot_reuse_attached_canonical_url(monkeypatch) -> None:
    first_user = create_user(84)
    second_user = create_user(85)
    shared_url = "https://trudvsem.ru/vacancy/card/shared"
    first = vacancy("first", source_scope="company-a", source_url=shared_url)
    second = vacancy("second", source_scope="company-b", source_url=shared_url)
    body = lambda item: {
        "source": "trudvsem",
        "source_scope": item.source_scope,
        "external_id": item.external_id,
    }
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([first]))
    assert client.post(f"/users/{first_user}/discover/jobs/save", json=body(first)).status_code == 200
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([second]))

    response = client.post(f"/users/{second_user}/discover/jobs/save", json=body(second))

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "source_identity_conflict"}}


def test_save_detects_identity_url_conflict(monkeypatch) -> None:
    user_id = create_user(9)
    item = vacancy(source_url="https://trudvsem.ru/vacancy/card/company-1/shared")
    with TestSessionLocal() as session:
        session.add_all(
            [
                Job(
                    source="trudvsem",
                    ingestion_method="discover",
                    source_url="https://trudvsem.ru/vacancy/card/company-1/other-url",
                    source_scope="company-1",
                    external_id="vacancy-1",
                ),
                Job(source="company_site", source_url=item.source_url),
            ]
        )
        session.commit()
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([item]))
    response = client.post(
        f"/users/{user_id}/discover/jobs/save",
        json={"source": "trudvsem", "source_scope": "company-1", "external_id": "vacancy-1"},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "source_identity_conflict"}}
    with TestSessionLocal() as session:
        assert session.query(Application).count() == 0


def test_disappeared_vacancy_creates_nothing(monkeypatch) -> None:
    user_id = create_user(10)
    monkeypatch.setattr(main_module, "trudvsem_client", StubClient([]))
    response = client.post(
        f"/users/{user_id}/discover/jobs/save",
        json={"source": "trudvsem", "source_scope": "company-1", "external_id": "missing"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "vacancy_not_found"}}
    with TestSessionLocal() as session:
        assert session.query(Job).count() == 0
        assert session.query(Application).count() == 0


def test_search_maps_typed_source_timeout(monkeypatch) -> None:
    user_id = create_user(101)

    class TimeoutClient(StubClient):
        def search(self, query: str, *, limit: int, offset: int, region_code: str | None = None):
            raise TrudvsemTimeoutError

    monkeypatch.setattr(main_module, "trudvsem_client", TimeoutClient())
    response = client.get(
        f"/users/{user_id}/discover/jobs",
        params={"market_country": "RU", "query": "Backend"},
    )
    assert response.status_code == 504
    assert response.json() == {"detail": {"code": "source_timeout"}}


def test_detail_fetch_runs_without_active_database_transaction() -> None:
    with TestSessionLocal() as session:
        user = User(telegram_id=11, first_name="Anna")
        session.add(user)
        session.commit()

        class TransactionProbe(StubClient):
            def get_detail(self, source_scope: str, external_id: str):
                assert session.in_transaction() is False
                return super().get_detail(source_scope, external_id)

        result = save_discovered_job(
            session,
            user.id,
            source="trudvsem",
            source_scope="company-1",
            external_id="vacancy-1",
            provider=TrudvsemProvider(TransactionProbe()),
            ai_service=main_module.ai_enrichment_service,
        )
        assert result.application.status == "saved"


def test_ai_enrichment_failure_keeps_saved_application() -> None:
    class FailingAI:
        configured = True

        def enrich(self, _vacancy):
            return None, "provider_error"

    with TestSessionLocal() as session:
        user = User(telegram_id=12, first_name="Anna")
        session.add(user)
        session.commit()
        result = save_discovered_job(
            session,
            user.id,
            source="trudvsem",
            source_scope="company-1",
            external_id="vacancy-1",
            provider=TrudvsemProvider(StubClient()),
            ai_service=FailingAI(),  # type: ignore[arg-type]
        )
        assert result.application.status == "saved"
        assert result.job.ai_enrichment_status == "failed"
        assert result.job.ai_enrichment_error == "provider_error"
        assert session.scalar(
            select(Application).where(Application.user_id == user.id)
        ) is not None
