import secrets
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.models import User, TelegramChallenge, AuthSession, UserProfile, Job, Application
from app.services import auth, telegram_auth
from conftest import TestSessionLocal
from test_auth import register, login, bearer, PASSWORD, SERVICE_TOKEN

client = TestClient(app)
BOT = {"X-Bot-Service-Token": SERVICE_TOKEN}
TG = {"telegram_id": 123, "username": "actor", "first_name": "Actor", "last_name": None, "language_code": "ru"}


@pytest.fixture(autouse=True)
def bot_name(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "identity_bot")


def challenge(purpose="login", headers=None, password=PASSWORD):
    binding = secrets.token_urlsafe(32)
    body = {"purpose": purpose, "binding": binding}
    if purpose == "link": body["password"] = password
    response = client.post("/auth/telegram/challenges", json=body, headers=headers or {})
    assert response.status_code == 200, response.text
    result = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert len("auth_" + result["token"]) <= 64
    return {"token": result["token"], "binding": binding, "purpose": purpose}


def approve(body, telegram=None):
    return client.post("/auth/telegram/bot/approve", json={"token": body["token"], "telegram": telegram or TG}, headers=BOT)


def complete(body, headers=None):
    return client.post("/auth/telegram/complete", json=body, headers=headers or {})


def uid(token):
    return client.get("/auth/internal/principal", headers=bearer(token)).json()["user_id"]


def test_existing_telegram_login_preserves_entire_account_and_data():
    with TestSessionLocal() as db:
        user = User(**TG, email="a@example.com", email_canonical="a@example.com", password_hash=auth.get_passwords().hash(PASSWORD), display_name="Keep")
        db.add(user); db.flush(); user_id = user.id
        db.add(UserProfile(user_id=user_id, target_roles=["Old role"]))
        job = Job(source="hh", source_url="https://hh.ru/vacancy/old", title="Old job")
        db.add(job); db.flush()
        application = Application(user_id=user_id, job_id=job.id)
        db.add(application); db.commit(); application_id = application.id
    body = challenge()
    assert approve(body).status_code == 200
    response = complete(body)
    assert response.status_code == 200
    token = response.json()["session_token"]
    assert uid(token) == user_id
    headers = bearer(token)
    assert client.get(f"/users/{user_id}/profile", headers=headers).json()["target_roles"] == ["Old role"]
    assert client.get(f"/users/{user_id}/applications/{application_id}", headers=headers).status_code == 200
    assert client.put(f"/users/{user_id}/applications/{application_id}/status", json={"status":"applied"}, headers=headers).status_code == 200
    assert client.get(f"/users/{user_id}/applications/{application_id}/status-history", headers=headers).json()["items"]
    with TestSessionLocal() as db:
        assert db.query(User).count() == 1
        user = db.get(User, user_id)
        assert user.email == "a@example.com" and user.display_name == "Keep" and user.password_hash
        record = db.get(TelegramChallenge, auth.token_hash(body["token"]))
        assert record.token_hash != body["token"] and record.binding_hash != body["binding"]
        assert record.outcome == "completed"
    assert complete(body).status_code == 409
    assert approve(body).status_code == 409


def test_new_telegram_login_creates_one_identity_without_profile():
    body = challenge()
    assert approve(body).status_code == 200
    first = complete(body).json()
    assert first["me"]["profile_exists"] is False and first["me"]["email"] is None
    second = challenge(); assert approve(second).status_code == 200
    assert uid(complete(second).json()["session_token"]) == uid(first["session_token"])
    with TestSessionLocal() as db:
        assert db.query(User).count() == 1 and db.query(AuthSession).count() == 2
        assert db.query(UserProfile).count() == 0


