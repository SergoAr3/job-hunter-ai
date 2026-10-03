"""Session-only user access, Bot boundary, settings and persistence faults."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from app.auth_config import AuthSettings, get_auth_settings
from app.database import get_session
from app.main import app
from app.models import AuthSession, User
from app.services import auth
from conftest import TestSessionLocal, client as bot_client
from test_auth import PASSWORD, SERVICE_TOKEN, bearer, login, plain, register

OBSOLETE_HEADER = "X-Web-Dev-Api-Token"
OBSOLETE_TOKEN = "web-tests-server-only-dev-token-123456"
RANDOM_TOKEN = "Z4RpqK72C0vjA6GyD5tN3xQbU8mW1eLfH9sIoBVkzEY"

@pytest.fixture
def existing_telegram(monkeypatch):
    uid = bot_client.post("/users/telegram", json={"telegram_id": 123, "first_name": "Actor"}).json()["id"]
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_ROLLOUT_MODE", "legacy-development")
    monkeypatch.setenv("WEB_DEV_USER_ID", str(uid))
    monkeypatch.setenv("WEB_DEV_API_TOKEN", OBSOLETE_TOKEN)
    get_auth_settings.cache_clear()
    try: yield uid
    finally: get_auth_settings.cache_clear()

@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "203.0.113.9"])
@pytest.mark.parametrize("headers", [
    {}, {"X-Forwarded-For":"127.0.0.1", "Host":"localhost"},
    {OBSOLETE_HEADER:OBSOLETE_TOKEN}, {OBSOLETE_HEADER:"wrong"},
    {OBSOLETE_HEADER:OBSOLETE_TOKEN, "Authorization":"malformed"},
    {OBSOLETE_HEADER:OBSOLETE_TOKEN, "X-Bot-Service-Token":"wrong"},
])
def test_obsolete_identity_cannot_authorize_even_on_loopback(existing_telegram, peer, headers):
    local = TestClient(app, client=(peer, 50000))
    assert local.get(f"/users/{existing_telegram}/applications", headers=headers).status_code == 401
    assert local.post("/users/telegram", headers=headers, json={"telegram_id":234}).status_code == 401


def test_real_session_cannot_be_overridden_by_obsolete_headers(existing_telegram):
    register(); token = login()["session_token"]
    with TestSessionLocal() as db: owner = db.scalar(select(User.id).where(User.email.is_not(None)))
    headers = {**bearer(token), OBSOLETE_HEADER:OBSOLETE_TOKEN}
    assert plain.get(f"/users/{owner}/applications", headers=headers).status_code == 200
    assert plain.get(f"/users/{existing_telegram}/applications", headers=headers).status_code == 404


def test_all_28_operations_reject_anonymous_and_obsolete_credential(existing_telegram):
    import re
    local = TestClient(app, client=("127.0.0.1", 50000)); count = 0
    for route in app.routes:
        path = getattr(route,"path", "")
        if "{user_id}" not in path: continue
        path = re.sub(r"\{[^}]+\}", str(existing_telegram), path)
        for method in route.methods:
            count += 1
            assert local.request(method,path).status_code == 401
            assert local.request(method,path,headers={OBSOLETE_HEADER:OBSOLETE_TOKEN}).status_code == 401
    assert count == 28

@pytest.mark.parametrize("token", ["", " "*40, "short", "replace-with-a-secure-random-service-token", "identity-tests-server-only-service-token", "explicit-server-only-test-token-123456", "development-only-placeholder-token-123456", "a"*43])
def test_production_rejects_unsafe_bot_secrets(token):
    with pytest.raises(RuntimeError,match="BOT_API_SERVICE_TOKEN"): AuthSettings("production", token)


def test_settings_surface_has_only_environment_and_bot_credential(existing_telegram):
    from dataclasses import fields
    assert {f.name for f in fields(AuthSettings)} == {"environment", "bot_service_token"}
    assert AuthSettings("production",RANDOM_TOKEN).bot_service_token == RANDOM_TOKEN
    assert get_auth_settings() == AuthSettings("development",SERVICE_TOKEN)


def test_production_lifespan_rejects_placeholder(monkeypatch):
    monkeypatch.setenv("APP_ENV","production")
    monkeypatch.setenv("BOT_API_SERVICE_TOKEN","replace-with-a-secure-random-service-token")
    get_auth_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError,match="BOT_API_SERVICE_TOKEN"):
            with TestClient(app): pass
    finally: get_auth_settings.cache_clear()


@pytest.mark.parametrize("failure", ["collision", "database"])
def test_session_persistence_failure_is_sanitized_and_rolls_back(monkeypatch, caplog, failure):
    register()
    raw = "A" * 43
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda size: raw)
    login()
    digest = auth.token_hash(raw)
    with TestSessionLocal() as session:
        previous = app.dependency_overrides[get_session]
        app.dependency_overrides[get_session] = lambda: session
        original_commit = session.commit
        if failure == "database":
            def fail_commit():
                raise OperationalError("sensitive SQL " + digest, {"raw": raw}, RuntimeError(digest))
            monkeypatch.setattr(session, "commit", fail_commit)
        try:
            response = plain.post("/auth/login", json={"email": "user+tag@example.com", "password": PASSWORD})
            assert response.status_code == 503
            assert response.json() == {"detail": {"code": "AUTH_UNAVAILABLE"}}
            assert raw not in response.text and digest not in response.text
            assert session.is_active
            assert session.scalar(select(User.id)) is not None
            assert session.query(AuthSession).count() == 1
            monkeypatch.setattr(session, "commit", original_commit)
            user = session.scalar(select(User))
            user.display_name = "Subsequent write"
            session.commit()
            session.expire_all()
            assert session.scalar(select(User)).display_name == "Subsequent write"
            assert raw not in caplog.text and digest not in caplog.text
        finally:
            app.dependency_overrides[get_session] = previous
