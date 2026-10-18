"""Real Alembic roundtrips; production/public tables are never migrated."""
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.database import Base
from app.models import (
    DiscoverInventoryGeneration, DiscoverInventoryItem, DiscoverInventoryKey, DiscoverInventoryView,
)
from test_auth_migration import config_for
from test_discover_feed_postgres import pg_sessions

TABLES = [model.__table__ for model in (DiscoverInventoryKey, DiscoverInventoryGeneration,
                                      DiscoverInventoryItem, DiscoverInventoryView)]


def roundtrip(url, monkeypatch, expected_schema=None):
    parsed = sa.engine.make_url(url)
    if parsed.get_backend_name() == "postgresql":
        assert expected_schema is not None and expected_schema.startswith("feed_test_")
        assert f"-csearch_path={expected_schema}" in parsed.query.get("options", "")
    engine = sa.create_engine(url)
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT current_schema()").scalar_one() == expected_schema
    config = config_for(url, monkeypatch)
    if engine.dialect.name == "sqlite":
        # Historical revision 03 uses PostgreSQL's regexp operator. As existing
        # migration tests do, establish the parent schema and stamp it; exercise
        # the actual new revision through Alembic, without changing old DDL.
        with engine.begin() as connection:
            Base.metadata.create_all(connection, tables=[t for t in Base.metadata.sorted_tables
                                                         if t not in TABLES])
        command.stamp(config, "20261016_21")
    else:
        command.upgrade(config, "20261016_21")
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, telegram_id) VALUES (998, 998)"))
        connection.execute(sa.text("INSERT INTO discover_feed_chains (id,creator_id,version,identity_json,created_at,expires_at) VALUES ('preserved',998,1,'{}','2026-10-18','2026-10-19')"))
        connection.execute(sa.text("INSERT INTO discover_feed_states (token,chain_id,status,payload) VALUES (:token,'preserved','ready','{}')"), {"token": "p" * 32})
        before = {name: connection.execute(sa.text(f"SELECT * FROM {name}")).mappings().all()
                  for name in ("users", "discover_feed_chains", "discover_feed_states")}
    for _ in range(2):
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "20261018_22"
            inspector = sa.inspect(connection)
            for table in TABLES:
                columns = {column["name"]: column for column in inspector.get_columns(table.name)}
                assert set(columns) == set(table.columns.keys())
                assert {name: column["nullable"] for name, column in columns.items()} == {column.name: column.nullable for column in table.columns}
                assert {c["name"] for c in inspector.get_check_constraints(table.name)} == {c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)}
                assert {i["name"] for i in inspector.get_indexes(table.name) if not i.get("duplicates_constraint")} == {i.name for i in table.indexes}
                assert set(inspector.get_pk_constraint(table.name)["constrained_columns"]) == {c.name for c in table.primary_key}
                assert {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name)} == {tuple(c.name for c in constraint.columns) for constraint in table.constraints if isinstance(constraint, sa.UniqueConstraint)}
                assert {(tuple(fk["constrained_columns"]), fk["referred_table"], fk["options"].get("ondelete")) for fk in inspector.get_foreign_keys(table.name)} == {
                    ((fk.parent.name,), fk.column.table.name, fk.ondelete) for fk in table.foreign_keys}
            names = {table.name for table in TABLES}
            def include(obj, name, kind, reflected, compare_to):
                table = obj if kind == "table" else getattr(obj, "table", None)
                return table is not None and table.name in names
            context = MigrationContext.configure(connection, opts={"include_object": include, "compare_type": True, "compare_server_default": True})
            assert compare_metadata(context, Base.metadata) == []
            index = next(i for i in inspector.get_indexes("discover_inventory_generations") if i["name"] == "uq_inventory_building_key")
            assert index["unique"] and any("building" in str(value) for value in index["dialect_options"].values())
        command.downgrade(config, "20261016_21")
        with engine.connect() as connection:
            assert not names.intersection(sa.inspect(connection).get_table_names())
            for name, rows in before.items():
                assert connection.execute(sa.text(f"SELECT * FROM {name}")).mappings().all() == rows
    command.upgrade(config, "head")
    assert ScriptDirectory.from_config(config).get_heads() == ["20261018_22"]
    assert ScriptDirectory.from_config(config).get_revision("20261018_22").down_revision == "20261016_21"
    engine.dispose()


def test_sqlite_real_alembic_roundtrip(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'inventory.sqlite'}", monkeypatch)


def test_postgres_real_alembic_roundtrip(pg_sessions, monkeypatch):
    engine = pg_sessions.kw["bind"]
    # This engine's search_path is the fixture's unique disposable schema only.
    with engine.begin() as connection:
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        assert schema.startswith("feed_test_")
        Base.metadata.drop_all(connection)
    url = engine.url.update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=5000"})
    roundtrip(url.render_as_string(hide_password=False), monkeypatch, expected_schema=schema)


def test_postgres_roundtrip_refuses_unscoped_database(monkeypatch):
    import pytest
    with pytest.raises(AssertionError):
        roundtrip("postgresql+psycopg://localhost/unused", monkeypatch)
