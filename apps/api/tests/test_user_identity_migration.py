"""Identity migration checks; PostgreSQL uses an isolated, disposable schema.

RUN_IDENTITY_POSTGRES=1 enables PostgreSQL alongside SQLite. Optionally set
IDENTITY_TEST_DATABASE_URL; otherwise the API's configured database is used.
"""
import importlib.util
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.database import Base, get_session
from app.main import app
from app.models import User
from conftest import client


API_ROOT = Path(__file__).parents[1]
REVISION = "20261002_14"
PREVIOUS = "20260923_13"
ACCOUNT_COLUMNS = {"email", "email_canonical", "password_hash", "email_verified_at", "display_name"}


def migration():
    spec = importlib.util.spec_from_file_location(
        "user_identity_migration", API_ROOT / "alembic/versions/20261002_14_user_account_identity.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_migration(connection, direction):
    module = migration()
    previous_proxy = getattr(module.op, "_proxy", None)
    module.op._proxy = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, direction)()
    finally:
        module.op._proxy = previous_proxy


@pytest.fixture(params=["sqlite", "postgresql"])
def connection(request):
    if request.param == "postgresql":
        if os.getenv("RUN_IDENTITY_POSTGRES") != "1":
            pytest.skip("set RUN_IDENTITY_POSTGRES=1 for isolated PostgreSQL checks")
        engine = sa.create_engine(os.getenv("IDENTITY_TEST_DATABASE_URL", DATABASE_URL))
        schema = f"identity_test_{uuid.uuid4().hex}"
        with engine.connect() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            conn.exec_driver_sql(f'SET search_path TO "{schema}"')
            conn.commit()
            try:
                yield conn
            finally:
                conn.rollback()
                conn.exec_driver_sql('SET search_path TO public')
                conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
                conn.commit()
    else:
        engine = sa.create_engine("sqlite://", connect_args={"check_same_thread": False})
        with engine.connect() as conn:
            # Mirrors env.py: off BEFORE the transaction, validate before commit.
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            yield conn
            conn.rollback()
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()
    engine.dispose()


