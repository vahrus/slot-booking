from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "booking.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    """Enable SQLite foreign key checks for a newly opened connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_sqlite_engine(database_url: str) -> Engine:
    """Create a synchronous SQLite engine with foreign keys enabled."""
    sqlite_engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )
    event.listen(sqlite_engine, "connect", _enable_sqlite_foreign_keys)
    return sqlite_engine


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
