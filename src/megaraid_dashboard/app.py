from __future__ import annotations

import errno
import fcntl
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import ArgumentError

from megaraid_dashboard import __version__
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db import get_engine, get_sessionmaker
from megaraid_dashboard.services import CollectorService, EventDetector

LOGGER = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><title>MegaRAID Dashboard</title></head>"
        "<body><h1>MegaRAID Dashboard</h1></body></html>"
    )


def create_app() -> FastAPI:
    app = FastAPI(title="MegaRAID Dashboard", lifespan=_lifespan)
    app.state.settings = get_settings()
    app.include_router(router)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    engine = get_engine(settings.database_url)
    with engine.begin() as connection:
        _upgrade_database(settings.database_url, connection=connection)
    session_factory = get_sessionmaker(engine)
    collector: CollectorService | None = None
    collector_lock_fd: int | None = None
    scheduler = None
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.collector = None
    app.state.collector_lock_fd = None
    app.state.scheduler = None

    try:
        if settings.collector_enabled:
            collector_lock_fd = _try_acquire_collector_lock(settings.collector_lock_path)
            app.state.collector_lock_fd = collector_lock_fd
            if collector_lock_fd is None:
                LOGGER.info(
                    "collector_scheduler_not_started",
                    reason="collector_lock_held",
                    lock_path=settings.collector_lock_path,
                )
            else:
                event_detector = EventDetector(
                    temp_warning=settings.temp_warning_celsius,
                    temp_critical=settings.temp_critical_celsius,
                    temp_hysteresis=settings.temp_hysteresis_celsius,
                    cv_capacitance_warning_percent=settings.cv_capacitance_warning_percent,
                )
                collector = CollectorService(
                    settings=settings,
                    session_factory=session_factory,
                    event_detector=event_detector,
                )
                scheduler = await collector.start()
                app.state.collector = collector
                app.state.scheduler = scheduler

        yield
    finally:
        try:
            if collector is not None and scheduler is not None:
                await collector.shutdown(scheduler)
        finally:
            if collector_lock_fd is not None:
                _release_collector_lock(collector_lock_fd)
            engine.dispose()


def _upgrade_database(database_url: str, *, connection: Connection | None = None) -> None:
    alembic_config = _alembic_config()
    alembic_config.set_main_option("sqlalchemy.url", _configparser_value(database_url))
    if connection is not None:
        alembic_config.attributes["connection"] = connection
    try:
        command.upgrade(alembic_config, "head")
    except Exception as exc:
        LOGGER.exception(
            "database_migration_failed",
            database_url=_redacted_database_url(database_url),
        )
        msg = "database migration failed"
        raise RuntimeError(msg) from exc


def _try_acquire_collector_lock(lock_path: str) -> int | None:
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            msg = f"collector lock path must not be a symlink: {lock_path}"
            raise RuntimeError(msg) from exc
        raise
    try:
        _validate_collector_lock_file(lock_fd, lock_path)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        return None
    except Exception:
        os.close(lock_fd)
        raise

    os.ftruncate(lock_fd, 0)
    os.write(lock_fd, str(os.getpid()).encode("ascii"))
    return lock_fd


def _validate_collector_lock_file(lock_fd: int, lock_path: str) -> None:
    lock_stat = os.fstat(lock_fd)
    if not stat.S_ISREG(lock_stat.st_mode):
        msg = f"collector lock path must be a regular file: {lock_path}"
        raise RuntimeError(msg)
    if lock_stat.st_uid != os.getuid():
        msg = f"collector lock path must be owned by the current user: {lock_path}"
        raise RuntimeError(msg)
    if lock_stat.st_nlink != 1:
        msg = f"collector lock path must not have hard links: {lock_path}"
        raise RuntimeError(msg)


def _release_collector_lock(lock_fd: int) -> None:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _alembic_config() -> Config:
    config_path, script_location = _alembic_paths()
    alembic_config = Config(str(config_path))
    alembic_config.set_main_option("script_location", str(script_location))
    return alembic_config


def _alembic_paths() -> tuple[Path, Path]:
    source_root = _project_root()
    source_config = source_root / "alembic.ini"
    source_migrations = source_root / "migrations"
    if source_config.exists() and source_migrations.exists():
        return source_config, source_migrations

    package_root = Path(__file__).resolve().parent
    return package_root / "alembic.ini", package_root / "migrations"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _redacted_database_url(database_url: str) -> str:
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except ArgumentError:
        return "<invalid database url>"


def _configparser_value(value: str) -> str:
    return value.replace("%", "%%")
