"""Opt-in concurrent PATCH evidence against a disposable PostgreSQL schema."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.database import Base
from app.models import Application, Job, User
from app.schemas import ApplicationPatchIn
from app.services.applications import patch_application


def test_disjoint_patches_serialize_and_preserve_legacy_date():
    if os.getenv("RUN_APPLICATION_PATCH_POSTGRES") != "1":
        pytest.skip("set RUN_APPLICATION_PATCH_POSTGRES=1 for isolated PostgreSQL checks")
    admin = create_engine(os.getenv("IDENTITY_TEST_DATABASE_URL", DATABASE_URL))
    schema = "application_patch_test_" + uuid4().hex
    engine = None
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(admin.url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=5000"})
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions() as session:
            session.add(User(id=1, telegram_id=1))
            session.add(Job(id=1, source="company_site", source_url="https://example.invalid/concurrency", ingestion_method="manual", parsing_status="pending"))
            session.flush()
            session.add(Application(id=1, user_id=1, job_id=1, note="Old note", next_action="Old action", next_action_due_on=date(2026, 10, 10)))
            session.commit()
        gate = Barrier(2)
        def write(changes):
            with sessions() as session:
                gate.wait(timeout=5)
                assert patch_application(session, 1, 1, ApplicationPatchIn(**changes)) is not None
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(write, [{"note": "New note"}, {"next_action": "New action"}]))
        with sessions() as session:
            value = session.get(Application, 1)
            assert (value.note, value.next_action, value.next_action_due_on) == ("New note", "New action", date(2026, 10, 10))
            assert value.status == "saved"
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()
