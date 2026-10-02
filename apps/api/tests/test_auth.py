from datetime import timedelta

import pytest
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth_config import AuthSettings, get_auth_settings, SESSION_TOUCH_INTERVAL
from app.auth_dependencies import require_user_access
from app.main import app
from app.models import AuthSession, User, UserProfile, Application, Job
from app.services import auth
from conftest import TestSessionLocal, client as bot_client

PASSWORD = "  Unicode пароль 🔑  "
SERVICE_TOKEN = "identity-tests-server-only-service-token"
plain = TestClient(app)


def register(email="User+tag@Example.com", password=PASSWORD, **extra):
    return plain.post("/auth/register", json={"email": email, "password": password, **extra})


def login(email="user+tag@example.com", password=PASSWORD):
    response = plain.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_registration_login_me_logout_no_leaks(caplog):
    assert register("  User+tag@Example.com  ", display_name=" Name ").status_code == 202
    with TestSessionLocal() as session:
        user = session.scalar(select(User))
        assert user.telegram_id is None and user.first_name is None and user.email_verified_at is None
        assert (user.email, user.email_canonical, user.display_name) == ("User+tag@Example.com", "user+tag@example.com", "Name")
        assert user.password_hash != PASSWORD and user.password_hash.startswith("$argon2id$")
        assert auth.get_passwords().verify(user.password_hash, PASSWORD)
        assert session.query(UserProfile).count() == 0
        uid, encoded = user.id, user.password_hash
    data = login()
    token = data["session_token"]
    assert len(token) == 43
    with TestSessionLocal() as session:
        record = session.scalar(select(AuthSession))
        assert record.token_hash == auth.token_hash(token) and record.token_hash != token and record.user_id == uid
        assert auth.aware(record.expires_at) - auth.aware(record.created_at) == timedelta(days=7)
    me = plain.get("/auth/me", headers=bearer(token))
    assert me.status_code == 200 and me.json() == data["me"]
    assert set(me.json()) == {"email", "email_verified", "telegram_linked", "display_name", "profile_exists", "created_at"}
    assert not me.json()["profile_exists"] and not me.json()["email_verified"]
    assert encoded not in me.text and token not in me.text and me.headers["cache-control"] == "no-store"
    assert plain.post("/auth/logout", headers=bearer(token)).status_code == 204
    assert plain.post("/auth/logout", headers=bearer(token)).status_code == 204
    assert plain.get("/auth/me", headers=bearer(token)).status_code == 401
    assert all(secret not in caplog.text for secret in (PASSWORD, token, SERVICE_TOKEN))


def test_duplicate_registration_is_generic_and_never_overwrites():
    a = register(display_name="First")
    b = register("USER+TAG@example.com", password="Different password 123", display_name="Changed")
    assert a.status_code == b.status_code == 202 and a.json() == b.json()
    assert login()["me"]["display_name"] == "First"
    with TestSessionLocal() as session:
        assert session.query(User).count() == 1


@pytest.mark.parametrize("password", ["a" * 14, "a" * 129, "秘密" * 7])
def test_rejected_password_is_not_echoed(password, caplog):
    response = register(password=password)
    assert response.status_code == 422 and password not in response.text and password not in caplog.text


@pytest.mark.parametrize("password", ["a" * 15, "a" * 128, " " * 15, PASSWORD])
def test_unicode_spaces_and_length_policy(password):
    assert register(password=password).status_code == 202
    assert login(password=password)["session_token"]


@pytest.mark.parametrize("email", ["x", "a@@example.com", "a@", "a@-domain.com", "a b@example.com", "ü@example.com", "a@example.com\x00suffix", "a@example.com\n"])
def test_invalid_email(email):
    assert register(email=email).status_code == 422


