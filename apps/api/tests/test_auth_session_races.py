"""Fixed-clock boundaries and deterministic PostgreSQL session interleavings."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update

from app.database import Base
from app.models import AuthSession, User
from app.services import auth
from conftest import TestSessionLocal
from test_user_identity_migration import connection

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
RAW = "r" * 43
DIGEST = auth.token_hash(RAW)


def seed(session):
    user = User(telegram_id=123, first_name="Actor")
    session.add(user)
    session.flush()
    session.add(AuthSession(token_hash=DIGEST, user_id=user.id,
                            created_at=NOW - timedelta(days=2),
                            expires_at=NOW + timedelta(days=5),
                            last_seen_at=NOW - timedelta(minutes=6)))
    session.commit()
    return user.id


@pytest.mark.parametrize("boundary", ["absolute", "idle"])
@pytest.mark.parametrize("offset,valid", [(-1, True), (0, False), (1, False)])
def test_exact_expiry_boundaries(monkeypatch, boundary, offset, valid):
    monkeypatch.setattr(auth, "utc_now", lambda: NOW + timedelta(microseconds=offset))
    with TestSessionLocal() as session:
        uid = seed(session)
        record = session.get(AuthSession, DIGEST)
        if boundary == "absolute":
            record.expires_at = NOW
            record.last_seen_at = NOW - timedelta(minutes=1)
        else:
            record.last_seen_at = NOW - timedelta(hours=24)
        session.commit()
        session.refresh(record)
        before = (record.created_at, record.expires_at, record.last_seen_at, record.revoked_at)
        if valid:
            assert auth.authenticate(session, RAW).user_id == uid
        else:
            with pytest.raises(auth.AuthError):
                auth.authenticate(session, RAW)
        session.expire_all()
        after = session.get(AuthSession, DIGEST)
        assert (after.created_at, after.expires_at, after.revoked_at) == (before[0], before[1], before[3])
        if not valid:
            assert after.last_seen_at == before[2]


def test_touch_changes_only_last_seen_and_revoked_wins(monkeypatch):
    monkeypatch.setattr(auth, "utc_now", lambda: NOW)
    with TestSessionLocal() as session:
        uid = seed(session)
        before = session.get(AuthSession, DIGEST)
        original = {c.name: getattr(before, c.name) for c in AuthSession.__table__.columns}
        assert auth.authenticate(session, RAW).user_id == uid
        session.expire_all()
        record = session.get(AuthSession, DIGEST)
        assert auth.aware(record.last_seen_at) == NOW
        assert all(getattr(record, name) == value for name, value in original.items() if name != "last_seen_at")
        auth.logout(session, RAW)
        session.expire_all()
        with pytest.raises(auth.AuthError):
            auth.authenticate(session, RAW)
        assert auth.aware(session.get(AuthSession, DIGEST).revoked_at) == NOW


@pytest.fixture
def postgres_engine(connection):
    if connection.dialect.name != "postgresql":
        pytest.skip("row-lock races require PostgreSQL; clock boundaries run on SQLite")
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    connection.commit()
    engine = sa.create_engine(connection.engine.url.update_query_dict({"options": f"-csearch_path={schema}"}))
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def touch_statement(statement):
    return isinstance(statement, Update) and statement.table.name == "auth_sessions"


def test_concurrent_touch_touch_zero_rowcount_remains_valid(postgres_engine, monkeypatch):
    monkeypatch.setattr(auth, "utc_now", lambda: NOW)
    with Session(postgres_engine) as session:
        uid = seed(session)
    barrier = Barrier(2)
    rows, connections = [], []
    class RacingSession(Session):
        def execute(self, statement, *args, **kwargs):
            if touch_statement(statement):
                connections.append(self.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
                barrier.wait(timeout=10)
                result = super().execute(statement, *args, **kwargs)
                rows.append(result.rowcount)
                return result
            return super().execute(statement, *args, **kwargs)
    def touch(_):
        with RacingSession(postgres_engine) as session:
            return auth.authenticate(session, RAW).user_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(touch, range(2))) == [uid, uid]
    assert len(set(connections)) == 2 and sorted(rows) == [0, 1]
    with Session(postgres_engine) as session:
        record = session.get(AuthSession, DIGEST)
        assert auth.aware(record.last_seen_at) == NOW
        assert auth.aware(record.expires_at) == NOW + timedelta(days=5)
        assert record.revoked_at is None


@pytest.mark.parametrize("winner", ["revoke", "absolute", "idle"])
def test_stale_touch_cannot_revive_invalid_session(postgres_engine, monkeypatch, winner):
    monkeypatch.setattr(auth, "utc_now", lambda: NOW)
    with Session(postgres_engine) as session:
        seed(session)
    ready, release = Event(), Event()
    rowcounts, pids = [], []
    class StaleSession(Session):
        def execute(self, statement, *args, **kwargs):
            if touch_statement(statement):
                pids.append(self.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
                ready.set()
                assert release.wait(timeout=10)
                result = super().execute(statement, *args, **kwargs)
                rowcounts.append(result.rowcount)
                return result
            return super().execute(statement, *args, **kwargs)
    def stale_touch():
        with StaleSession(postgres_engine) as session:
            with pytest.raises(auth.AuthError):
                auth.authenticate(session, RAW)
            assert session.is_active
            assert session.scalar(sa.select(User.id)) is not None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale_touch)
        try:
            assert ready.wait(timeout=10)
            with Session(postgres_engine) as session:
                pids.append(session.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
                if winner == "revoke":
                    auth.logout(session, RAW)
                else:
                    values = {"expires_at": NOW} if winner == "absolute" else {"last_seen_at": NOW - timedelta(hours=24)}
                    session.execute(sa.update(AuthSession).where(AuthSession.token_hash == DIGEST).values(**values))
                    session.commit()
        finally:
            release.set()
        future.result(timeout=10)
    assert rowcounts == [0] and len(set(pids)) == 2
    with Session(postgres_engine) as session:
        record = session.get(AuthSession, DIGEST)
        if winner == "revoke":
            assert auth.aware(record.revoked_at) == NOW
        elif winner == "absolute":
            assert auth.aware(record.expires_at) == NOW
        else:
            assert auth.aware(record.last_seen_at) == NOW - timedelta(hours=24)
        assert auth.aware(record.last_seen_at) != NOW
        with pytest.raises(auth.AuthError):
            auth.authenticate(session, RAW)
