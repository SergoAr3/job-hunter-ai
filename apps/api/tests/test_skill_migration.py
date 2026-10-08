import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.database import Base
from app.models import Job, Skill, User, UserProfile
from app.services.skill_reconciliation import reconcile_skills
from app.services.skill_sync import sync_job_skills, sync_profile_skills

API_ROOT = Path(__file__).parents[1]
FOUNDATION = {"skills", "skill_aliases", "job_skills", "user_skills"}


def run(connection, direction):
    spec = importlib.util.spec_from_file_location("skill_migration", API_ROOT / "alembic/versions/20261009_20_matching_foundation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


def compare_schema(connection):
    actual = sa.inspect(connection)
    # Compare the new tables to ORM metadata: names, nullability, lengths,
    # defaults, named constraints, FK actions, and indexes.
    for name in FOUNDATION:
        model = Base.metadata.tables[name]
        columns = {item["name"]:item for item in actual.get_columns(name)}
        assert set(columns) == set(model.columns.keys())
        for column in model.columns:
            assert columns[column.name]["nullable"] == column.nullable
            if isinstance(column.type, sa.String):
                assert columns[column.name]["type"].length == column.type.length
        assert actual.get_pk_constraint(name)["name"] == model.primary_key.name
        assert set(actual.get_pk_constraint(name)["constrained_columns"]) == set(model.primary_key.columns.keys())
        checks = {item["name"]:item["sqltext"] for item in actual.get_check_constraints(name)}
        assert set(checks) == {item.name for item in model.constraints if isinstance(item, sa.CheckConstraint)}
        uniques = {item["name"]:tuple(item["column_names"]) for item in actual.get_unique_constraints(name)}
        assert uniques == {item.name:tuple(item.columns.keys()) for item in model.constraints if isinstance(item, sa.UniqueConstraint)}
        fks = {item["name"]:(tuple(item["constrained_columns"]), item["referred_table"], item["options"].get("ondelete")) for item in actual.get_foreign_keys(name)}
        expected = {item.name:(tuple(item.columns.keys()), item.elements[0].column.table.name, item.ondelete) for item in model.foreign_key_constraints}
        assert fks == expected
        assert {item["name"] for item in actual.get_indexes(name)} - set(uniques) == {item.name for item in model.indexes}


def test_sqlite_additive_migration_preserves_semantics_round_trip():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE jobs (id INTEGER PRIMARY KEY, required_skills JSON NOT NULL, nice_to_have_skills JSON NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE user_profiles (id INTEGER PRIMARY KEY, skills JSON NOT NULL)")
        connection.exec_driver_sql('INSERT INTO jobs VALUES (1,\'["Python"]\',\'["Postgres"]\')')
        connection.exec_driver_sql('INSERT INTO user_profiles VALUES (1,\'["Postgres"]\')')
        before = [connection.exec_driver_sql(f"SELECT * FROM {name}").all() for name in ("jobs", "user_profiles")]
        run(connection, "upgrade")
        compare_schema(connection)
        assert all(connection.exec_driver_sql(f"SELECT count(*) FROM {name}").scalar_one() == 0 for name in FOUNDATION)
        connection.exec_driver_sql("INSERT INTO skills (id,canonical_name,normalized_key) VALUES (1,'Python','python')")
        connection.exec_driver_sql("INSERT INTO skill_aliases VALUES ('python',1,'Python')")
        connection.exec_driver_sql("INSERT INTO job_skills VALUES (1,1,'required')")
        connection.exec_driver_sql("INSERT INTO user_skills VALUES (1,1)")
        run(connection, "downgrade")
        assert not FOUNDATION.intersection(sa.inspect(connection).get_table_names())
        assert before == [connection.exec_driver_sql(f"SELECT * FROM {name}").all() for name in ("jobs", "user_profiles")]
        run(connection, "upgrade")
        compare_schema(connection)
    engine.dispose()


def test_postgres_full_migrated_chain_round_trip_and_isolated_apply_smoke(monkeypatch):
    if os.getenv("RUN_MATCHING_POSTGRES") != "1":
        pytest.skip("set RUN_MATCHING_POSTGRES=1 for disposable migration schema")
    import app.config as app_config
    admin = sa.create_engine(os.getenv("IDENTITY_TEST_DATABASE_URL", DATABASE_URL), hide_parameters=True)
    schema = f"skills_migration_{uuid4().hex}"
    engine = None
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        url = admin.url.update_query_dict({"options":f"-csearch_path={schema}"})
        monkeypatch.setattr(app_config, "DATABASE_URL", url.render_as_string(hide_password=False))
        config = Config(str(API_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(API_ROOT / "alembic"))
        command.upgrade(config, "20261006_19")
        engine = sa.create_engine(url, hide_parameters=True)
        with engine.begin() as connection:
            connection.execute(User.__table__.insert().values(id=1, telegram_id=1))
            connection.execute(UserProfile.__table__.insert().values(id=1, user_id=1, target_roles=["Engineer"], skills=["Postgres"]))
            connection.execute(Job.__table__.insert().values(id=1, source="company_site", source_url="https://example.com/migration", required_skills=["Python"], nice_to_have_skills=["Postgres"]))
        command.upgrade(config, "20261009_20")
        with engine.connect() as connection:
            compare_schema(connection)
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "20261009_20"
            assert connection.exec_driver_sql("SELECT count(*) FROM skills").scalar_one() == 0
            # PostgreSQL identity comparisons use deterministic C collation.
            assert connection.exec_driver_sql("SELECT collation_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='skills' AND column_name='normalized_key'").scalar_one() == "C"
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        dry = reconcile_skills(sessions, dry_run=True)
        assert dry["added"] == 3 and dry["schema_ready"]
        apply = reconcile_skills(sessions, dry_run=False)
        repeat = reconcile_skills(sessions, dry_run=False)
        assert apply["added"] == 3 and repeat["added"] == repeat["removed"] == 0
        command.downgrade(config, "20261006_19")
        with engine.connect() as connection:
            assert not FOUNDATION.intersection(sa.inspect(connection).get_table_names())
            assert connection.exec_driver_sql("SELECT skills FROM user_profiles WHERE id=1").scalar_one() == ["Postgres"]
            assert connection.exec_driver_sql("SELECT required_skills,nice_to_have_skills FROM jobs WHERE id=1").one() == (["Python"], ["Postgres"])
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "20261006_19"
        command.upgrade(config, "20261009_20")
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()
