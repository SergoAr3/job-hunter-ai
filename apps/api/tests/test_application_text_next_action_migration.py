import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def run(connection, direction):
    path = Path(__file__).parents[1] / "alembic/versions/20261005_18_application_text_next_action.py"
    spec = importlib.util.spec_from_file_location("text_action_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


def legacy(connection):
    connection.exec_driver_sql("CREATE TABLE applications (id INTEGER PRIMARY KEY, note TEXT, next_action TEXT, next_action_due_on DATE, CONSTRAINT ck_applications_next_action_block CHECK ((next_action IS NULL AND next_action_due_on IS NULL) OR (next_action IS NOT NULL AND next_action_due_on IS NOT NULL)))")
    connection.exec_driver_sql("INSERT INTO applications VALUES (1, NULL, NULL, NULL), (2, 'Private', 'Call', '2026-10-10')")


def test_upgrade_downgrade_upgrade_preserves_legacy_and_new_constraint():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        legacy(connection)
        before = connection.exec_driver_sql("SELECT * FROM applications ORDER BY id").all()
        run(connection, "upgrade")
        assert connection.exec_driver_sql("SELECT * FROM applications ORDER BY id").all() == before
        connection.exec_driver_sql("INSERT INTO applications VALUES (3, NULL, 'Undated', NULL)")
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO applications VALUES (4, NULL, NULL, '2026-10-10')")
        with pytest.raises(RuntimeError, match="undated next actions"):
            run(connection, "downgrade")
        assert connection.exec_driver_sql("SELECT next_action FROM applications WHERE id=3").scalar_one() == "Undated"
        connection.exec_driver_sql("DELETE FROM applications WHERE id=3")
        run(connection, "downgrade")
        assert connection.exec_driver_sql("SELECT * FROM applications ORDER BY id").all() == before
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO applications VALUES (3, NULL, 'Undated', NULL)")
        run(connection, "upgrade")
        connection.exec_driver_sql("INSERT INTO applications VALUES (3, NULL, 'Undated', NULL)")
