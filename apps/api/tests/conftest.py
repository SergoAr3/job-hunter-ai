import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

# Explicit test-only trust configuration; never a production default.
os.environ["BOT_API_SERVICE_TOKEN"] = "identity-tests-server-only-service-token"
os.environ["APP_ENV"] = "test"
os.environ["AUTH_MAIL_DELIVERY"] = "capture"
os.environ["AUTH_MAIL_SINK_DIR"] = tempfile.mkdtemp(prefix="auth-tests-mail-")
os.environ["WEB_PUBLIC_ORIGIN"] = "http://127.0.0.1:3100"

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
    from app.services import auth_limits, auth_mail
    auth_limits.limiter.buckets.clear()
    auth_mail.get_mailer.cache_clear()
    for mail in Path(os.environ["AUTH_MAIL_SINK_DIR"]).glob("*.json"):
        mail.unlink()
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


def mail_token(email, purpose="verify"):
    import json
    matches = []
    for path in Path(os.environ["AUTH_MAIL_SINK_DIR"]).glob("*.json"):
        item = json.loads(path.read_text())
        if item["to"].lower() == email.strip().lower() and item["purpose"] == purpose:
            matches.append((path.stat().st_mtime_ns, item))
    assert matches, "Expected captured email"
    return parse_qs(urlsplit(max(matches, key=lambda item: item[0])[1]["link"]).fragment)["token"][0]


def verify_account(client, email):
    response = client.post("/auth/email/verify", json={"token": mail_token(email)})
    assert response.status_code in {200, 400}  # Replayed proof is already terminal.
    if response.status_code == 400:
        assert response.json()["detail"]["code"] == "EMAIL_TOKEN_USED"
