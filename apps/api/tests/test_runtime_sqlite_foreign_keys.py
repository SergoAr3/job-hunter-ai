"""Runtime engine configuration, without test-only foreign-key PRAGMAs."""
import os
import subprocess
import sys
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.database import Base, create_runtime_engine
from app.models import AuthEmailToken, AuthSession, TelegramChallenge, User
from app.services import auth, email_tokens


@pytest.fixture
def runtime_engine(tmp_path):
    engine = create_runtime_engine(f"sqlite:///{tmp_path / 'runtime.sqlite'}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_module_runtime_engine_enforces_foreign_keys():
    # Import the real module in isolation so conftest's prepared engine cannot
    # mask a regression in the module-level runtime engine initialization.
    result = subprocess.run(
        [sys.executable, "-c", "from app.database import engine; "
         "connection = engine.connect(); "
         "assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1; "
         "connection.close(); engine.dispose()"],
        env={**os.environ, "DATABASE_URL": "sqlite://"},
        capture_output=True, text=True, check=True,
    )
    assert not result.stdout


def test_each_connection_pool_replacement_and_transactions(runtime_engine):
    with runtime_engine.connect() as first, runtime_engine.connect() as second:
        for connection in (first, second):
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        first.rollback()
        with first.begin():
            first.execute(sa.insert(User), {"telegram_id": 123})
        first.execute(sa.delete(User))
        first.rollback()
        assert first.scalar(sa.select(sa.func.count()).select_from(User)) == 1
        first.invalidate()
        first.rollback()
        assert first.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    runtime_engine.dispose()
    with runtime_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def seed(db):
    user = User(telegram_id=123, email="old@example.com",
                email_canonical="old@example.com", password_hash="old-test-hash")
    db.add(user)
    db.flush()
    raw_session = auth.issue_session(db, user.id).session_token
    proofs = {purpose: email_tokens.issue(db, user, purpose) for purpose in ("verify", "reset")}
    now = auth.utc_now()
    db.add(TelegramChallenge(
        token_hash="c" * 64, binding_hash="b" * 64, purpose="link",
        initiating_user_id=user.id, initiating_session_hash=auth.token_hash(raw_session),
        created_at=now, expires_at=now + timedelta(minutes=5),
    ))
    db.commit()
    return user.id, raw_session, proofs


def test_user_delete_cascades_all_auth_credentials(runtime_engine):
    with Session(runtime_engine) as db:
        user_id, _, _ = seed(db)
        db.execute(sa.delete(User).where(User.id == user_id))
        db.commit()
        for model in (AuthSession, AuthEmailToken, TelegramChallenge):
            assert db.scalar(sa.select(sa.func.count()).select_from(model)) == 0


def test_session_delete_cascades_link_challenge_only(runtime_engine):
    with Session(runtime_engine) as db:
        user_id, raw_session, _ = seed(db)
        db.execute(sa.delete(AuthSession).where(AuthSession.token_hash == auth.token_hash(raw_session)))
        db.commit()
        assert db.scalar(sa.select(sa.func.count()).select_from(TelegramChallenge)) == 0
        assert db.get(User, user_id) is not None
        assert db.scalar(sa.select(sa.func.count()).select_from(AuthEmailToken)) == 2


@pytest.mark.parametrize("model", [AuthSession, AuthEmailToken])
def test_orphan_auth_insert_rejected_and_session_recovers(runtime_engine, model):
    now = auth.utc_now()
    values = {"user_id": 999, "created_at": now, "expires_at": now + timedelta(hours=1)}
    if model is AuthSession:
        values.update(token_hash="a" * 64, last_seen_at=now)
    else:
        values.update(token_digest="a" * 64, purpose="verify")
    with Session(runtime_engine) as db:
        db.add(model(**values))
        with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
            db.commit()
        db.rollback()
        db.add(User(telegram_id=456))
        db.commit()
        assert db.scalar(sa.select(sa.func.count()).select_from(model)) == 0


def test_reused_id_cannot_accept_old_session_or_email_proofs(runtime_engine):
    with Session(runtime_engine) as db:
        old_id, raw_session, proofs = seed(db)
        db.execute(sa.delete(User).where(User.id == old_id))
        db.commit()
    with Session(runtime_engine) as db:
        new_user = User(email="new@example.com", email_canonical="new@example.com",
                        password_hash="new-test-hash")
        db.add(new_user)
        db.commit()
        assert new_user.id == old_id  # Exercise actual SQLite ROWID reuse.
        with pytest.raises(auth.AuthError) as result:
            auth.authenticate(db, raw_session)
        assert result.value.code == "AUTH_REQUIRED"
        db.rollback()
        for purpose, raw in proofs.items():
            with pytest.raises(auth.AuthError) as result:
                email_tokens.consume(db, raw, purpose, "replacement password 123")
            assert result.value.code == "EMAIL_TOKEN_INVALID"
            db.rollback()
        db.refresh(new_user)
        assert new_user.email_verified_at is None
        assert new_user.password_hash == "new-test-hash"
        assert new_user.email == "new@example.com"


@pytest.mark.parametrize("missing", ["user", "session"])
def test_link_challenge_rejects_missing_initiator(runtime_engine, missing):
    with Session(runtime_engine) as db:
        user_id, raw_session, _ = seed(db)
        now = auth.utc_now()
        db.add(TelegramChallenge(
            token_hash="d" * 64, binding_hash="e" * 64, purpose="link",
            initiating_user_id=999 if missing == "user" else user_id,
            initiating_session_hash="f" * 64 if missing == "session" else auth.token_hash(raw_session),
            created_at=now, expires_at=now + timedelta(minutes=5),
        ))
        with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
            db.commit()
        db.rollback()
        assert db.scalar(sa.select(sa.func.count()).select_from(TelegramChallenge)) == 1