def test_generic_login_errors_and_dummy_verify(monkeypatch):
    register()
    passwords, calls = auth.get_passwords(), []
    original = passwords.verify
    def spy(encoded, password):
        calls.append(encoded)
        return original(encoded, password)
    monkeypatch.setattr(passwords, "verify", spy)
    a = plain.post("/auth/login", json={"email": "user+tag@example.com", "password": "Wrong password here"})
    b = plain.post("/auth/login", json={"email": "unknown@example.com", "password": PASSWORD})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": {"code": "AUTH_INVALID_CREDENTIALS"}}
    assert passwords.dummy_hash in calls
    with TestSessionLocal() as session:
        assert session.query(AuthSession).count() == 0


def test_salts_verification_rehash_and_hash_concurrency_guard():
    passwords = auth.get_passwords()
    a, b = passwords.hash(PASSWORD), passwords.hash(PASSWORD)
    assert a != b and passwords.verify(a, PASSWORD) and not passwords.verify(a, PASSWORD.strip())
    assert not passwords.verify(a, "Wrong password here") and not passwords.needs_rehash(a)
    old = PasswordHasher(type=Type.ID, time_cost=1, memory_cost=1024, parallelism=1).hash(PASSWORD)
    with TestSessionLocal() as session:
        session.add(User(email="user+tag@example.com", email_canonical="user+tag@example.com", password_hash=old))
        session.commit()
    login()
    with TestSessionLocal() as session:
        assert not passwords.needs_rehash(session.scalar(select(User)).password_hash)
    assert passwords.slots.acquire(False) and passwords.slots.acquire(False)
    try:
        assert register("other@example.com").status_code == 503
    finally:
        passwords.slots.release()
        passwords.slots.release()


def test_rehash_failure_preserves_valid_login(monkeypatch):
    old = PasswordHasher(type=Type.ID, time_cost=1, memory_cost=1024, parallelism=1).hash(PASSWORD)
    with TestSessionLocal() as session:
        session.add(User(email="user+tag@example.com", email_canonical="user+tag@example.com", password_hash=old))
        session.commit()
    def fail(password):
        raise auth.AuthError("AUTH_UNAVAILABLE", 503)
    monkeypatch.setattr(auth.get_passwords(), "hash", fail)
    assert login()["session_token"]
    with TestSessionLocal() as session:
        assert session.scalar(select(User)).password_hash == old


@pytest.mark.parametrize("state", ["absolute", "idle", "revoked"])
def test_invalid_session_states(state):
    register()
    token, now = login()["session_token"], auth.utc_now()
    with TestSessionLocal() as session:
        record = session.get(AuthSession, auth.token_hash(token))
        if state == "absolute":
            record.created_at = record.last_seen_at = now - timedelta(days=8)
            record.expires_at = now - timedelta(seconds=1)
        elif state == "idle":
            record.created_at, record.last_seen_at = now - timedelta(days=2), now - timedelta(hours=24)
        else:
            record.revoked_at = now
        session.commit()
    assert plain.get("/auth/me", headers=bearer(token)).json() == {"detail": {"code": "AUTH_REQUIRED"}}


def test_independent_sessions_and_touch_throttle():
    register()
    first, second = login()["session_token"], login()["session_token"]
    with TestSessionLocal() as session:
        original = session.get(AuthSession, auth.token_hash(second)).last_seen_at
    plain.get("/auth/me", headers=bearer(second))
    with TestSessionLocal() as session:
        record = session.get(AuthSession, auth.token_hash(second))
        assert record.last_seen_at == original
        record.created_at = auth.utc_now() - timedelta(hours=1)
        record.last_seen_at = auth.utc_now() - SESSION_TOUCH_INTERVAL - timedelta(seconds=1)
        session.commit()
    assert plain.post("/auth/logout", headers=bearer(first)).status_code == 204
    assert plain.get("/auth/me", headers=bearer(second)).status_code == 200
    with TestSessionLocal() as session:
        assert auth.aware(session.get(AuthSession, auth.token_hash(second)).last_seen_at) > auth.utc_now() - timedelta(minutes=1)


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": ""}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"},
    {"Authorization": "Bearer "}, {"Authorization": "Bearer bad"},
    {"Authorization": "Bearer " + "a" * 43 + ", Bearer " + "b" * 43},
    [("Authorization", "Bearer " + "a" * 43), ("Authorization", "Bearer " + "b" * 43)],
    {"Authorization": "Bearer " + "a" * 43, "X-Bot-Service-Token": SERVICE_TOKEN},
])
def test_ambiguous_or_missing_bearer(headers):
    assert plain.get("/auth/me", headers=headers).status_code == 401
    assert plain.post("/auth/logout", headers=headers).status_code == 401


