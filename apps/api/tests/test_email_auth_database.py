"""Revision 17 parity and atomic recovery on SQLite and isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.database import Base
from app.models import AuthEmailToken, AuthSession, User, UserProfile
from app.services import auth, email_tokens
from app.auth_schemas import AuthCredentials
from test_user_identity_migration import connection, API_ROOT, legacy_schema
from test_auth_migration import config_for
from test_auth import PASSWORD


@pytest.fixture
def email_engine(connection,tmp_path):
    connection.commit()
    if connection.dialect.name=="sqlite":
        engine=sa.create_engine(f"sqlite:///{tmp_path/'email.sqlite'}",connect_args={"check_same_thread":False,"timeout":10})
        @sa.event.listens_for(engine,"connect")
        def fk(dbapi,record): dbapi.execute("PRAGMA foreign_keys=ON")
    else:
        schema=connection.exec_driver_sql("SELECT current_schema()").scalar_one();connection.commit()
        engine=sa.create_engine(connection.engine.url.update_query_dict({"options":f"-csearch_path={schema}"}))
    try: yield engine
    finally: engine.dispose()


def seed(engine,purpose="verify"):
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user=User(telegram_id=123,first_name="Existing",email="email@example.com",email_canonical="email@example.com",password_hash=auth.get_passwords().hash(PASSWORD))
        db.add(user);db.flush()
        db.add(UserProfile(user_id=user.id,target_roles=["Keep"]))
        session_token=auth.issue_session(db,user.id).session_token
        raw=email_tokens.issue(db,user,purpose);db.commit()
        return user.id,raw,session_token


def test_revision17_real_upgrade_downgrade_preserves_domain_and_metadata(email_engine,monkeypatch):
    config=config_for(email_engine.url.render_as_string(hide_password=False),monkeypatch)
    if email_engine.dialect.name == "sqlite":
        with email_engine.begin() as conn: legacy_schema(conn)
        command.stamp(config,"20260923_13")
    command.upgrade(config,"20261002_16")
    with email_engine.begin() as conn:
        conn.execute(sa.insert(User),{"id":42,"telegram_id":123,"first_name":"Keep"})
        conn.execute(sa.insert(UserProfile),{"user_id":42,"target_roles":["Keep"]})
        before=conn.execute(sa.select(User.__table__)).mappings().all()
        profile=conn.execute(sa.select(UserProfile.__table__)).mappings().all()
    command.upgrade(config,"20261003_17")
    with email_engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version"))=="20261003_17"
        inspector=sa.inspect(conn)
        columns={c["name"]:c for c in inspector.get_columns("auth_email_tokens")}
        assert set(columns)==set(AuthEmailToken.__table__.columns.keys())
        assert all(columns[c.name]["nullable"]==c.nullable for c in AuthEmailToken.__table__.columns)
        assert inspector.get_pk_constraint("auth_email_tokens")["constrained_columns"]==["token_digest"]
        assert {i["name"] for i in inspector.get_indexes("auth_email_tokens")}=={"ix_auth_email_tokens_expires_at","ix_auth_email_tokens_user_purpose"}
        assert {c["name"] for c in inspector.get_check_constraints("auth_email_tokens")}=={c.name for c in AuthEmailToken.__table__.constraints if isinstance(c,sa.CheckConstraint)}
        fk=inspector.get_foreign_keys("auth_email_tokens")[0]
        assert fk["referred_table"]=="users" and fk["options"]["ondelete"]=="CASCADE"
        now=auth.utc_now()
        row={"token_digest":"a"*64,"user_id":42,"purpose":"verify","created_at":now,"expires_at":now+timedelta(hours=24)}
        conn.rollback()
        if conn.dialect.name=="sqlite": conn.exec_driver_sql("PRAGMA foreign_keys=ON");conn.commit()
        for invalid in [{"purpose":"other"},{"expires_at":now},{"user_id":999},{"consumed_at":now,"outcome":None},{"consumed_at":None,"outcome":"consumed"},{"consumed_at":now,"outcome":"bad"}]:
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(sa.insert(AuthEmailToken),row|invalid);conn.commit()
            conn.rollback()
        conn.execute(sa.insert(AuthEmailToken),row);conn.commit()
        with pytest.raises(sa.exc.IntegrityError): conn.execute(sa.insert(AuthEmailToken),row);conn.commit()
        conn.rollback()
        assert conn.execute(sa.select(User.__table__)).mappings().all()==before
        assert conn.execute(sa.select(UserProfile.__table__)).mappings().all()==profile
    command.downgrade(config,"20261002_16")
    with email_engine.connect() as conn:
        assert "auth_email_tokens" not in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.select(User.__table__)).mappings().all()==before
        assert conn.execute(sa.select(UserProfile.__table__)).mappings().all()==profile
    command.upgrade(config,"20261003_17")
    with email_engine.begin() as conn:
        conn.execute(sa.insert(AuthEmailToken),row)
        conn.execute(sa.delete(UserProfile).where(UserProfile.user_id==42))
        conn.execute(sa.delete(User).where(User.id==42))
        assert conn.scalar(sa.select(sa.func.count()).select_from(AuthEmailToken))==0


@pytest.mark.parametrize("purpose",["verify","reset"])
def test_concurrent_consume_exactly_one_mutation(email_engine,purpose):
    uid,raw,session_raw=seed(email_engine,purpose)
    barrier=Barrier(2)
    class Racing(Session):
        def execute(self,statement,*args,**kwargs):
            if isinstance(statement,Update) and statement.table.name=="users":
                barrier.wait(timeout=10)
            return super().execute(statement,*args,**kwargs)
    # Synchronize only the first serialization lock, not the actual user update.
    class Once(Racing):
        def execute(self,statement,*args,**kwargs):
            if getattr(self,"waited",False): return Session.execute(self,statement,*args,**kwargs)
            if isinstance(statement,Update) and statement.table.name=="users": self.waited=True
            return super().execute(statement,*args,**kwargs)
    def consume(_):
        with Once(email_engine) as db:
            try: email_tokens.consume(db,raw,purpose,"new password value 123");return "ok"
            except auth.AuthError as e: assert db.is_active;return e.code
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(consume,range(2)))
    assert sorted(results)==["EMAIL_TOKEN_USED","ok"]
    with Session(email_engine) as db:
        token=db.get(AuthEmailToken,email_tokens.digest(raw));user=db.get(User,uid)
        assert token.outcome=="consumed" and token.consumed_at is not None
        assert db.scalar(sa.select(UserProfile)).target_roles==["Keep"]
        if purpose=="verify":
            assert user.email_verified_at is not None and db.get(AuthSession,auth.token_hash(session_raw)).revoked_at is None
        else:
            assert auth.get_passwords().verify(user.password_hash,"new password value 123")
            assert db.get(AuthSession,auth.token_hash(session_raw)).revoked_at is not None
            assert user.telegram_id==123


@pytest.mark.parametrize("purpose",["verify","reset"])
def test_commit_failure_all_state_rolled_back_both_dialects(email_engine,purpose):
    uid,raw,session_raw=seed(email_engine,purpose)
    class Failing(Session):
        def commit(self): raise sa.exc.OperationalError("hidden",{},RuntimeError("hidden"))
    with Failing(email_engine) as db:
        with pytest.raises(auth.AuthError) as result: email_tokens.consume(db,raw,purpose,"new password value 123")
        assert result.value.code=="AUTH_UNAVAILABLE" and db.is_active
    with Session(email_engine) as db:
        assert db.get(User,uid).email_verified_at is None
        assert auth.get_passwords().verify(db.get(User,uid).password_hash,PASSWORD)
        assert db.get(AuthEmailToken,email_tokens.digest(raw)).consumed_at is None
        assert db.get(AuthSession,auth.token_hash(session_raw)).revoked_at is None
        email_tokens.consume(db,raw,purpose,"new password value 123")


def test_two_concurrent_reset_requests_only_one_live_proof(email_engine):
    uid,old,_=seed(email_engine,"reset");barrier=Barrier(2)
    def request(_):
        with Session(email_engine) as db:
            user=db.get(User,uid);barrier.wait(timeout=10)
            raw=email_tokens.issue(db,user,"reset");db.commit();return raw
    with ThreadPoolExecutor(max_workers=2) as pool: proofs=list(pool.map(request,range(2)))
    with Session(email_engine) as db:
        active=db.scalars(sa.select(AuthEmailToken).where(AuthEmailToken.consumed_at.is_(None))).all()
        assert len(active)==1
        assert active[0].token_digest in {email_tokens.digest(p) for p in proofs}
        assert db.get(AuthEmailToken,email_tokens.digest(old)).outcome=="replaced"
        winner=next(p for p in proofs if email_tokens.digest(p)==active[0].token_digest)
        email_tokens.consume(db,winner,"reset","replacement password 123")
        for proof in proofs:
            with pytest.raises(auth.AuthError): email_tokens.consume(db,proof,"reset","replacement password 456")


def test_reset_wins_over_old_password_login_before_session_issue(email_engine,monkeypatch):
    uid,raw,old_session=seed(email_engine,"reset")
    with Session(email_engine) as db: db.execute(sa.update(User).values(email_verified_at=auth.utc_now()));db.commit()
    ready,release=Event(),Event();original=auth.get_passwords().verify
    def slow_verify(encoded,password):
        result=original(encoded,password)
        if password==PASSWORD:
            ready.set();assert release.wait(timeout=10)
        return result
    monkeypatch.setattr(auth.get_passwords(),"verify",slow_verify)
    def stale_login():
        with Session(email_engine) as db:
            with pytest.raises(auth.AuthError) as error: auth.login(db,AuthCredentials(email="email@example.com",password=PASSWORD))
            assert error.value.code=="AUTH_INVALID_CREDENTIALS"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(stale_login)
        try:
            assert ready.wait(timeout=10)
            with Session(email_engine) as db: email_tokens.consume(db,raw,"reset","replacement password 123")
        finally:release.set()
        future.result(timeout=10)
    with Session(email_engine) as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(AuthSession))==1
        assert db.get(AuthSession,auth.token_hash(old_session)).revoked_at is not None


def test_model_fk_cascade_and_terminal_constraints(email_engine):
    uid,raw,_=seed(email_engine)
    now=auth.utc_now()
    with email_engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.update(AuthEmailToken).values(consumed_at=now,outcome=None));conn.commit()
        conn.rollback()
        conn.execute(sa.delete(UserProfile).where(UserProfile.user_id==uid))
        conn.execute(sa.delete(User).where(User.id==uid));conn.commit()
        assert conn.scalar(sa.select(sa.func.count()).select_from(AuthEmailToken))==0


def test_revision17_ddl_failure_rolls_back_to_parent(email_engine,monkeypatch):
    config=config_for(email_engine.url.render_as_string(hide_password=False),monkeypatch)
    if email_engine.dialect.name=="sqlite":
        with email_engine.begin() as conn: legacy_schema(conn)
        command.stamp(config,"20260923_13")
    command.upgrade(config,"20261002_16")
    from alembic import op
    original=op.create_index
    def fail(name,*args,**kwargs):
        if name=="ix_auth_email_tokens_expires_at": raise RuntimeError("injected DDL failure")
        return original(name,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(op,"create_index",fail)
        with pytest.raises(RuntimeError,match="injected DDL"): command.upgrade(config,"20261003_17")
    with email_engine.connect() as conn:
        assert "auth_email_tokens" not in sa.inspect(conn).get_table_names()
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version"))=="20261002_16"
    command.upgrade(config,"20261003_17")
    with email_engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version"))=="20261003_17"


@pytest.mark.parametrize("purpose",["verify","reset"])
def test_resend_and_consume_serialize_without_two_live_proofs(email_engine,purpose):
    uid,raw,_=seed(email_engine,purpose);barrier=Barrier(2)
    def consuming():
        with Session(email_engine) as db:
            barrier.wait(timeout=10)
            try: email_tokens.consume(db,raw,purpose,"replacement password 123");return "ok"
            except auth.AuthError as error:return error.code
    def replacing():
        with Session(email_engine) as db:
            barrier.wait(timeout=10)
            email_tokens.request_email(db,"email@example.com",purpose)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(consuming);second=pool.submit(replacing)
        assert first.result(timeout=15) in {"ok","EMAIL_TOKEN_USED"};second.result(timeout=15)
    with Session(email_engine) as db:
        tokens=db.scalars(sa.select(AuthEmailToken)).all()
        assert len([t for t in tokens if t.consumed_at is None])<=1
        assert db.get(AuthEmailToken,email_tokens.digest(raw)).outcome in {"consumed","replaced"}
