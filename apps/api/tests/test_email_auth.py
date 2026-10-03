"""Ownership/recovery contracts including failures, isolation and replay."""
from datetime import timedelta
import json
from pathlib import Path
import os

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from app.database import get_session
from app.main import app
from app.models import AuthEmailToken, AuthSession, User, UserProfile
from app.services import auth, auth_mail, email_tokens
from conftest import TestSessionLocal, mail_token, verify_account
from test_auth import plain, register, PASSWORD, bearer

@pytest.fixture(autouse=True)
def bot_name(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "identity_bot")


NEW_PASSWORD = "  Новый Unicode пароль 🔒  "


def post_token(raw, purpose="verify", password=NEW_PASSWORD):
    return plain.post("/auth/email/verify" if purpose == "verify" else "/auth/password/reset", content=json.dumps({"token": raw, **({"password": password} if purpose == "reset" else {})}), headers={"Content-Type":"application/json"})


def request_token(purpose="verify", email="user+tag@example.com"):
    response = plain.post("/auth/email/resend" if purpose == "verify" else "/auth/password/forgot", json={"email": email})
    assert response.status_code == 202 and response.json() == {"ok": True}
    return mail_token(email, purpose)


def raw_login(password=PASSWORD):
    return plain.post("/auth/login", json={"email": "user+tag@example.com", "password": password})


def test_register_digest_trusted_fragment_link_and_policy(caplog):
    result = register()
    assert result.status_code == 202 and result.headers["cache-control"] == "no-store"
    raw = mail_token("user+tag@example.com")
    path = next(Path(os.environ["AUTH_MAIL_SINK_DIR"]).glob("*.json"))
    mail = json.loads(path.read_text())
    assert mail["link"] == f"http://127.0.0.1:3100/verify-email#token={raw}"
    assert path.stat().st_mode & 0o077 == 0
    with TestSessionLocal() as db:
        user = db.scalar(select(User)); token = db.scalar(select(AuthEmailToken))
        assert token.token_digest == email_tokens.digest(raw) and raw != token.token_digest
        assert token.purpose == "verify" and token.user_id == user.id
        assert auth.aware(token.expires_at) - auth.aware(token.created_at) == timedelta(hours=24)
        assert token.outcome is None and user.email_verified_at is None
        assert db.query(AuthSession).count() == 0
    assert raw_login().status_code == 403
    assert raw_login("wrong password value").status_code == 401
    assert post_token(raw).json() == {"ok": True}
    assert raw_login().status_code == 200
    assert post_token(raw).json() == {"detail": {"code": "EMAIL_TOKEN_USED"}}
    assert PASSWORD not in caplog.text and raw not in caplog.text


@pytest.mark.parametrize("purpose", ["verify", "reset"])
def test_resend_replaces_old_token_expiry_and_replay(purpose):
    register(); old = mail_token("user+tag@example.com") if purpose == "verify" else request_token("reset")
    new = request_token(purpose)
    assert old != new
    assert post_token(old, purpose).json()["detail"]["code"] == "EMAIL_TOKEN_USED"
    with TestSessionLocal() as db:
        record = db.get(AuthEmailToken, email_tokens.digest(new))
        record.created_at = auth.utc_now() - timedelta(days=2)
        record.expires_at = auth.utc_now() - timedelta(seconds=1)
        db.commit()
    assert post_token(new, purpose).json()["detail"]["code"] == "EMAIL_TOKEN_EXPIRED"
    fresh = request_token(purpose)
    assert post_token(fresh, purpose).status_code == 200
    assert post_token(fresh, purpose).json()["detail"]["code"] == "EMAIL_TOKEN_USED"


@pytest.mark.parametrize("raw", ["", "x", "a"*42, "a"*44, "a"*42+"!", "ü"*43, "a"*43])
@pytest.mark.parametrize("purpose", ["verify", "reset"])
def test_invalid_proof_no_identity_or_session_mutation(raw, purpose):
    register()
    response = post_token(raw, purpose)
    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "EMAIL_TOKEN_INVALID"}}
    with TestSessionLocal() as db:
        assert db.scalar(select(User)).email_verified_at is None
        assert db.scalar(select(AuthEmailToken)).consumed_at is None
        assert db.query(AuthSession).count() == 0