def test_unknown_token_logout_is_idempotent():
    assert plain.post("/auth/logout", headers=bearer("a" * 43)).status_code == 204


def test_ownership_path_and_resource_tampering():
    register()
    token = login()["session_token"]
    with TestSessionLocal() as session:
        owner = session.scalar(select(User))
        other = User(telegram_id=234, first_name="Other")
        session.add(other)
        session.flush()
        job = Job(source="company_site", source_url="https://example.com/job")
        session.add(job)
        session.flush()
        entry = Application(user_id=other.id, job_id=job.id)
        session.add(entry)
        session.commit()
        oid, uid, aid = owner.id, other.id, entry.id
    assert plain.get(f"/users/{oid}/applications", headers=bearer(token)).status_code == 200
    assert plain.get(f"/users/{uid}/applications", headers=bearer(token)).status_code == 404
    assert plain.get(f"/users/{oid}/applications/{aid}", headers=bearer(token)).status_code == 404
    assert plain.get(f"/users/{oid}/applications").status_code == 401
    assert plain.get(f"/users/{oid}/applications", headers=bearer("a" * 43)).status_code == 401
    assert plain.get(f"/users/{uid}/applications", headers={"X-User-ID": str(uid)}).status_code == 401


def test_every_user_route_has_auth_guard():
    for route in app.routes:
        if "{user_id}" in getattr(route, "path", ""):
            assert any(dep.call is require_user_access for dep in route.dependant.dependencies), route.path


def test_bot_boundary_requires_service_credential():
    body = {"telegram_id": 123, "first_name": "Name"}
    assert plain.post("/users/telegram", json=body).status_code == 401
    assert plain.post("/users/telegram", json=body, headers={"X-Bot-Service-Token": "wrong"}).status_code == 401
    a = bot_client.post("/users/telegram", json=body)
    b = bot_client.post("/users/telegram", json={**body, "first_name": "New"})
    assert a.status_code == b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert not b.json()["created"] and SERVICE_TOKEN not in a.text



def test_unsafe_startup_settings_rejected():
    with pytest.raises(RuntimeError, match="only allowed in development"):
        AuthSettings("production", "legacy-development", SERVICE_TOKEN)
    with pytest.raises(RuntimeError, match="BOT_API_SERVICE_TOKEN"):
        AuthSettings("production", "enforced", "")


def test_all_user_routes_reject_anonymous_and_foreign_principal():
    register()
    headers = bearer(login()["session_token"])
    for route in app.routes:
        path = getattr(route, "path", "")
        if "{user_id}" not in path:
            continue
        import re
        path = re.sub(r"\{[^}]+\}", "9999", path)
        for method in route.methods:
            assert plain.request(method, path).status_code == 401, (method, path)
            assert plain.request(method, path, headers=headers).status_code == 404, (method, path)


