from datetime import timedelta
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session
from app.database import Base
from app.models import TelegramChallenge, User, AuthSession
from app.services import auth
from test_user_identity_migration import connection
from test_auth_migration import config_for


def test_revision16_upgrade_downgrade_parity(connection, tmp_path, monkeypatch):
    if connection.dialect.name=="sqlite": url=f"sqlite:///{tmp_path / 'migration16.sqlite'}"
    else:
        schema=connection.exec_driver_sql("SELECT current_schema()").scalar_one();connection.commit()
        url=connection.engine.url.update_query_dict({"options":f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    engine=sa.create_engine(url)
    if engine.dialect.name == "sqlite":
        @sa.event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
    try:
        with engine.begin() as conn:
            Base.metadata.create_all(conn, tables=[t for t in Base.metadata.sorted_tables if t.name != "auth_email_tokens"]);TelegramChallenge.__table__.drop(conn)
        config=config_for(url,monkeypatch);command.stamp(config,"20261002_15")
        with Session(engine) as db:
            user=User(telegram_id=123,first_name="Keep");db.add(user);db.flush()
            result=auth.issue_session(db,user.id);db.commit(); uid=user.id
        command.upgrade(config,"20261002_16")
        assert ScriptDirectory.from_config(config).get_heads()==["20261006_19"]
        with engine.connect() as conn:
            inspect=sa.inspect(conn)
            assert {c["name"]:c["nullable"] for c in inspect.get_columns("auth_telegram_challenges")}=={c.name:c.nullable for c in TelegramChallenge.__table__.columns}
            assert {c["name"] for c in inspect.get_check_constraints("auth_telegram_challenges")}=={c.name for c in TelegramChallenge.__table__.constraints if isinstance(c,sa.CheckConstraint)}
            assert {i["name"] for i in inspect.get_indexes("auth_telegram_challenges")}=={"ix_auth_telegram_challenges_expires_at","ix_auth_telegram_challenges_initiating_user_id"}
            assert all(fk["options"]["ondelete"]=="CASCADE" for fk in inspect.get_foreign_keys("auth_telegram_challenges"))
        with Session(engine) as db:
            now=auth.utc_now()
            db.add(TelegramChallenge(token_hash="a"*64,binding_hash="b"*64,purpose="link",initiating_user_id=uid,initiating_session_hash=auth.token_hash(result.session_token),created_at=now,expires_at=now+timedelta(minutes=5)))
            db.commit()
        # Exercise both migrated FK cascades, not just reflected DDL options.
        with Session(engine) as db:
            extra = auth.issue_session(db, uid)
            extra_hash = auth.token_hash(extra.session_token)
            db.add(TelegramChallenge(token_hash="c"*64, binding_hash="d"*64,
                purpose="link", initiating_user_id=uid, initiating_session_hash=extra_hash,
                created_at=now, expires_at=now+timedelta(minutes=5)))
            db.commit()
            db.execute(sa.delete(AuthSession).where(AuthSession.token_hash == extra_hash))
            db.commit()
            assert db.get(TelegramChallenge, "c"*64) is None
            other = User(telegram_id=456)
            db.add(other); db.flush()
            other_session = auth.issue_session(db, other.id)
            db.add(TelegramChallenge(token_hash="e"*64, binding_hash="f"*64,
                purpose="link", initiating_user_id=other.id,
                initiating_session_hash=auth.token_hash(other_session.session_token),
                created_at=now, expires_at=now+timedelta(minutes=5)))
            db.commit()
            db.execute(sa.delete(User).where(User.id == other.id))
            db.commit()
            assert db.get(TelegramChallenge, "e"*64) is None
            assert db.query(AuthSession).count() == 1
        command.downgrade(config,"20261002_15")
        with Session(engine) as db:
            assert db.get(User,uid).first_name=="Keep" and db.query(AuthSession).count()==1
            assert "auth_telegram_challenges" not in sa.inspect(engine).get_table_names()
        command.upgrade(config,"20261002_16")
        with Session(engine) as db: assert db.query(TelegramChallenge).count()==0
    finally: engine.dispose()

@pytest.mark.parametrize("invalid", [
    {"purpose":"unknown"}, {"purpose":"link"}, {"outcome":"conflict"},
    {"consumed_at":auth.utc_now()}, {"approved_at":auth.utc_now()},
])
def test_model_constraints(connection,invalid):
    Base.metadata.create_all(connection);connection.commit();now=auth.utc_now()
    values={"token_hash":"a"*64,"binding_hash":"b"*64,"purpose":"login","created_at":now,"expires_at":now+timedelta(minutes=5)}|invalid
    with pytest.raises(sa.exc.IntegrityError):
        with connection.begin_nested(): connection.execute(TelegramChallenge.__table__.insert(),values)
    assert connection.scalar(sa.select(sa.func.count()).select_from(TelegramChallenge))==0