def test_purpose_separation_verified_no_resend_and_duplicate_register():
    register(); verification = mail_token("user+tag@example.com"); reset = request_token("reset")
    assert post_token(verification, "reset").status_code == 400
    assert post_token(reset, "verify").status_code == 400
    assert post_token(verification).status_code == 200
    before = len(list(Path(os.environ["AUTH_MAIL_SINK_DIR"]).glob("*.json")))
    assert plain.post("/auth/email/resend", json={"email":"USER+TAG@example.com"}).status_code == 202
    assert register(password=NEW_PASSWORD).status_code == 202
    assert len(list(Path(os.environ["AUTH_MAIL_SINK_DIR"]).glob("*.json"))) == before
    assert raw_login().status_code == 200


def test_unknown_and_telegram_only_requests_generic_without_tokens():
    from conftest import client as bot
    bot.post("/users/telegram", json={"telegram_id":123, "first_name":"Actor"})
    for endpoint in ["/auth/email/resend", "/auth/password/forgot"]:
        response = plain.post(endpoint, json={"email":"unknown@example.com"})
        assert response.status_code == 202 and response.json() == {"ok":True}
    with TestSessionLocal() as db:
        assert db.query(AuthEmailToken).count() == 0
        user = db.scalar(select(User))
        assert user.email is None and user.password_hash is None
    assert not list(Path(os.environ["AUTH_MAIL_SINK_DIR"]).glob("*.json"))


def test_reset_revokes_email_and_telegram_sessions_preserves_identity_and_domain():
    from test_telegram_auth import challenge, approve, complete, uid
    register(); verify_account(plain,"user+tag@example.com")
    first = raw_login().json()["session_token"]; second = raw_login().json()["session_token"]
    with TestSessionLocal() as db:
        user=db.scalar(select(User)); user.telegram_id=123
        db.add(UserProfile(user_id=user.id, target_roles=["Preserved profile"])); db.commit()
        user_id=user.id
    body=challenge(); assert approve(body).status_code == 200
    tg = complete(body).json()["session_token"]
    assert uid(tg) == user_id
    token=request_token("reset")
    with TestSessionLocal() as db:
        record=db.get(AuthEmailToken,email_tokens.digest(token))
        assert auth.aware(record.expires_at)-auth.aware(record.created_at) == timedelta(minutes=30)
    assert post_token(token,"reset").status_code == 200
    for session_token in [first,second,tg]:
        assert plain.get("/auth/me",headers=bearer(session_token)).status_code == 401
    assert raw_login().status_code == 401
    assert raw_login(NEW_PASSWORD).status_code == 200
    with TestSessionLocal() as db:
        user=db.get(User,user_id)
        assert user.telegram_id==123 and user.email_verified_at is not None
        assert db.scalar(select(UserProfile)).target_roles==["Preserved profile"]
        assert db.query(User).count()==1
    body=challenge(); approve(body)
    assert uid(complete(body).json()["session_token"])==user_id


def test_telegram_login_unverified_hybrid_works_and_verify_keeps_existing_session():
    from test_telegram_auth import challenge, approve, complete, uid
    register()
    with TestSessionLocal() as db:
        user=db.scalar(select(User)); user.telegram_id=123; db.commit(); user_id=user.id
    assert raw_login().status_code == 403
    body=challenge(); approve(body)
    session_token=complete(body).json()["session_token"]
    assert uid(session_token)==user_id
    assert not plain.get("/auth/me",headers=bearer(session_token)).json()["email_verified"]
    assert post_token(mail_token("user+tag@example.com")).status_code==200
    assert plain.get("/auth/me",headers=bearer(session_token)).json()["email_verified"]
    assert raw_login().status_code==200


@pytest.mark.parametrize("password", ["a"*14,"a"*129,"x"*15+"\ud800"])
def test_reset_password_policy_does_not_consume(password):
    register(); raw=request_token("reset")
    result=post_token(raw,"reset",password)
    assert result.status_code==422 and password not in result.text
    assert post_token(raw,"reset"," "*15).status_code==200
    with TestSessionLocal() as db:
        assert auth.get_passwords().verify(db.scalar(select(User)).password_hash," "*15)
        assert db.scalar(select(User)).email_verified_at is None  # Reset is not verification.


