import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.database import Base
from app.models import DiscoverFeedChain as Chain, DiscoverFeedState as State
from test_discover_feed_postgres import pg_sessions  # fixture; disposable schemas only


def migration(connection, action):
    path = Path(__file__).parents[1] / "alembic/versions/20261016_21_discover_feed.py"
    spec = importlib.util.spec_from_file_location("feed_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = getattr(module.op, "_proxy", None)
    module.op._proxy = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, action)()
    finally:
        module.op._proxy = previous


def check_roundtrip(connection):
    before = set(sa.inspect(connection).get_table_names())
    migration(connection, "upgrade")
    inspector = sa.inspect(connection)
    expected = {"ix_feed_state_prev": ["prev_token"], "ix_feed_state_next": ["next_token"]}
    indexes = {item["name"]: item["column_names"] for item in inspector.get_indexes("discover_feed_states")}
    assert all(indexes.get(name) == columns for name, columns in expected.items())
    for table in (Chain.__table__, State.__table__):
        columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        assert set(columns) == {column.name for column in table.columns}
        assert {name: value["nullable"] for name, value in columns.items()} == {column.name: column.nullable for column in table.columns}
        assert {item["name"] for item in inspector.get_indexes(table.name)} == {index.name for index in table.indexes}
        assert {item["name"] for item in inspector.get_check_constraints(table.name)} == {constraint.name for constraint in table.constraints if isinstance(constraint, sa.CheckConstraint)}
    def include_object(obj, name, kind, reflected, compare_to):
        table = obj if kind == "table" else getattr(obj, "table", None)
        return table is not None and table.name in {Chain.__tablename__, State.__tablename__}
    context = MigrationContext.configure(connection, opts={"include_object": include_object, "compare_type": True, "compare_server_default": True})
    assert compare_metadata(context, Base.metadata) == []
    # Constraints work in both dialects, including SQL NULL semantics.
    connection.execute(sa.text("INSERT INTO users (id, telegram_id) VALUES (100, 100)"))
    connection.execute(sa.text("INSERT INTO discover_feed_chains (id, creator_id, version, identity_json, created_at, expires_at) VALUES ('chain',100,1,'{}','2026-10-16','2026-10-17')"))
    for status, result, lease, until in [("complete", None, None, None), ("claimed", None, None, None), ("ready", "page", None, None)]:
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.execute(sa.text("INSERT INTO discover_feed_states (token,chain_id,status,payload,result_state,lease_token,lease_until) VALUES (:token,'chain',:status,'{}',:result,:lease,:until)"),
                                   {"token": "x" * 32, "status": status, "result": result, "lease": lease, "until": until})
    migration(connection, "downgrade")
    assert set(sa.inspect(connection).get_table_names()) == before
    assert connection.execute(sa.text("SELECT telegram_id FROM users WHERE id=100")).scalar_one() == 100
    migration(connection, "upgrade")


def test_sqlite_migration_model_agreement_and_upgrade_downgrade():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_id BIGINT)")
        check_roundtrip(connection)
    engine.dispose()


def test_postgres_migration_model_agreement_and_upgrade_downgrade(pg_sessions):
    engine = pg_sessions.kw["bind"]
    with engine.begin() as connection:
        Base.metadata.drop_all(connection, tables=[State.__table__, Chain.__table__])
        check_roundtrip(connection)


def test_single_alembic_head_and_expected_parent():
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["20261016_21"]
    assert scripts.get_revision("20261016_21").down_revision == "20261009_20"