def test_session_touch_does_not_break_domain_transaction_or_ownership():
    from app.models import ProfileExperienceFact, WorkExperience
    register()
    token = login()["session_token"]
    with TestSessionLocal() as session:
        owner = session.scalar(select(User))
        other = User(telegram_id=234, first_name="Other")
        session.add(other)
        session.flush()
        own_profile, other_profile = UserProfile(user_id=owner.id), UserProfile(user_id=other.id)
        session.add_all([own_profile, other_profile])
        session.flush()
        fact = ProfileExperienceFact(user_profile_id=other_profile.id, text="Private fact")
        experience = WorkExperience(user_profile_id=other_profile.id, company="Private company")
        job = Job(source="company_site", source_url="https://example.com/owned")
        session.add_all([fact, experience, job])
        session.flush()
        own = Application(user_id=owner.id, job_id=job.id)
        session.add(own)
        record = session.get(AuthSession, auth.token_hash(token))
        record.created_at = auth.utc_now() - timedelta(hours=1)
        record.last_seen_at = auth.utc_now() - timedelta(minutes=6)
        session.commit()
        uid, aid, fid, wid = owner.id, own.id, fact.id, experience.id
    headers = bearer(token)
    assert plain.put(f"/users/{uid}/applications/{aid}/status", json={"status": "applied"}, headers=headers).status_code == 200
    assert plain.put(f"/users/{uid}/applications/{aid}/note", json={"note": "Owned note"}, headers=headers).status_code == 200
    assert plain.delete(f"/users/{uid}/profile/experience-facts/{fid}", headers=headers).status_code == 404
    assert plain.delete(f"/users/{uid}/profile/work-experiences/{wid}", headers=headers).status_code == 404
    assert plain.get("/auth/me", headers=headers).json()["profile_exists"]
    with TestSessionLocal() as session:
        assert session.get(Application, aid).note == "Owned note"
        assert session.get(ProfileExperienceFact, fid) is not None
        assert session.get(WorkExperience, wid) is not None
        assert auth.aware(session.get(AuthSession, auth.token_hash(token)).last_seen_at) > auth.utc_now() - timedelta(minutes=1)


def test_invalid_unicode_password_is_safe_validation_error():
    password = "a" * 15 + "\ud800"
    import json
    response = plain.post("/auth/register", content=json.dumps({"email": "user@example.com", "password": password}),
                          headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_telegram_only_cannot_email_login_and_hybrid_me_preserves_data():
    response = bot_client.post("/users/telegram", json={"telegram_id": 123, "first_name": "Telegram actor"})
    uid = response.json()["id"]
    assert plain.post("/auth/login", json={"email": "actor@example.com", "password": PASSWORD}).json() == {"detail": {"code": "AUTH_INVALID_CREDENTIALS"}}
    with TestSessionLocal() as session:
        user = session.get(User, uid)
        assert user.password_hash is None and user.email is None
        user.email = user.email_canonical = "actor@example.com"
        user.password_hash = auth.get_passwords().hash(PASSWORD)
        session.add(UserProfile(user_id=uid))
        session.commit()
    data = login(email="actor@example.com")
    assert data["me"]["telegram_linked"] and data["me"]["profile_exists"]
    assert bot_client.post("/users/telegram", json={"telegram_id": 123, "first_name": "Refreshed"}).json()["id"] == uid
    with TestSessionLocal() as session:
        user = session.get(User, uid)
        assert user.email == "actor@example.com" and auth.get_passwords().verify(user.password_hash, PASSWORD)
        assert user.first_name == "Refreshed"


def test_credential_change_during_verification_cannot_issue_stale_session(monkeypatch):
    register()
    passwords, changed = auth.get_passwords(), False
    original_verify = passwords.verify
    replacement = passwords.hash("A replacement password")
    def verify_then_change(encoded, password):
        nonlocal changed
        valid = original_verify(encoded, password)
        if not changed:
            changed = True
            with TestSessionLocal() as session:
                session.scalar(select(User)).password_hash = replacement
                session.commit()
        return valid
    monkeypatch.setattr(passwords, "verify", verify_then_change)
    response = plain.post("/auth/login", json={"email": "user+tag@example.com", "password": PASSWORD})
    assert response.status_code == 401 and response.json() == {"detail": {"code": "AUTH_INVALID_CREDENTIALS"}}
    with TestSessionLocal() as session:
        assert session.query(AuthSession).count() == 0
        assert session.scalar(select(User)).password_hash == replacement