def test_link_same_account_and_both_login_methods():
    register("email@example.com", display_name="Preserved")
    token = login("email@example.com")["session_token"]; user_id = uid(token); headers = bearer(token)
    body = challenge("link", headers)
    assert approve(body).status_code == 200
    linked = complete(body, headers)
    assert linked.status_code == 200 and linked.json()["me"]["telegram_linked"] is True
    assert "session_token" not in linked.text and "user_id" not in linked.text
    again = challenge("link", headers); approve(again)
    assert complete(again, headers).status_code == 200  # Same identity is idempotent.
    telegram = challenge(); approve(telegram)
    assert uid(complete(telegram).json()["session_token"]) == user_id
    assert uid(login("email@example.com")["session_token"]) == user_id
    with TestSessionLocal() as db:
        assert db.query(User).count() == 1
        user = db.get(User, user_id)
        assert user.telegram_id == 123 and user.email == "email@example.com" and user.display_name == "Preserved"


def test_conflict_is_terminal_no_transfer_or_merge():
    with TestSessionLocal() as db:
        user = User(**TG); db.add(user); db.commit(); old_id = user.id
    register("email@example.com"); token = login("email@example.com")["session_token"]
    headers = bearer(token); new_id = uid(token)
    body = challenge("link", headers); approve(body)
    assert complete(body, headers).json() == {"detail":{"code":"ACCOUNT_LINK_CONFLICT"}}
    assert client.post("/auth/telegram/status", json=body, headers=headers).json() == {"status":"conflict"}
    assert complete(body, headers).status_code == 409
    with TestSessionLocal() as db:
        assert db.get(User, old_id).telegram_id == 123
        assert db.get(User, new_id).telegram_id is None
        assert db.query(User).count() == 2 and db.query(AuthSession).count() == 1


@pytest.mark.parametrize("terminal", ["expired", "cancelled", "pending"])
def test_terminal_or_unapproved_cannot_complete(terminal):
    body = challenge()
    if terminal == "expired":
        with TestSessionLocal() as db:
            record = db.get(TelegramChallenge, auth.token_hash(body["token"]))
            record.created_at = auth.utc_now() - timedelta(minutes=10); record.expires_at = auth.utc_now() - timedelta(minutes=5); db.commit()
    elif terminal == "cancelled":
        assert client.post("/auth/telegram/bot/cancel", json={"token":body["token"],"telegram":TG}, headers=BOT).status_code == 200
    assert complete(body).status_code in (409,410)
    assert client.post("/auth/telegram/status", json=body).json() == {"status":terminal}
    with TestSessionLocal() as db: assert db.query(AuthSession).count() == 0 and db.query(User).count() == 0


def test_binding_purpose_and_initiating_session_enforced():
    body = challenge(); approve(body)
    assert complete(body | {"binding":secrets.token_urlsafe(32)}).status_code == 400
    assert complete(body | {"purpose":"link"}).status_code == 400
    register("b@example.com"); token = login("b@example.com")["session_token"]
    assert complete(body, bearer(token)).status_code == 400
    link = challenge("link", bearer(token)); approve(link)
    assert complete(link).status_code == 401
    second = login("b@example.com")["session_token"]
    assert complete(link, bearer(second)).status_code == 400
    register("c@example.com"); other = login("c@example.com")["session_token"]
    assert complete(link, bearer(other)).status_code == 400
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 204
    assert complete(link, bearer(token)).status_code == 401
    assert complete(body).status_code == 200  # Wrong binding/purpose didn't consume.


def test_link_requires_password_and_rejects_browser_identity_input():
    register(); token = login()["session_token"]
    for headers, password in [({}, PASSWORD),(bearer(token),None),(bearer(token),"wrong password of enough length")]:
        data={"purpose":"link","binding":secrets.token_urlsafe(32),"password":password}
        assert client.post("/auth/telegram/challenges", json=data, headers=headers).status_code == 401
    body = challenge()
    assert complete(body | {"telegram_id":123}).status_code == 422
    with TestSessionLocal() as db: assert db.query(TelegramChallenge).count() == 1


