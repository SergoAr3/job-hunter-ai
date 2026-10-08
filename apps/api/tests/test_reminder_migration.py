import importlib.util
from pathlib import Path
from uuid import uuid4
from datetime import datetime

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def run(connection, direction):
    path = Path(__file__).parents[1] / "alembic/versions/20261006_19_application_reminders.py"
    spec = importlib.util.spec_from_file_location("reminder_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


def test_additive_preservation_constraints_cascade_and_defensive_downgrade():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE applications (id INTEGER PRIMARY KEY, note TEXT, next_action TEXT, next_action_due_on DATE)")
        connection.exec_driver_sql("INSERT INTO applications VALUES (1,'Keep','Call','2026-10-10'),(2,NULL,NULL,NULL)")
        before = connection.exec_driver_sql("SELECT * FROM applications ORDER BY id").all()
        run(connection, "upgrade")
        assert connection.exec_driver_sql("SELECT * FROM applications ORDER BY id").all() == before
        assert connection.exec_driver_sql("SELECT count(*) FROM application_reminders").scalar_one() == 0
        table = sa.Table("application_reminders", sa.MetaData(), autoload_with=connection)
        values = dict(application_id=1, remind_at=datetime(2026, 10, 10),
                      timezone="Asia/Yerevan", generation=uuid4().hex,
                      next_attempt_at=datetime(2026, 10, 10))
        connection.execute(table.insert().values(**values))
        with pytest.raises(RuntimeError, match="reminder data"):
            run(connection, "downgrade")
        assert connection.exec_driver_sql("SELECT count(*) FROM application_reminders").scalar_one() == 1
        for changes in ({"delivery_state": "claimed"}, {"attempt_count": 4}, {"delivery_state": "sent"}, {"last_error_code": "RAW_PROVIDER_BODY"}):
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(table.update().values(**changes))
        connection.exec_driver_sql("DELETE FROM applications WHERE id=1")
        assert connection.exec_driver_sql("SELECT count(*) FROM application_reminders").scalar_one() == 0
        run(connection, "downgrade")
        run(connection, "upgrade")
        assert connection.exec_driver_sql("SELECT count(*) FROM application_reminders").scalar_one() == 0
