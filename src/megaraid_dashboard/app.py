from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import stat
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from fastapi import FastAPI
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.config import Settings, get_settings
from megaraid_dashboard.db import get_engine, get_sessionmaker
from megaraid_dashboard.services import CollectorService, EventDetector
from megaraid_dashboard.web.auth import BasicAuthMiddleware
from megaraid_dashboard.web.middleware import ForwardedPrefixMiddleware
from megaraid_dashboard.web.routes import router
from megaraid_dashboard.web.static import CacheControlStaticFiles

LOGGER = structlog.get_logger(__name__)
_COLLECTOR_LOCK_RETRY_SECONDS = 30.0
_HEALTH_CHECK_SQLITE_BUSY_TIMEOUT_MS = 250
_PACKAGE_ROOT = Path(__file__).resolve().parent
_STATIC_DIR = _PACKAGE_ROOT / "static"


@dataclass
class _CollectorRuntime:
    collector: CollectorService | None = None
    scheduler: AsyncIOScheduler | None = None
    lock_fd: int | None = None
    retry_task: asyncio.Task[None] | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="MegaRAID Dashboard", lifespan=_lifespan)
    app.state.settings = get_settings()
    app.add_middleware(ForwardedPrefixMiddleware)
    app.add_middleware(BasicAuthMiddleware, settings=app.state.settings)
    app.mount(
        "/static",
        CacheControlStaticFiles(directory=_STATIC_DIR),
        name="static",
    )
    app.include_router(router)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = get_engine(settings.database_url)
    health_engine = get_engine(
        settings.database_url,
        sqlite_busy_timeout_ms=_HEALTH_CHECK_SQLITE_BUSY_TIMEOUT_MS,
    )
    health_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="healthz-db")
    with engine.begin() as connection:
        _upgrade_database(settings.database_url, connection=connection)
    session_factory = get_sessionmaker(engine)
    collector_runtime = _CollectorRuntime()
    app.state.engine = engine
    app.state.health_engine = health_engine
    app.state.health_executor = health_executor
    app.state.health_probe_lock = asyncio.Lock()
    app.state.session_factory = session_factory
    app.state.collector = None
    app.state.collector_lock_fd = None
    app.state.collector_retry_task = None
    app.state.scheduler = None

    try:
        if settings.collector_enabled:
            collector_started = await _start_collector_scheduler(
                app=app,
                settings=settings,
                session_factory=session_factory,
                runtime=collector_runtime,
            )
            if not collector_started:
                LOGGER.info(
                    "collector_scheduler_not_started",
                    reason="collector_lock_held",
                    lock_path=settings.collector_lock_path,
                )
                collector_runtime.retry_task = asyncio.create_task(
                    _retry_collector_scheduler_start(
                        app=app,
                        settings=settings,
                        session_factory=session_factory,
                        runtime=collector_runtime,
                    )
                )
                app.state.collector_retry_task = collector_runtime.retry_task

        yield
    finally:
        if collector_runtime.retry_task is not None:
            collector_runtime.retry_task.cancel()
            with suppress(asyncio.CancelledError):
                await collector_runtime.retry_task
        try:
            if collector_runtime.collector is not None and collector_runtime.scheduler is not None:
                await collector_runtime.collector.shutdown(collector_runtime.scheduler)
        finally:
            if collector_runtime.lock_fd is not None:
                _release_collector_lock(collector_runtime.lock_fd)
            health_executor.shutdown(wait=False, cancel_futures=True)
            health_engine.dispose()
            engine.dispose()


async def _start_collector_scheduler(
    *,
    app: FastAPI,
    settings: Settings,
    session_factory: sessionmaker[Session],
    runtime: _CollectorRuntime,
) -> bool:
    if runtime.collector is not None:
        return True

    collector_lock_fd = _try_acquire_collector_lock(settings.collector_lock_path)
    app.state.collector_lock_fd = collector_lock_fd
    if collector_lock_fd is None:
        return False

    try:
        event_detector = EventDetector(
            temp_warning=settings.temp_warning_celsius,
            temp_critical=settings.temp_critical_celsius,
            temp_hysteresis=settings.temp_hysteresis_celsius,
            roc_temp_warning=settings.roc_temp_warning_celsius,
            roc_temp_critical=settings.roc_temp_critical_celsius,
            roc_temp_hysteresis=settings.roc_temp_hysteresis_celsius,
            cv_capacitance_warning_percent=settings.cv_capacitance_warning_percent,
        )
        collector = CollectorService(
            settings=settings,
            session_factory=session_factory,
            event_detector=event_detector,
        )
        scheduler = await collector.start()
    except asyncio.CancelledError:
        _release_collector_lock(collector_lock_fd)
        app.state.collector_lock_fd = None
        raise
    except Exception:
        _release_collector_lock(collector_lock_fd)
        app.state.collector_lock_fd = None
        raise

    runtime.collector = collector
    runtime.scheduler = scheduler
    runtime.lock_fd = collector_lock_fd
    app.state.collector = collector
    app.state.scheduler = scheduler
    return True


async def _retry_collector_scheduler_start(
    *,
    app: FastAPI,
    settings: Settings,
    session_factory: sessionmaker[Session],
    runtime: _CollectorRuntime,
) -> None:
    while runtime.collector is None:
        await asyncio.sleep(_COLLECTOR_LOCK_RETRY_SECONDS)
        try:
            if await _start_collector_scheduler(
                app=app,
                settings=settings,
                session_factory=session_factory,
                runtime=runtime,
            ):
                LOGGER.info(
                    "collector_scheduler_started_after_retry",
                    lock_path=settings.collector_lock_path,
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "collector_scheduler_retry_failed",
                lock_path=settings.collector_lock_path,
            )


def _upgrade_database(database_url: str, *, connection: Connection | None = None) -> None:
    alembic_config = _alembic_config()
    alembic_config.set_main_option("sqlalchemy.url", _configparser_value(database_url))
    if connection is not None:
        alembic_config.attributes["connection"] = connection
    try:
        current_heads = _current_database_heads(connection)
        target_heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
        command.upgrade(alembic_config, "head")
    except Exception as exc:
        LOGGER.exception(
            "database_migration_failed",
            database_url=_redacted_database_url(database_url),
        )
        msg = "database migration failed"
        raise RuntimeError(msg) from exc
    if current_heads is not None and current_heads == target_heads:
        LOGGER.debug("database_at_head_revision", revision=",".join(sorted(target_heads)))
    elif current_heads is not None:
        LOGGER.info(
            "database_migration_applied",
            from_revision=",".join(sorted(current_heads)) or None,
            to_revision=",".join(sorted(target_heads)) or None,
        )


def _current_database_heads(connection: Connection | None) -> set[str] | None:
    if connection is None:
        return None
    context = MigrationContext.configure(connection)
    return set(context.get_current_heads())


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