def legacy_schema(connection):
    # Other tables match revision 13. Only users is replaced with its actual
    # pre-identity definition; no changes to domain FK tables are under test.
    metadata = sa.MetaData()
    sa.Table(
        "users", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("username", sa.String(255)),
        sa.Column("first_name", sa.String(255), nullable=False),
        sa.Column("last_name", sa.String(255)),
        sa.Column("language_code", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for table in Base.metadata.sorted_tables:
        if table.name != "users":
            table.to_metadata(metadata)
    metadata.create_all(connection)
    return metadata


def seed_graph(connection, metadata):
    timestamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    connection.execute(metadata.tables["users"].insert(), {
        "id": 41, "telegram_id": 123456, "first_name": "Legacy", "username": "legacy",
        "last_name": "User", "language_code": "ru", "created_at": timestamp,
    })
    connection.execute(metadata.tables["user_profiles"].insert(), {
        "id": 101, "user_id": 41, "target_roles": ["Developer"], "skills": ["Python"],
    })
    connection.execute(metadata.tables["work_experiences"].insert(), {"id": 102, "user_profile_id": 101, "company": "Legacy Co"})
    connection.execute(metadata.tables["profile_experience_facts"].insert(), {"id": 103, "user_profile_id": 101, "text": "Legacy fact"})
    connection.execute(metadata.tables["jobs"].insert(), {"id": 301, "source": "company_site", "source_url": "https://example.com/legacy", "title": "Developer"})
    connection.execute(metadata.tables["applications"].insert(), {"id": 201, "user_id": 41, "job_id": 301, "status": "applied"})
    connection.execute(metadata.tables["application_status_history"].insert(), {"id": 401, "application_id": 201, "status": "applied"})
    connection.execute(metadata.tables["application_match_snapshots"].insert(), {
        "id": 501, "application_id": 201, "trigger_status_history_id": 401,
        "capture_status": "captured", "algorithm_version": "legacy-v1", "score": 80,
        "verdict": "high", "coverage": 90, "confidence": "high",
        "profile_updated_at": timestamp, "job_updated_at": timestamp,
        "job_parsing_status": "success", "job_ai_enrichment_status": "success",
        "inputs": {"skills": ["Python"]}, "result_detail": {"score": 80},
    })


def graph(connection):
    return {
        name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY id').mappings().all()
        for name in ("user_profiles", "work_experiences", "profile_experience_facts", "jobs", "applications", "application_status_history", "application_match_snapshots")
    }


def assert_schema_matches_model(connection):
    inspector = sa.inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert set(columns) == set(User.__table__.columns.keys())
    for column in User.__table__.columns:
        assert columns[column.name]["nullable"] == column.nullable
    assert {item["name"] for item in inspector.get_check_constraints("users")} == {
        constraint.name for constraint in User.__table__.constraints if isinstance(constraint, sa.CheckConstraint)
    }
    assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints("users")} == {
        ("telegram_id",), ("email_canonical",),
    }


def test_upgrade_preserves_entire_telegram_graph_and_bot_resolver(connection):
    metadata = legacy_schema(connection)
    seed_graph(connection, metadata)
    before = graph(connection)
    old_user = connection.exec_driver_sql("SELECT * FROM users WHERE id=41").mappings().one()
    run_migration(connection, "upgrade")
    assert_schema_matches_model(connection)
    user = connection.exec_driver_sql("SELECT * FROM users WHERE id=41").mappings().one()
    assert {key: user[key] for key in old_user} == dict(old_user)
    assert all(user[key] is None for key in ACCOUNT_COLUMNS)
    assert graph(connection) == before

    def migrated_session():
        with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
            yield session

    previous_override = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = migrated_session
    try:
        response = client.post("/users/telegram", json={"telegram_id": 123456, "first_name": "Updated"})
    finally:
        app.dependency_overrides[get_session] = previous_override
    assert response.status_code == 200
    assert response.json() == {"id": 41, "telegram_id": 123456, "created": False}
    assert connection.exec_driver_sql("SELECT count(*) FROM users").scalar_one() == 1
    assert graph(connection) == before
    if connection.dialect.name == "sqlite":
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    connection.commit()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("DELETE FROM users WHERE id=41")
        connection.rollback()
        assert graph(connection) == before


@pytest.mark.parametrize("telegram_id,email", [(123, None), (None, "User@Example.com"), (123, "User@Example.com")])
def test_migrated_schema_accepts_identity_states(connection, telegram_id, email):
    legacy_schema(connection)
    run_migration(connection, "upgrade")
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        session.add(User(telegram_id=telegram_id, email=email, email_canonical="user@example.com" if email else None, password_hash="placeholder" if email else None))
        session.flush()


@pytest.mark.parametrize("fields", [
    {}, {"telegram_id": 123, "email": "user@example.com"},
    {"telegram_id": 123, "password_hash": "placeholder"},
    {"telegram_id": 123, "email": "user@example.com", "password_hash": "placeholder"},
    {"telegram_id": 123, "email_canonical": "user@example.com", "password_hash": "placeholder"},
    {"telegram_id": 123, "email": "user@example.com", "email_canonical": "user@example.com"},
    {"email": "user@example.com", "email_canonical": "User@example.com", "password_hash": "placeholder"},
    {"email": "user@example.com", "email_canonical": "different@example.com", "password_hash": "placeholder"},
    {"email": "Üser@example.com", "email_canonical": "üser@example.com", "password_hash": "placeholder"},
    {"email": "user@example.com\n", "email_canonical": "user@example.com\n", "password_hash": "placeholder"},
])
def test_migrated_schema_rejects_invalid_identities(connection, fields):
    legacy_schema(connection)
    run_migration(connection, "upgrade")
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        session.add(User(**fields))
        with pytest.raises(sa.exc.IntegrityError):
            session.flush()
        session.rollback()
        session.add(User(telegram_id=456))
        session.flush()


def test_migrated_email_and_telegram_uniqueness(connection):
    legacy_schema(connection)
    run_migration(connection, "upgrade")
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        session.add(User(telegram_id=123, email="User@Example.com", email_canonical="user@example.com", password_hash="placeholder"))
        session.flush()
        for fields in (
            {"email": "user@example.com", "email_canonical": "user@example.com", "password_hash": "placeholder"},
            {"telegram_id": 123},
        ):
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(User(**fields))
                session.flush()
        assert session.query(User).count() == 1


def test_downgrade_round_trip_preserves_graph(connection):
    metadata = legacy_schema(connection)
    seed_graph(connection, metadata)
    before = graph(connection)
    run_migration(connection, "upgrade")
    run_migration(connection, "downgrade")
    assert graph(connection) == before
    columns = {column["name"]: column for column in sa.inspect(connection).get_columns("users")}
    assert not ACCOUNT_COLUMNS.intersection(columns)
    assert not columns["telegram_id"]["nullable"]
    assert not columns["first_name"]["nullable"]
    assert connection.exec_driver_sql("SELECT id, telegram_id FROM users").one() == (41, 123456)
    if connection.dialect.name == "sqlite":
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    connection.commit()


@pytest.mark.parametrize("fields", [
    {"telegram_id": None, "first_name": None, "email": "user@example.com", "email_canonical": "user@example.com", "password_hash": "placeholder"},
    {"telegram_id": 123, "first_name": "Name", "email": "user@example.com", "email_canonical": "user@example.com", "password_hash": "placeholder"},
    {"telegram_id": 123, "first_name": None},
    {"telegram_id": 123, "first_name": "Name", "display_name": "Web name"},
])
def test_downgrade_refuses_data_loss_before_schema_changes(connection, fields):
    legacy_schema(connection)
    run_migration(connection, "upgrade")
    connection.execute(User.__table__.insert(), fields)
    before = connection.exec_driver_sql("SELECT * FROM users").mappings().all()
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        run_migration(connection, "downgrade")
    assert_schema_matches_model(connection)
    assert connection.exec_driver_sql("SELECT * FROM users").mappings().all() == before


def test_sqlite_alembic_environment_migrates_referenced_users_atomically(tmp_path, monkeypatch):
    import app.config as app_config

    path = tmp_path / "identity.sqlite"
    url = f"sqlite:///{path}"
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        metadata = legacy_schema(connection)
        seed_graph(connection, metadata)
        before = graph(connection)
    monkeypatch.setattr(app_config, "DATABASE_URL", url)
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.stamp(config, PREVIOUS)
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        assert_schema_matches_model(connection)
        assert graph(connection) == before
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        connection.execute(User.__table__.insert(), {"email": "User@Example.com", "email_canonical": "user@example.com", "password_hash": "placeholder"})
        connection.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        command.downgrade(config, PREVIOUS)
    with engine.begin() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == REVISION
        assert_schema_matches_model(connection)
        connection.execute(sa.delete(User).where(User.telegram_id.is_(None)))
    command.downgrade(config, PREVIOUS)
    with engine.connect() as connection:
        assert graph(connection) == before
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PREVIOUS
    engine.dispose()


def test_sqlite_foreign_key_failure_rolls_back_schema_and_revision(tmp_path, monkeypatch):
    import app.config as app_config

    url = f"sqlite:///{tmp_path / 'invalid_graph.sqlite'}"
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        legacy_schema(connection)
    monkeypatch.setattr(app_config, "DATABASE_URL", url)
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.stamp(config, PREVIOUS)
    with engine.begin() as connection:
        # Deliberately corrupt legacy data with enforcement off.
        connection.execute(Base.metadata.tables["user_profiles"].insert(), {"user_id": 999})
    with pytest.raises(RuntimeError, match="invalid foreign keys"):
        command.upgrade(config, REVISION)
    with engine.connect() as connection:
        columns = {column["name"] for column in sa.inspect(connection).get_columns("users")}
        assert not ACCOUNT_COLUMNS.intersection(columns)
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PREVIOUS
        assert "_alembic_tmp_users" not in sa.inspect(connection).get_table_names()
        assert connection.exec_driver_sql("SELECT user_id FROM user_profiles").scalar_one() == 999
    engine.dispose()


def test_postgres_full_alembic_chain_and_round_trip(connection, monkeypatch):
    if connection.dialect.name != "postgresql":
        pytest.skip("historical migrations include PostgreSQL-only JSONB")
    import app.config as app_config

    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    connection.commit()
    url = connection.engine.url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(app_config, "DATABASE_URL", url.render_as_string(hide_password=False))
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(config, PREVIOUS)
    metadata = sa.MetaData()
    metadata.reflect(connection)
    seed_graph(connection, metadata)
    before = graph(connection)
    connection.commit()
    command.upgrade(config, REVISION)
    assert_schema_matches_model(connection)
    assert graph(connection) == before
    assert connection.exec_driver_sql("SELECT id, telegram_id FROM users").one() == (41, 123456)
    connection.commit()
    command.downgrade(config, PREVIOUS)
    assert graph(connection) == before
    assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PREVIOUS
    assert connection.exec_driver_sql("SELECT id, telegram_id FROM users").one() == (41, 123456)
    connection.commit()


def test_identity_attachment_changes_no_domain_rows(connection):
    metadata = legacy_schema(connection)
    seed_graph(connection, metadata)
    before = graph(connection)
    run_migration(connection, "upgrade")
    with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
        user = session.get(User, 41)
        user.email = "User@Example.com"
        user.email_canonical = "user@example.com"
        user.password_hash = "test-only-placeholder"
        user.display_name = "Web display"
        user.email_verified_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
        session.commit()
        user.telegram_id = None
        session.commit()
        user.telegram_id = 123456
        session.commit()
        assert user.id == 41
    assert graph(connection) == before
    assert connection.exec_driver_sql("SELECT count(*) FROM users").scalar_one() == 1

    def migrated_session():
        with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
            yield session

    previous_override = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = migrated_session
    try:
        response = client.post("/users/telegram", json={"telegram_id": 123456, "first_name": "Refreshed"})
    finally:
        app.dependency_overrides[get_session] = previous_override
    assert response.json() == {"id": 41, "telegram_id": 123456, "created": False}
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        user = session.get(User, 41)
        assert (user.email, user.email_canonical, user.password_hash, user.display_name) == (
            "User@Example.com", "user@example.com", "test-only-placeholder", "Web display",
        )
        assert user.email_verified_at.replace(tzinfo=timezone.utc) == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert user.first_name == "Refreshed"
    assert graph(connection) == before


@pytest.mark.parametrize("schema_kind", ["model", "migrated"])
@pytest.mark.parametrize("email,canonical", [
    ("user@example.com\x00suffix", "user@example.com\x00suffix"),
    ("user\x00@example.com", "user\x00@example.com"),
    ("user@example.com", "user@example.com\x00suffix"),
    ("user@example.com\x00\nÜ", "user@example.com\x00\nü"),
])
def test_email_nul_is_rejected_by_both_database_schemas(connection, schema_kind, email, canonical):
    if schema_kind == "model":
        User.__table__.create(connection)
    else:
        legacy_schema(connection)
        run_migration(connection, "upgrade")
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        session.add(User(email=email, email_canonical=canonical, password_hash="placeholder"))
        # SQLite rejects via CHECK; psycopg/PostgreSQL reject NUL text as DataError.
        with pytest.raises((sa.exc.IntegrityError, sa.exc.DataError)):
            session.flush()
        session.rollback()
        session.add(User(email="User+tag@Example.com", email_canonical="user+tag@example.com", password_hash="placeholder"))
        session.flush()
        assert session.query(User).count() == 1
