import os

# Explicit test-only trust configuration; never a production default.
os.environ["BOT_API_SERVICE_TOKEN"] = "identity-tests-server-only-service-token"
os.environ["APP_ENV"] = "test"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_session
from app.main import app
import app.main as main_module

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


TestSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def override_get_session():
    with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_session] = override_get_session
client = TestClient(app, headers={"X-Bot-Service-Token": os.environ["BOT_API_SERVICE_TOKEN"]})


class StubEnrichmentService:
    def preflight(self, url: str) -> None:
        return None

    def enrich(self, url: str):
        return None, "fetch_timeout"

    @staticmethod
    def values(data):
        return {}


main_module.enrichment_service = StubEnrichmentService()


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture
def concurrent_database(tmp_path):
    """Independent connections for concurrent requests; StaticPool shares one."""
    concurrent_engine = create_engine(
        f"sqlite:///{tmp_path / 'requests.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    event.listen(concurrent_engine, "connect", enable_sqlite_foreign_keys)
    Base.metadata.create_all(concurrent_engine)
    request_sessions = sessionmaker(bind=concurrent_engine, expire_on_commit=False)
    previous = app.dependency_overrides[get_session]
    def request_session():
        with request_sessions() as session:
            yield session
    app.dependency_overrides[get_session] = request_session
    try:
        yield request_sessions
    finally:
        app.dependency_overrides[get_session] = previous
        concurrent_engine.dispose()
