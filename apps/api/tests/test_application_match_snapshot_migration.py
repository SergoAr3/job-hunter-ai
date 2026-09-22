import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/20260922_12_create_application_match_snapshots.py"
    )
    spec = importlib.util.spec_from_file_location("application_match_snapshot_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(connection: sa.Connection, name: str) -> None:
    module = _migration_module()
    previous_proxy = getattr(module.op, "_proxy", None)
    module.op._proxy = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, name)()
    finally:
        module.op._proxy = previous_proxy


def _existing_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "applications",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    sa.Table(
        "application_status_history",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
    )
    metadata.create_all(connection)


def test_sqlite_snapshot_migration_has_no_backfill_and_round_trips() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _existing_schema(connection)
        connection.execute(sa.text("INSERT INTO applications (id) VALUES (1)"))
        connection.execute(sa.text(
            "INSERT INTO application_status_history (id, application_id, status) "
            "VALUES (1, 1, 'applied')"
        ))

        _run_migration(connection, "upgrade")

        inspector = sa.inspect(connection)
        assert "application_match_snapshots" in inspector.get_table_names()
        assert connection.execute(
            sa.text("SELECT count(*) FROM application_match_snapshots")
        ).scalar_one() == 0

        connection.execute(sa.text(
            "INSERT INTO application_match_snapshots "
            "(application_id, trigger_status_history_id, capture_status, unavailable_reason) "
            "VALUES (1, 1, 'unavailable', 'profile_missing')"
        ))
        assert connection.execute(
            sa.text("SELECT capture_status FROM application_match_snapshots")
        ).scalar_one() == "unavailable"

        _run_migration(connection, "downgrade")

        assert "application_match_snapshots" not in sa.inspect(connection).get_table_names()
        assert connection.execute(sa.text("SELECT status FROM application_status_history"))\
            .scalar_one() == "applied"
