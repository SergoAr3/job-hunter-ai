"""Deterministic budgets; known/unknown parity, no forwarded-header trust."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services import auth_limits, auth
from app.services.auth_limits import Limiter
from test_auth import PASSWORD, register


@pytest.fixture
def clock(monkeypatch):
    now=[100.0]
    limiter=Limiter(clock=lambda:now[0])
    monkeypatch.setattr(auth_limits,"limiter",limiter)
    return now,limiter


def test_atomic_multi_budget_threshold_retry_expiry_and_capacity(clock):
    now,limiter=clock
    limiter.check([("ip:a",2,10),("email:a",1,20)])
    with pytest.raises(auth.AuthError) as result:
        limiter.check([("ip:b",2,10),("email:a",1,20)])
    assert result.value.code=="rate_limited" and result.value.status==429 and result.value.retry_after=="20"
    assert "ip:b" not in limiter.buckets
    now[0]+=19.5
    with pytest.raises(auth.AuthError) as result: limiter.check([("email:a",1,20)])
    assert result.value.retry_after=="1"
    now[0]+=0.5
    limiter.check([("email:a",1,20)])
    assert len(limiter.buckets)==1
    limiter.capacity=1
    with pytest.raises(auth.AuthError): limiter.check([("new",1,10)])
    assert len(limiter.buckets)==1


@pytest.mark.parametrize("email",["known@example.com","unknown@example.com"])
def test_login_email_threshold_before_argon2_and_ip_spoof_cannot_bypass(clock,monkeypatch,email):
    if email.startswith("known"): register(email)
    calls=[]
    monkeypatch.setattr(auth.get_passwords(),"verify",lambda encoded,password:calls.append(encoded) or False)
    for index in range(10):
        client=TestClient(app,client=(f"203.0.113.{index+1}",1234))
        response=client.post("/auth/login",json={"email":email.upper(),"password":PASSWORD})
        assert response.status_code==401
    client=TestClient(app,client=("127.0.0.1",1234))
    response=client.post("/auth/login",json={"email":email,"password":PASSWORD},headers={"X-Forwarded-For":"198.51.100.99","Forwarded":"for=198.51.100.99"})
    assert response.status_code==429 and response.json()=={"code":"rate_limited"}
    assert response.headers["Retry-After"]=="900" and response.headers["Cache-Control"]=="no-store"
    assert len(calls)==10
    assert client.post("/auth/login",json={"email":"isolated@example.com","password":PASSWORD}).status_code==401
    clock[0][0]+=900
    assert client.post("/auth/login",json={"email":email,"password":PASSWORD}).status_code==401


def test_ip_login_cap_cannot_be_evaded_with_email_or_xff(clock,monkeypatch):
    monkeypatch.setattr(auth.get_passwords(),"verify",lambda *args:False)
    client=TestClient(app,client=("127.0.0.1",1234))
    for index in range(60):
        assert client.post("/auth/login",json={"email":f"x{index}@example.com","password":PASSWORD},headers={"X-Forwarded-For":f"203.0.113.{index}"}).status_code==401
    assert client.post("/auth/login",json={"email":"more@example.com","password":PASSWORD}).status_code==429
    assert TestClient(app,client=("127.0.0.2",1234)).post("/auth/login",json={"email":"more@example.com","password":PASSWORD}).status_code==401


@pytest.mark.parametrize("known",[False,True])
def test_mail_budget_shared_register_resend_forgot_across_ip_and_case(clock,known):
    email="mail@example.com"
    if known: register(email)
    for index in range(5-int(known)):
        endpoint="/auth/email/resend" if index%2 else "/auth/password/forgot"
        response=TestClient(app,client=(f"203.0.113.{index}",1234)).post(endpoint,json={"email":email.upper()})
        assert response.status_code==202
    client=TestClient(app)
    for endpoint in ["/auth/register","/auth/email/resend","/auth/password/forgot"]:
        response=client.post(endpoint,json={"email":email,**({"password":PASSWORD} if endpoint.endswith("register") else {})})
        assert response.status_code==429 and response.json()=={"code":"rate_limited"}
        assert response.headers["Retry-After"]=="3600"


def test_telegram_create_threshold_poll_and_link_user_budget(clock,monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME","identity_bot")
    client=TestClient(app)
    payload={"purpose":"login","binding":"b"*43}
    for _ in range(30): assert client.post("/auth/telegram/challenges",json=payload).status_code==200
    assert client.post("/auth/telegram/challenges",json=payload).status_code==429
    clock[0][0]+=600
    assert client.post("/auth/telegram/challenges",json=payload).status_code==200
    # Reauth user budget does not depend on caller IP or password correctness.
    for _ in range(5): auth_limits.limit_link(1)
    with pytest.raises(auth.AuthError): auth_limits.limit_link(1)
    auth_limits.limit_link(2)
    for _ in range(600): auth_limits.limiter.check([("telegram-poll:ip:testclient",600,600)])
    result=client.post("/auth/telegram/status",json={**payload,"token":"c"*43})
    assert result.status_code==429


def test_completion_ip_limit_including_unknown_proofs(clock,monkeypatch):
    client=TestClient(app,client=("::1",1234))
    for _ in range(60): assert client.post("/auth/email/verify",json={"token":"x"}).status_code==400
    assert client.post("/auth/password/reset",json={"token":"x","password":PASSWORD}).status_code==429
    assert not any(PASSWORD in key or "@" in key for key in clock[1].buckets)


def test_link_reauth_is_throttled_by_authenticated_user_across_ips(clock,monkeypatch):
    from conftest import TestSessionLocal
    from sqlalchemy import select
    from app.models import User
    from test_auth import bearer
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME","identity_bot")
    register("linked@example.com")
    with TestSessionLocal() as db:
        user=db.scalar(select(User))
        session_token=auth.issue_session(db,user.id).session_token;db.commit()
    calls=[]
    monkeypatch.setattr(auth.get_passwords(),"verify",lambda *args:calls.append(1) or False)
    payload={"purpose":"link","binding":"b"*43,"password":"wrong password value"}
    for index in range(5):
        result=TestClient(app,client=(f"203.0.113.{index}",1234)).post("/auth/telegram/challenges",json=payload,headers=bearer(session_token))
        assert result.status_code==401
    response=TestClient(app,client=("198.51.100.99",1234)).post("/auth/telegram/challenges",json=payload,headers=bearer(session_token))
    assert response.status_code==429 and response.json()=={"code":"rate_limited"}
    assert len(calls)==5
