from collections.abc import Generator
from sqlite3 import Connection as SQLiteConnection
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from llc_api.core.config import get_settings


def make_engine(database_url: str | None = None, **kwargs: Any) -> Engine:
    url = database_url or get_settings().database_url
    enable_foreign_keys = kwargs.pop("enable_foreign_keys", True)
    connect_args = kwargs.pop(
        "connect_args",
        {"check_same_thread": False, "timeout": 5} if url.startswith("sqlite") else {},
    )
    created_engine = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        **kwargs,
    )
    if make_url(url).get_backend_name() == "sqlite":

        @event.listens_for(created_engine, "connect")
        def configure_sqlite(dbapi_connection: SQLiteConnection, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            if enable_foreign_keys:
                cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return created_engine


settings = get_settings()
settings.ensure_data_directory()
engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
