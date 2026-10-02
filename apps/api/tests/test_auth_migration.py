"""Revision 15 parity and concurrent identity writes in isolated databases."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import Base, get_session
from app.main import app
from app.models import AuthSession, User
from app.services.auth import get_passwords
from test_user_identity_migration import (
    API_ROOT, connection, legacy_schema, seed_graph, graph,
)


def config_for(url, monkeypatch):
    import app.config as app_config
    monkeypatch.setattr(app_config, "DATABASE_URL", url)
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    # Keep Alembic fileConfig from disabling the application/test loggers.
    config.config_file_name = None
    return config


def test_real_revision15_upgrade_downgrade_preserves_accounts(tmp_path, monkeypatch, connection):
    dialect = connection.dialect.name
    if dialect == "sqlite":
        url = f"sqlite:///{tmp_path / 'sessions.sqlite'}"
    else:
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        connection.commit()
        url = connection.engine.url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    config = config_for(url, monkeypatch)
    if dialect == "sqlite":
        with engine.begin() as conn:
            metadata = legacy_schema(conn)
            seed_graph(conn, metadata)
        command.stamp(config, "20260923_13")
        command.upgrade(config, "20261002_14")
    else:
        command.upgrade(config, "20260923_13")
        with engine.begin() as conn:
            metadata = sa.MetaData()
            metadata.reflect(conn)
            seed_graph(conn, metadata)
        command.upgrade(config, "20261002_14")
    with engine.begin() as conn:
        conn.execute(User.__table__.insert(), {"id": 42, "email": "User+tag@Example.com", "email_canonical": "user+tag@example.com", "password_hash": "preserved-hash"})
        before_users = conn.exec_driver_sql("SELECT * FROM users ORDER BY id").mappings().all()
        before_graph = graph(conn)
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "20261002_15"
        inspector = sa.inspect(conn)
        columns = {c["name"]: c for c in inspector.get_columns("auth_sessions")}
        assert set(columns) == set(AuthSession.__table__.columns.keys())
        assert all(columns[c.name]["nullable"] == c.nullable for c in AuthSession.__table__.columns)
        assert inspector.get_pk_constraint("auth_sessions")["constrained_columns"] == ["token_hash"]
        assert {i["name"] for i in inspector.get_indexes("auth_sessions")} == {"ix_auth_sessions_user_id", "ix_auth_sessions_expires_at"}
        fk = inspector.get_foreign_keys("auth_sessions")[0]
        assert fk["referred_table"] == "users" and fk["options"]["ondelete"] == "CASCADE"
        assert {c["name"] for c in inspector.get_check_constraints("auth_sessions")} == {"ck_auth_sessions_expiry", "ck_auth_sessions_last_seen"}
        if dialect == "sqlite":
            conn.rollback()
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        now = datetime.now(timezone.utc)
        fields = dict(token_hash="a" * 64, user_id=42, created_at=now, last_seen_at=now, expires_at=now + timedelta(days=7))
        conn.execute(AuthSession.__table__.insert(), fields)
        conn.commit()
        for invalid in ({"user_id": 999}, {"expires_at": now}, {"last_seen_at": now - timedelta(seconds=1)}):
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(AuthSession.__table__.insert(), fields | {"token_hash": "b" * 64} | invalid)
                conn.commit()
            conn.rollback()
        assert conn.exec_driver_sql("SELECT * FROM users ORDER BY id").mappings().all() == before_users
        assert graph(conn) == before_graph
        conn.rollback()
    command.downgrade(config, "20261002_14")
    with engine.connect() as conn:
        assert "auth_sessions" not in sa.inspect(conn).get_table_names()
        assert conn.exec_driver_sql("SELECT * FROM users ORDER BY id").mappings().all() == before_users
        assert graph(conn) == before_graph
    command.upgrade(config, "head")
    with engine.connect() as conn:
        if dialect == "sqlite":
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.execute(AuthSession.__table__.insert(), fields)
        conn.execute(sa.delete(User).where(User.id == 42))
        conn.commit()
        assert conn.scalar(sa.select(sa.func.count()).select_from(AuthSession)) == 0
        assert graph(conn) == before_graph
    engine.dispose()


def test_concurrent_registration_and_telegram_resolution(tmp_path, connection, monkeypatch):
    dialect = connection.dialect.name
    if dialect == "sqlite":
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'concurrent.sqlite'}", connect_args={"check_same_thread": False, "timeout": 10})
    else:
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        connection.commit()
        engine = sa.create_engine(connection.engine.url.update_query_dict({"options": f"-csearch_path={schema}"}))
    Base.metadata.create_all(engine)
    previous = app.dependency_overrides[get_session]
    def request_session():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = request_session
    passwords = get_passwords()
    original_hash = passwords.hash
    barrier = Barrier(2)
    def synchronized_hash(password):
        result = original_hash(password)
        barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(passwords, "hash", synchronized_hash)
    try:
        client = TestClient(app)
        payloads = [dict(email="User+tag@Example.com", password="first password kept", display_name="First"),
                    dict(email="user+tag@example.com", password="second password kept", display_name="Second")]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda p: client.post("/auth/register", json=p), payloads))
        assert [r.status_code for r in responses] == [202, 202]
        assert responses[0].json() == responses[1].json()
        with Session(engine) as session:
            users = session.scalars(sa.select(User)).all()
            assert len(users) == 1
            user = users[0]
            winning = next(p for p in payloads if p["display_name"] == user.display_name)
            losing = next(p for p in payloads if p != winning)
            assert passwords.verify(user.password_hash, winning["password"])
            assert not passwords.verify(user.password_hash, losing["password"])
            assert user.telegram_id is None and user.email_canonical == "user+tag@example.com"
        # Two valid logins racing to rehash must both create independent sessions.
        from argon2 import PasswordHasher, Type
        obsolete = PasswordHasher(type=Type.ID, time_cost=1, memory_cost=1024, parallelism=1).hash(winning["password"])
        with Session(engine) as session:
            session.execute(sa.update(User).values(password_hash=obsolete))
            session.commit()
        monkeypatch.setattr(passwords, "hash", original_hash)
        original_verify = passwords.verify
        barrier = Barrier(2)
        def synchronized_verify(encoded, password):
            verified = original_verify(encoded, password)
            if encoded == obsolete:
                barrier.wait(timeout=10)
            return verified
        monkeypatch.setattr(passwords, "verify", synchronized_verify)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post("/auth/login", json={"email": winning["email"], "password": winning["password"]}), range(2)))
        assert [r.status_code for r in responses] == [200, 200]
        assert responses[0].json()["session_token"] != responses[1].json()["session_token"]
        with Session(engine) as session:
            assert session.scalar(sa.select(sa.func.count()).select_from(AuthSession)) == 2
        monkeypatch.setattr(passwords, "verify", original_verify)
        barrier = Barrier(2)
        def telegram(_):
            barrier.wait(timeout=10)
            return client.post("/users/telegram", json={"telegram_id": 654321, "first_name": "Same actor"},
                               headers={"X-Bot-Service-Token": os.environ["BOT_API_SERVICE_TOKEN"]})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(telegram, range(2)))
        assert [r.status_code for r in responses] == [200, 200]
        assert responses[0].json()["id"] == responses[1].json()["id"]
        assert sorted(r.json()["created"] for r in responses) == [False, True]
        with Session(engine) as session:
            assert session.scalar(sa.select(sa.func.count()).select_from(User)) == 2
    finally:
        app.dependency_overrides[get_session] = previous
        engine.dispose()


def test_auth_lifecycle_on_supported_databases(connection):
    Base.metadata.create_all(connection)
    connection.commit()
    previous = app.dependency_overrides[get_session]
    def request_session():
        with Session(bind=connection, expire_on_commit=False) as session:
            yield session
    app.dependency_overrides[get_session] = request_session
    client = TestClient(app)
    try:
        body = {"email": "Parity+tag@Example.com", "password": "  Unicode пароль 🔑  "}
        assert client.post("/auth/register", json=body).status_code == 202
        response = client.post("/auth/login", json=body)
        assert response.status_code == 200
        raw = response.json()["session_token"]
        headers = {"Authorization": "Bearer " + raw}
        assert client.get("/auth/me", headers=headers).status_code == 200
        record = connection.execute(sa.select(AuthSession.__table__)).mappings().one()
        uid = record["user_id"]
        assert raw != record["token_hash"]
        now = datetime.now(timezone.utc)
        connection.execute(sa.update(AuthSession).values(created_at=now - timedelta(hours=3), last_seen_at=now - timedelta(hours=2)))
        connection.commit()
        assert client.get(f"/users/{uid}/applications", headers=headers).status_code == 200
        assert client.get(f"/users/{uid + 1}/applications", headers=headers).status_code == 404
        touched = connection.scalar(sa.select(AuthSession.last_seen_at))
        if touched.tzinfo is None:
            touched = touched.replace(tzinfo=timezone.utc)
        assert touched > now - timedelta(minutes=1)
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.get("/auth/me", headers=headers).status_code == 401
        for expired_values in ({"created_at": now - timedelta(days=8), "expires_at": now - timedelta(seconds=1), "last_seen_at": now - timedelta(days=8)},
                               {"created_at": now - timedelta(days=2), "last_seen_at": now - timedelta(hours=25)}):
            response = client.post("/auth/login", json=body)
            assert response.status_code == 200
            raw = response.json()["session_token"]
            from app.services.auth import token_hash
            connection.execute(sa.update(AuthSession).where(AuthSession.token_hash == token_hash(raw)).values(**expired_values))
            connection.commit()
            assert client.get("/auth/me", headers={"Authorization": "Bearer " + raw}).status_code == 401
    finally:
        app.dependency_overrides[get_session] = previous
