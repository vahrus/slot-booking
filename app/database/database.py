from collections.abc import Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "booking.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
BUSY_TIMEOUT_MS = 5000
_LOCK_ERROR_CODES = {5, 6, 517}  # SQLITE_BUSY, SQLITE_LOCKED, SQLITE_BUSY_SNAPSHOT
_BEGIN_MODE: ContextVar[str] = ContextVar("slotly_sqlite_begin", default="DEFERRED")


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""


def _configure_sqlite_connection(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    """Apply per-connection SQLite settings before SQLAlchemy starts a transaction.

    ``journal_mode=WAL`` is stored in the database header the first time a
    connection opens that file. It does not rewrite application rows.
    ``busy_timeout`` and ``foreign_keys`` apply only to this connection.
    Disabling the driver transaction control lets SQLAlchemy emit BEGIN itself.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.fetchone()
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.close()
    dbapi_connection.isolation_level = None


def _begin_sqlite_transaction(connection: Any) -> None:
    """Emit the transaction mode selected by the current write or read."""
    if _BEGIN_MODE.get() == "IMMEDIATE":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        return
    connection.exec_driver_sql("BEGIN")


def create_sqlite_engine(database_url: str) -> Engine:
    """Create a synchronous SQLite engine with foreign keys, WAL, and a lock wait."""
    sqlite_engine = create_engine(
        database_url,
        connect_args={
            "check_same_thread": False,
            "timeout": BUSY_TIMEOUT_MS / 1000,
        },
    )
    event.listen(sqlite_engine, "connect", _configure_sqlite_connection)
    event.listen(sqlite_engine, "begin", _begin_sqlite_transaction)
    return sqlite_engine


def is_sqlite_lock_error(error: OperationalError) -> bool:
    """Return whether SQLite rejected the statement because the file is busy."""
    original = getattr(error, "orig", None)
    code = getattr(original, "sqlite_errorcode", None)
    if code in _LOCK_ERROR_CODES:
        return True
    message = str(original or error).lower()
    return "database is locked" in message or "database table is locked" in message


@contextmanager
def immediate_write(session: Session) -> Iterator[None]:
    """Run one read-modify-write under ``BEGIN IMMEDIATE``.

    SQLite's deferred transaction takes the reserved lock only at the first
    write, so two bookings can both observe a free slot. ``BEGIN IMMEDIATE``
    takes that lock before the availability check. The session must not already
    contain pending changes; an open read transaction is rolled back first.
    """
    if session.new or session.dirty or session.deleted:
        raise RuntimeError(
            "An immediate SQLite write needs a session without pending changes."
        )
    if session.in_transaction():
        session.rollback()

    token = _BEGIN_MODE.set("IMMEDIATE")
    try:
        session.connection()
        yield
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        _BEGIN_MODE.reset(token)


DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_sqlite_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Provide a database session and always close it afterwards."""
    with SessionLocal() as session:
        yield session


def init_db() -> None:
    """Create all application tables that do not exist yet."""
    from app.database import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