@pytest.mark.parametrize("purpose", ["verify","reset"])
def test_commit_failure_rolls_back_everything_and_session_reusable(monkeypatch, purpose, caplog):
    register(); verify_raw=mail_token("user+tag@example.com")
    with TestSessionLocal() as db:
        user=db.scalar(select(User)); before_hash=user.password_hash
        session_raw=auth.issue_session(db,user.id).session_token; db.commit()
    raw=verify_raw if purpose=="verify" else request_token("reset")
    with TestSessionLocal() as db:
        previous=app.dependency_overrides[get_session]; app.dependency_overrides[get_session]=lambda:db
        original=db.commit
        def fail(): raise OperationalError("private "+raw, {"password":NEW_PASSWORD}, RuntimeError(raw))
        monkeypatch.setattr(db,"commit",fail)
        try:
            response=post_token(raw,purpose)
            assert response.status_code==503 and raw not in response.text and NEW_PASSWORD not in response.text
            assert db.is_active
            db.expire_all()
            assert db.scalar(select(User)).password_hash==before_hash
            assert db.scalar(select(User)).email_verified_at is None
            assert db.get(AuthEmailToken,email_tokens.digest(raw)).consumed_at is None
            assert db.get(AuthSession,auth.token_hash(session_raw)).revoked_at is None
            monkeypatch.setattr(db,"commit",original)
            assert post_token(raw,purpose).status_code==200
        finally: app.dependency_overrides[get_session]=previous
    assert raw not in caplog.text and NEW_PASSWORD not in caplog.text


@pytest.mark.parametrize("endpoint", ["/auth/register","/auth/email/resend","/auth/password/forgot"])
def test_provider_unavailable_is_generic_before_account_lookup(monkeypatch,endpoint):
    register()
    def fail(): raise auth.AuthError("AUTH_UNAVAILABLE",503)
    monkeypatch.setattr(auth_mail.get_mailer(),"check",fail)
    results=[plain.post(endpoint,json={"email":email, **({"password":PASSWORD} if endpoint.endswith("register") else {})}) for email in ["user+tag@example.com","unknown@example.com"]]
    assert all(r.status_code==503 for r in results) and results[0].json()==results[1].json()


def test_send_failure_keeps_identity_token_and_public_result(monkeypatch,caplog):
    def fail(*args): raise RuntimeError("provider private exception")
    monkeypatch.setattr(auth_mail.get_mailer(),"send",fail)
    assert register().status_code==202
    with TestSessionLocal() as db:
        assert db.query(User).count()==1 and db.query(AuthEmailToken).count()==1
    for endpoint in ["/auth/email/resend","/auth/password/forgot"]:
        a=plain.post(endpoint,json={"email":"user+tag@example.com"})
        b=plain.post(endpoint,json={"email":"unknown@example.com"})
        assert a.status_code==b.status_code==202 and a.json()==b.json()
    assert "provider private exception" not in caplog.text


@pytest.mark.parametrize("purpose",["verify","reset"])
@pytest.mark.parametrize("offset,valid",[(-1,True),(0,False),(1,False)])
def test_exact_proof_expiry_boundary(monkeypatch,purpose,offset,valid):
    register();raw=mail_token("user+tag@example.com") if purpose=="verify" else request_token("reset")
    with TestSessionLocal() as db: expires=auth.aware(db.get(AuthEmailToken,email_tokens.digest(raw)).expires_at)
    monkeypatch.setattr(auth,"utc_now",lambda:expires+timedelta(microseconds=offset))
    response=post_token(raw,purpose)
    assert response.status_code==(200 if valid else 400)
    if not valid:assert response.json()["detail"]["code"]=="EMAIL_TOKEN_EXPIRED"


def test_already_verified_token_is_safely_consumed_once():
    register();raw=mail_token("user+tag@example.com")
    with TestSessionLocal() as db:
        user=db.scalar(select(User));user.email_verified_at=auth.utc_now();db.commit();before=user.email_verified_at
    assert post_token(raw).status_code==200
    assert post_token(raw).json()["detail"]["code"]=="EMAIL_TOKEN_USED"
    with TestSessionLocal() as db:assert auth.aware(db.scalar(select(User)).email_verified_at)==auth.aware(before)