@pytest.mark.parametrize("operation", ["inspect","approve","cancel"])
def test_bot_boundary_rejects_browser_dev_and_anonymous(operation):
    body=challenge(); register(); token=login()["session_token"]
    payload={"token":body["token"]}
    if operation != "inspect": payload["telegram"]=TG
    for headers in ({}, bearer(token), {"X-Web-Dev-Api-Token":SERVICE_TOKEN}):
        assert client.post(f"/auth/telegram/bot/{operation}", json=payload, headers=headers).status_code == 401
    response=client.post(f"/auth/telegram/bot/{operation}", json=payload, headers=BOT)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    for secret in [body["token"],body["binding"],SERVICE_TOKEN,"telegram_id","user_id"]:
        assert secret not in response.text


def test_completion_failure_rolls_back_consume_identity_and_session(monkeypatch):
    body=challenge(); approve(body)
    def failure(*args):
        from sqlalchemy.exc import OperationalError
        raise OperationalError("failure", {}, Exception("unavailable"))
    monkeypatch.setattr(auth,"issue_session",failure)
    assert complete(body).status_code == 503
    with TestSessionLocal() as db:
        assert db.query(User).count() == 0 and db.query(AuthSession).count() == 0
        assert db.get(TelegramChallenge,auth.token_hash(body["token"])).consumed_at is None


def test_linked_email_and_telegram_sessions_keep_data_isolated():
    register("b@example.com"); email_token=login("b@example.com")["session_token"]; b_id=uid(email_token)
    with TestSessionLocal() as db:
        a=User(**TG);db.add(a);db.flush();a_id=a.id
        app_ids={}
        for owner,label in [(a_id,"A"),(b_id,"B")]:
            db.add(UserProfile(user_id=owner,target_roles=[label]))
            job=Job(source="hh",source_url=f"https://hh.ru/vacancy/{label}",title=label);db.add(job);db.flush()
            item=Application(user_id=owner,job_id=job.id);db.add(item);db.flush();app_ids[label]=item.id
        db.commit()
    body=challenge("link",bearer(email_token));approve(body,TG|{"telegram_id":222});assert complete(body,bearer(email_token)).status_code==200
    telegram=challenge();approve(telegram,TG|{"telegram_id":222});tg_token=complete(telegram).json()["session_token"]
    for token in [tg_token,login("b@example.com")["session_token"]]:
        assert uid(token)==b_id;headers=bearer(token)
        assert client.get(f"/users/{b_id}/profile",headers=headers).json()["target_roles"]==["B"]
        assert [item["app_id"] for item in client.get(f"/users/{b_id}/applications",headers=headers).json()["items"]]==[app_ids["B"]]
        assert client.get(f"/users/{b_id}/applications/{app_ids['A']}",headers=headers).status_code==404
        assert client.put(f"/users/{b_id}/applications/{app_ids['A']}/status",json={"status":"applied"},headers=headers).status_code==404
        assert client.put(f"/users/{b_id}/applications/{app_ids['B']}/status",json={"status":"applied"},headers=headers).status_code==200
    with TestSessionLocal() as db:
        assert db.get(User,a_id).telegram_id==123 and db.get(User,b_id).telegram_id==222
        assert db.get(Application,app_ids["A"]).status=="saved"
        assert db.query(User).count()==2


