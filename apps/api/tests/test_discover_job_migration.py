import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/20260923_13_add_discover_job_identity.py"
    )
    spec = importlib.util.spec_from_file_location("discover_job_migration", path)
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


def _legacy_jobs(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "jobs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("ingestion_method", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("source_url", sa.String(2048), nullable=False, unique=True),
        sa.Column("title", sa.String(512)),
        sa.CheckConstraint(
            "source IN ('linkedin', 'hh', 'greenhouse', 'lever', 'company_site')",
            name="ck_jobs_source",
        ),
        sa.CheckConstraint(
            "ingestion_method IN ('manual')", name="ck_jobs_ingestion_method"
        ),
    )
    metadata.create_all(connection)


def test_sqlite_migration_preserves_legacy_rows_and_external_identity_is_unique() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_jobs(connection)
        connection.execute(
            sa.text(
                "INSERT INTO jobs (id, source, ingestion_method, source_url, title) "
                "VALUES (1, 'company_site', 'manual', 'https://example.com/1', 'Legacy')"
            )
        )

        _run_migration(connection, "upgrade")

        row = connection.execute(
            sa.text(
                "SELECT title, external_id, source_scope, source_updated_at, fetched_at "
                "FROM jobs WHERE id = 1"
            )
        ).one()
        assert row == ("Legacy", None, None, None, None)
        connection.execute(
            sa.text(
                "INSERT INTO jobs "
                "(id, source, ingestion_method, source_url, external_id, source_scope) "
                "VALUES (2, 'trudvsem', 'discover', 'https://trudvsem.ru/1', 'vac-1', 'company-1')"
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO jobs "
                    "(id, source, ingestion_method, source_url, external_id, source_scope) "
                    "VALUES (3, 'trudvsem', 'discover', 'https://trudvsem.ru/2', 'vac-1', 'company-1')"
                )
            )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO jobs "
                    "(id, source, ingestion_method, source_url, external_id, source_scope) "
                    "VALUES (4, 'trudvsem', 'discover', 'https://trudvsem.ru/3', 'vac-2', NULL)"
                )
            )


def test_sqlite_migration_allows_multiple_legacy_null_identities_and_round_trips() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_jobs(connection)
        connection.execute(
            sa.text(
                "INSERT INTO jobs (id, source, ingestion_method, source_url) VALUES "
                "(1, 'company_site', 'manual', 'https://example.com/1'), "
                "(2, 'company_site', 'manual', 'https://example.com/2')"
            )
        )
        _run_migration(connection, "upgrade")
        assert connection.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one() == 2

        _run_migration(connection, "downgrade")

        columns = {column["name"] for column in sa.inspect(connection).get_columns("jobs")}
        assert "external_id" not in columns
        assert connection.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one() == 2
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO jobs (id, source, ingestion_method, source_url) "
                    "VALUES (3, 'trudvsem', 'manual', 'https://example.com/3')"
                )
            )


def test_downgrade_refuses_to_drop_discover_semantics() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_jobs(connection)
        _run_migration(connection, "upgrade")
        connection.execute(
            sa.text(
                "INSERT INTO jobs "
                "(id, source, ingestion_method, source_url, external_id, source_scope) "
                "VALUES (1, 'trudvsem', 'discover', 'https://trudvsem.ru/1', 'vac-1', 'company-1')"
            )
        )
        with pytest.raises(RuntimeError, match="Discover job data"):
            _run_migration(connection, "downgrade")
