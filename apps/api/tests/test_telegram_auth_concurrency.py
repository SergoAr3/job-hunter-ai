"""Independent transactions with barriers at CAS/identity lookup, no sleeps."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import secrets
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update
from app.database import Base
from app.models import User, AuthSession, TelegramChallenge
from app.services import auth, telegram_auth
from app.schemas import TelegramUserIn
from test_user_identity_migration import connection

@pytest.fixture
def challenge_engine(connection, tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "identity_bot")
    if connection.dialect.name == "sqlite":
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'challenges.sqlite'}", connect_args={"timeout":10,"check_same_thread":False})
        with engine.connect() as conn: conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    else:
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one(); connection.commit()
        engine = sa.create_engine(connection.engine.url.update_query_dict({"options":f"-csearch_path={schema}"}))
    Base.metadata.create_all(engine)
    try: yield engine
    finally: engine.dispose()

def create(engine, purpose="login", principal=None):
    binding=secrets.token_urlsafe(32)
    with Session(engine) as db:
        if purpose=="login": value=telegram_auth.create(db,purpose,binding)
        else:
            # Completion races use already reauthenticated initiating sessions.
            raw=secrets.token_urlsafe(32); now=auth.utc_now()
            db.add(TelegramChallenge(token_hash=auth.token_hash(raw),binding_hash=auth.token_hash(binding),purpose="link",initiating_user_id=principal.user_id,initiating_session_hash=principal.session_hash,created_at=now,expires_at=now+telegram_auth.TTL))
            db.commit(); value={"token":raw}
    with Session(engine) as db:
        telegram_auth.approve(db,value["token"],TelegramUserIn(telegram_id=123,first_name="Actor"))
    return value["token"],binding,purpose,principal

def race(engine, inputs, identity_barrier=False):
    cas=Barrier(2); identity=Barrier(2)
    class RacingSession(Session):
        armed=True
        def execute(self, statement, *args, **kwargs):
            if isinstance(statement,Update) and statement.table.name=="auth_telegram_challenges" and self.armed:
                self.armed=False; cas.wait(timeout=10)
            return super().execute(statement,*args,**kwargs)
        def scalar(self,statement,*args,**kwargs):
            result=super().scalar(statement,*args,**kwargs)
            if (identity_barrier and engine.dialect.name=="postgresql" and result is None
                and "users.telegram_id" in str(getattr(statement,"whereclause",""))):
                identity.wait(timeout=10)
            return result
    def run(item):
        with RacingSession(engine) as db:
            try: return telegram_auth.complete(db,*item)
            except auth.AuthError as error:
                assert db.is_active and db.scalar(sa.select(sa.func.count()).select_from(User)) is not None
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool: return list(pool.map(run,inputs))

def test_same_challenge_consumed_once(challenge_engine):
    item=create(challenge_engine);results=race(challenge_engine,[item,item])
    assert sum(not isinstance(r,str) for r in results)==1
    assert [r for r in results if isinstance(r,str)]==["TELEGRAM_CHALLENGE_CONSUMED"]
    with Session(challenge_engine) as db:
        assert db.query(User).count()==1 and db.query(AuthSession).count()==1

def test_concurrent_new_telegram_resolves_one_account(challenge_engine):
    results=race(challenge_engine,[create(challenge_engine),create(challenge_engine)],True)
    assert all(not isinstance(r,str) for r in results), results
    with Session(challenge_engine) as db:
        assert db.query(User).count()==1 and db.query(AuthSession).count()==2
        assert {r.user_id for r in db.query(AuthSession)}=={db.scalar(sa.select(User.id))}

def test_concurrent_link_one_winner_no_partial_transfer(challenge_engine):
    principals=[]
    with Session(challenge_engine) as db:
        for email in ["b@example.com","c@example.com"]:
            user=User(email=email,email_canonical=email,password_hash="preserved-hash",display_name=email)
            db.add(user);db.flush(); result=auth.issue_session(db,user.id)
            principals.append(auth.Principal(user.id,auth.token_hash(result.session_token)))
        db.commit()
    results=race(challenge_engine,[create(challenge_engine,"link",p) for p in principals],True)
    assert sum(not isinstance(r,str) for r in results)==1
    assert [r for r in results if isinstance(r,str)]==["ACCOUNT_LINK_CONFLICT"]
    with Session(challenge_engine) as db:
        assert db.query(User).count()==2 and db.query(AuthSession).count()==2
        assert db.query(User).filter_by(telegram_id=123).count()==1
        assert all(u.password_hash=="preserved-hash" and u.email==u.display_name for u in db.query(User))
        assert {r.outcome for r in db.query(TelegramChallenge)}=={"completed","conflict"}

def test_browser_cancel_and_complete_have_one_terminal_winner(challenge_engine):
    item = create(challenge_engine)
    barrier = Barrier(2)
    class RacingSession(Session):
        armed = True
        def execute(self, statement, *args, **kwargs):
            if isinstance(statement, Update) and statement.table.name == 'auth_telegram_challenges' and self.armed:
                self.armed = False
                barrier.wait(timeout=10)
            return super().execute(statement, *args, **kwargs)
    def run(operation):
        with RacingSession(challenge_engine) as db:
            try:
                result = (telegram_auth.complete(db, *item) if operation == 'complete'
                    else telegram_auth.cancel_browser(db, *item))
                return operation, result
            except auth.AuthError as error:
                assert db.is_active
                return operation, error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(run, ['complete', 'cancel']))
    assert sum(not isinstance(value, str) for value in results.values()) == 1
    with Session(challenge_engine) as db:
        record = db.get(TelegramChallenge, auth.token_hash(item[0]))
        if record.outcome == 'cancelled':
            assert results['complete'] in ('TELEGRAM_CHALLENGE_CANCELLED', 'TELEGRAM_CHALLENGE_CONSUMED')
            assert db.query(AuthSession).count() == db.query(User).count() == 0
        else:
            assert record.outcome == 'completed' and results['cancel'] == 'TELEGRAM_CHALLENGE_INVALID'
            assert db.query(AuthSession).count() == db.query(User).count() == 1
