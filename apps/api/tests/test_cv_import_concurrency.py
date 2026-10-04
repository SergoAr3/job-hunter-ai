"""Opt-in real PostgreSQL evidence; follows isolated-schema test conventions."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.database import Base
from app.models import User, UserProfile, WorkExperience
from app.schemas import CVProfileDraftOut
from app.services.cv_import import apply_import, prepare_preview
from app.services.cv_import_state import CVImportError, ImportStore
from app.work_experience_schema import WorkExperienceIn


def test_postgres_distinct_previews_serialize_domain_apply(tmp_path):
    if os.getenv("RUN_CV_IMPORT_POSTGRES") != "1":
        pytest.skip("set RUN_CV_IMPORT_POSTGRES=1 for isolated PostgreSQL checks")
    url = os.getenv("IDENTITY_TEST_DATABASE_URL", DATABASE_URL)
    admin = create_engine(url, connect_args={"connect_timeout": 3})
    assert admin.dialect.name == "postgresql"
    schema = f"cv_import_test_{uuid4().hex}"
    engine = None
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=5000"})
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        gate = Barrier(2)
        class OverlappingStore(ImportStore):
            def claim(self, *args):
                result = super().claim(*args)
                # Both different tokens are durably claimed before either
                # request attempts domain locking. No scheduling sleeps.
                gate.wait(timeout=5)
                return result
        store = OverlappingStore(tmp_path / "imports")
        draft = CVProfileDraftOut(target_roles=["Engineer"], suggested_work_experience=[WorkExperienceIn(company="Acme", position="Engineer")])
        with sessions() as session:
            session.add(User(id=1, telegram_id=1))
            session.add(UserProfile(user_id=1, target_roles=["Engineer"]))
            session.commit()
            tokens = [prepare_preview(session, 1, "session", draft, store).token for _ in range(2)]
        def apply(token):
            with sessions() as session:
                try:
                    apply_import(session, 1, "session", token, store)
                    return "success"
                except CVImportError as error:
                    return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(apply, tokens)) == ["cv_import_stale", "success"]
        with sessions() as session:
            assert session.query(WorkExperience).count() == 1
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()
