from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


def create_runtime_engine(url: str) -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, connection_record) -> None:
            # sqlite3 requires this outside a transaction. Python 3.12+ may
            # disable autocommit; restore its original mode after the PRAGMA.
            has_autocommit = hasattr(dbapi_connection, "autocommit")
            if has_autocommit:
                previous = dbapi_connection.autocommit
                dbapi_connection.autocommit = True
            try:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                finally:
                    cursor.close()
            finally:
                if has_autocommit:
                    dbapi_connection.autocommit = previous
    return engine


engine = create_runtime_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
