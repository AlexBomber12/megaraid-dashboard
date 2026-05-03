from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

SQLITE_BUSY_TIMEOUT_MS = 5000


def get_engine(url: str, *, sqlite_busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS) -> Engine:
    engine_kwargs: dict[str, Any] = {}
    if _is_sqlite_url(url):
        engine_kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": sqlite_busy_timeout_ms / 1000,
        }
        if _is_sqlite_memory_url(url):
            engine_kwargs["poolclass"] = StaticPool

    engine = create_engine(url, future=True, echo=False, **engine_kwargs)

    if _is_sqlite_url(url):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                if not _is_sqlite_memory_url(url):
                    cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={sqlite_busy_timeout_ms}")
            finally:
                cursor.close()

    return engine


def get_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def _is_sqlite_url(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def _is_sqlite_memory_url(url: str) -> bool:
    parsed = make_url(url)
    return parsed.get_backend_name() == "sqlite" and parsed.database in (None, "", ":memory:")
