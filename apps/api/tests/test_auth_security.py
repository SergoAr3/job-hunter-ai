"""Explicit dev trust boundary, fail-closed configuration and persistence faults."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.auth_config import AuthSettings, get_auth_settings
from app.auth_dependencies import DEV_HEADER
from app.database import get_session
from app.main import app
from app.models import AuthSession, User
from app.services import auth
from conftest import TestSessionLocal, client as bot_client
from test_auth import PASSWORD, SERVICE_TOKEN, bearer, login, plain, register

DEV_TOKEN = "web-tests-server-only-dev-token-123456"
RANDOM_TOKEN = "Z4RpqK72C0vjA6GyD5tN3xQbU8mW1eLfH9sIoBVkzEY"


@pytest.fixture
def dev_user():
    uid = bot_client.post("/users/telegram", json={"telegram_id": 123, "first_name": "Actor"}).json()["id"]
    app.dependency_overrides[get_auth_settings] = lambda: AuthSettings(
        "development", "legacy-development", SERVICE_TOKEN, DEV_TOKEN, uid,
    )
    try:
        yield uid
    finally:
        app.dependency_overrides.pop(get_auth_settings, None)


@pytest.mark.parametrize("peer,headers,allowed", [
    ("127.0.0.1", {}, False),  # Direct or local proxy without forwarding.
    ("127.0.0.1", {"X-Forwarded-For": "127.0.0.1"}, False),
    ("127.0.0.1", {"X-Forwarded-For": "203.0.113.9", "Host": "127.0.0.1"}, False),
    ("203.0.113.9", {"Host": "127.0.0.1", "X-Forwarded-For": "127.0.0.1"}, False),
    ("203.0.113.9", {DEV_HEADER: DEV_TOKEN}, False),
    ("127.0.0.1", {DEV_HEADER: "wrong"}, False),
    ("127.0.0.1", {DEV_HEADER: DEV_TOKEN}, True),
    ("::1", {DEV_HEADER: DEV_TOKEN}, True),
    ("127.0.0.1", {DEV_HEADER: DEV_TOKEN, "Authorization": "Bearer " + "a" * 43}, False),
    ("127.0.0.1", {DEV_HEADER: DEV_TOKEN, "Authorization": "malformed"}, False),
    ("127.0.0.1", {DEV_HEADER: DEV_TOKEN, "X-Bot-Service-Token": "wrong"}, False),
])
def test_dev_access_requires_all_boundaries(dev_user, peer, headers, allowed):
    local = TestClient(app, base_url="http://127.0.0.1", client=(peer, 50000))
    response = local.get(f"/users/{dev_user}/applications", headers=headers)
    assert response.status_code == (200 if allowed else 401)
    assert DEV_TOKEN not in response.text and SERVICE_TOKEN not in response.text
    assert local.get(f"/users/{dev_user + 999}/applications", headers={DEV_HEADER: DEV_TOKEN}).status_code == 401
    assert local.post("/users/telegram", headers={DEV_HEADER: DEV_TOKEN}, json={"telegram_id": 234, "first_name": "Other"}).status_code == 401


def test_duplicate_dev_header_and_session_identity_cannot_be_overridden(dev_user):
    local = TestClient(app, client=("127.0.0.1", 50000))
    assert local.get(f"/users/{dev_user}/applications", headers=[(DEV_HEADER, DEV_TOKEN)] * 2).status_code == 401
    register()
    token = login()["session_token"]
    with TestSessionLocal() as session:
        owner = session.scalar(select(User.id).where(User.email.is_not(None)))
    headers = {**bearer(token), DEV_HEADER: DEV_TOKEN}
    assert local.get(f"/users/{owner}/applications", headers=headers).status_code == 200
    assert local.get(f"/users/{dev_user}/applications", headers=headers).status_code == 404


def test_all_27_operations_require_explicit_dev_credential(dev_user):
    import re
    local = TestClient(app, client=("127.0.0.1", 50000))
    count = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if "{user_id}" not in path:
            continue
        path = re.sub(r"\{[^}]+\}", "9999", path)
        for method in route.methods:
            count += 1
            assert local.request(method, path).status_code == 401
            assert local.request(method, path, headers={DEV_HEADER: DEV_TOKEN}).status_code == 401
    assert count == 27


@pytest.mark.parametrize("token", ["", " " * 40, "short", "replace-with-a-secure-random-service-token", "identity-tests-server-only-service-token", "explicit-server-only-test-token-123456", "development-only-placeholder-token-123456", "a" * 43])
def test_production_rejects_unsafe_bot_secrets(token):
    with pytest.raises(RuntimeError, match="BOT_API_SERVICE_TOKEN"):
        AuthSettings("production", "enforced", token)


def test_valid_production_and_development_settings():
    assert AuthSettings("production", "enforced", RANDOM_TOKEN).bot_service_token == RANDOM_TOKEN
    assert AuthSettings("development", "enforced", SERVICE_TOKEN).bot_service_token == SERVICE_TOKEN
    for env, mode in [("production", "enforced"), ("test", "enforced"), ("development", "enforced")]:
        with pytest.raises(RuntimeError, match="development legacy mode"):
            AuthSettings(env, mode, RANDOM_TOKEN, DEV_TOKEN, 1)
    for token, uid in [(DEV_TOKEN, None), ("", 1), (DEV_TOKEN, 0), (SERVICE_TOKEN, 1)]:
        with pytest.raises(RuntimeError):
            AuthSettings("development", "legacy-development", SERVICE_TOKEN, token, uid)


def test_production_lifespan_rejects_placeholder(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_ROLLOUT_MODE", "enforced")
    monkeypatch.setenv("BOT_API_SERVICE_TOKEN", "replace-with-a-secure-random-service-token")
    monkeypatch.delenv("WEB_DEV_API_TOKEN", raising=False)
    get_auth_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="BOT_API_SERVICE_TOKEN"):
            with TestClient(app):
                pass
    finally:
        get_auth_settings.cache_clear()


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
