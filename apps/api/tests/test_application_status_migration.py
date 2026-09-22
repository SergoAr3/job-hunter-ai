import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration_module():
    path = Path(__file__).parents[1] / "alembic/versions/20260922_11_expand_application_statuses.py"
    spec = importlib.util.spec_from_file_location("application_status_migration", path)
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


def _legacy_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "applications", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="saved"),
        sa.CheckConstraint(
            "status IN ('saved', 'applied', 'interview', 'offer', 'rejected')",
            name="ck_applications_status",
        ),
    )
    sa.Table(
        "application_status_history", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("application_id", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "status IN ('saved', 'applied', 'interview', 'offer', 'rejected')",
            name="ck_application_status_history_status",
        ),
    )
    metadata.create_all(connection)


def test_sqlite_status_migration_preserves_existing_data_and_accepts_new_statuses() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_schema(connection)
        connection.execute(sa.text("INSERT INTO applications (id, status) VALUES (1, 'offer')"))
        connection.execute(sa.text("INSERT INTO application_status_history (id, application_id, status) VALUES (1, 1, 'offer')"))

        _run_migration(connection, "upgrade")

        columns = sa.inspect(connection).get_columns("applications")
        assert str(next(column for column in columns if column["name"] == "status")["type"]) == "VARCHAR(32)"
        connection.execute(sa.text("INSERT INTO applications (id, status) VALUES (2, 'recruiter_response')"))
        connection.execute(sa.text("INSERT INTO application_status_history (id, application_id, status) VALUES (2, 2, 'withdrawn')"))
        assert connection.execute(sa.text("SELECT status FROM applications WHERE id = 1")).scalar_one() == "offer"
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(sa.text("INSERT INTO applications (id, status) VALUES (3, 'unknown')"))


def test_sqlite_status_migration_downgrade_refuses_to_lose_new_status_semantics() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_schema(connection)
        _run_migration(connection, "upgrade")
        connection.execute(sa.text("INSERT INTO applications (id, status) VALUES (1, 'hired')"))

        with pytest.raises(RuntimeError, match="explicit outcome data"):
            _run_migration(connection, "downgrade")
        assert connection.execute(sa.text("SELECT status FROM applications WHERE id = 1")).scalar_one() == "hired"


def test_sqlite_status_migration_downgrade_preserves_legacy_data_without_new_statuses() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_schema(connection)
        connection.execute(sa.text("INSERT INTO applications (id, status) VALUES (1, 'offer')"))
        _run_migration(connection, "upgrade")

        _run_migration(connection, "downgrade")

        assert connection.execute(sa.text("SELECT status FROM applications WHERE id = 1")).scalar_one() == "offer"
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(sa.text("INSERT INTO applications (id, status) VALUES (2, 'hired')"))