def test_link_commit_failure_rolls_back_identity_and_consume(monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import OperationalError
    register();token=login()["session_token"];headers=bearer(token);user_id=uid(token)
    body=challenge("link",headers);approve(body)
    original=Session.commit
    def failure(self):raise OperationalError("commit",{},Exception("unavailable"))
    monkeypatch.setattr(Session,"commit",failure)
    assert complete(body,headers).status_code==503
    monkeypatch.setattr(Session,"commit",original)
    with TestSessionLocal() as db:
        assert db.get(User,user_id).telegram_id is None
        assert db.get(TelegramChallenge,auth.token_hash(body["token"])).consumed_at is None
        assert db.query(AuthSession).count()==1


def test_approval_cannot_be_replaced_and_cancel_requires_same_verified_actor():
    body=challenge();assert approve(body).status_code==200
    assert approve(body,TG|{"telegram_id":456}).status_code==409
    assert client.post("/auth/telegram/bot/cancel",json={"token":body["token"],"telegram":TG|{"telegram_id":456}},headers=BOT).status_code==409
    assert complete(body).status_code==200


def test_exact_expiry_after_approval_is_rejected(monkeypatch):
    body=challenge();approve(body)
    with TestSessionLocal() as db:expires=auth.aware(db.get(TelegramChallenge,auth.token_hash(body["token"])).expires_at)
    monkeypatch.setattr(auth,"utc_now",lambda:expires)
    assert complete(body).status_code==410
    assert approve(body).status_code==409
    with TestSessionLocal() as db: assert db.query(User).count()==0 and db.query(AuthSession).count()==0

@pytest.mark.parametrize("approved", [False, True])
def test_browser_cancel_owned_login_is_terminal_without_session(approved):
    body = challenge()
    if approved: assert approve(body).status_code == 200
    response = client.post('/auth/telegram/cancel', json=body)
    assert response.status_code == 200 and response.json() == {'ok': True}
    assert response.headers['cache-control'] == 'no-store'
    assert complete(body).status_code == 409 and approve(body).status_code == 409
    assert client.post('/auth/telegram/cancel', json=body).status_code == 409
    with TestSessionLocal() as db:
        assert db.query(AuthSession).count() == 0 and db.query(User).count() == 0
        assert db.get(TelegramChallenge, auth.token_hash(body['token'])).outcome == 'cancelled'

@pytest.mark.parametrize('variant', ['binding', 'purpose', 'bot', 'bearer'])
def test_browser_cancel_requires_login_binding_and_browser_boundary(variant):
    body = challenge(); payload = body.copy(); headers = {}
    if variant == 'binding': payload['binding'] = secrets.token_urlsafe(32)
    if variant == 'purpose': payload['purpose'] = 'link'
    if variant == 'bot': headers = BOT
    if variant == 'bearer':
        register('cancel@example.com'); headers = bearer(login('cancel@example.com')['session_token'])
    assert client.post('/auth/telegram/cancel', json=payload, headers=headers).status_code in (400, 401)
    with TestSessionLocal() as db:
        assert db.get(TelegramChallenge, auth.token_hash(body['token'])).outcome is None

def test_browser_cancel_cannot_change_link_challenge():
    register('link-cancel@example.com'); headers=bearer(login('link-cancel@example.com')['session_token'])
    body=challenge('link', headers)
    assert client.post('/auth/telegram/cancel', json=body, headers=headers).status_code == 400
    assert approve(body).status_code == 200 and complete(body, headers).status_code == 200

def test_browser_cancel_database_failure_rolls_back(monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import OperationalError
    body=challenge()
    def fail(self): raise OperationalError('commit', {}, Exception('unavailable'))
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail)
        assert client.post('/auth/telegram/cancel', json=body).status_code == 503
    with TestSessionLocal() as db:
        assert db.get(TelegramChallenge, auth.token_hash(body['token'])).outcome is None
        assert db.query(AuthSession).count() == 0


def test_removed_web_credential_is_only_an_ignored_header_not_identity():
    body = challenge()
    headers = {"X-Web-Dev-Api-Token": "obsolete-not-authentication"}
    assert client.post('/auth/telegram/cancel', json=body | {"binding":secrets.token_urlsafe(32)}, headers=headers).status_code == 400
    assert client.post('/auth/telegram/cancel', json=body, headers=headers).status_code == 200
    with TestSessionLocal() as db:
        assert db.query(AuthSession).count() == 0 and db.query(User).count() == 0
