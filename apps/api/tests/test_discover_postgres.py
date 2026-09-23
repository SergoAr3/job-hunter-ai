"""Opt-in Discover concurrency checks against the migrated local PostgreSQL database."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import Application, Job, User
from app.services.discover import SourceIdentityConflictError, save_discovered_job
from app.services.trudvsem import ExternalVacancy
from test_discover_api import StubClient, vacancy


class DisabledAI:
    configured = False


@pytest.mark.skipif(
    os.getenv("RUN_DISCOVER_POSTGRES") != "1",
    reason="requires migrated local PostgreSQL",
)
def test_concurrent_manual_url_attachment_never_silently_overwrites_identity() -> None:
    unique = uuid.uuid4().hex
    shared_url = f"https://trudvsem.ru/vacancy/card/concurrency-{unique}"
    identities = [
        replace(
            vacancy(),
            source_scope=f"company-a-{unique}",
            external_id=f"vacancy-a-{unique}",
            source_url=shared_url,
        ),
        replace(
            vacancy(),
            source_scope=f"company-b-{unique}",
            external_id=f"vacancy-b-{unique}",
            source_url=shared_url,
        ),
    ]
    with SessionLocal() as session:
        user = User(
            telegram_id=-(uuid.uuid4().int % (2**62)),
            first_name="Discover concurrency integration test",
        )
        manual = Job(source="company_site", ingestion_method="manual", source_url=shared_url)
        session.add_all([user, manual])
        session.commit()
        user_id, job_id = user.id, manual.id

    barrier = Barrier(2)

    class ConcurrentClient(StubClient):
        def get_detail(self, source_scope: str, external_id: str) -> ExternalVacancy:
            result = super().get_detail(source_scope, external_id)
            barrier.wait(timeout=10)
            return result

    def save(item: ExternalVacancy) -> str:
        with SessionLocal() as session:
            try:
                save_discovered_job(
                    session,
                    user_id,
                    source="trudvsem",
                    source_scope=item.source_scope,
                    external_id=item.external_id,
                    client=ConcurrentClient([item]),
                    ai_service=DisabledAI(),  # type: ignore[arg-type]
                )
                return "saved"
            except SourceIdentityConflictError:
                return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, identities))

        assert sorted(results) == ["conflict", "saved"]
        with SessionLocal() as session:
            saved = session.get(Job, job_id)
            assert saved is not None
            assert (saved.source_scope, saved.external_id) in {
                (item.source_scope, item.external_id) for item in identities
            }
            assert session.scalar(select(Application).where(Application.job_id == job_id)) is not None
    finally:
        with SessionLocal() as session:
            session.execute(delete(Application).where(Application.job_id == job_id))
            session.execute(delete(Job).where(Job.id == job_id))
            session.execute(delete(User).where(User.id == user_id))
            session.commit()
