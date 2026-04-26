from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy.engine import make_url
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
    _upgrade_database(settings.database_url)
    engine = get_engine(settings.database_url)
    session_factory = get_sessionmaker(engine)
    collector: CollectorService | None = None
    scheduler = None
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.scheduler = None

    if settings.collector_enabled:
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

    try:
        yield
    finally:
        if collector is not None and scheduler is not None:
            await collector.shutdown(scheduler)
        engine.dispose()


def _upgrade_database(database_url: str) -> None:
    alembic_config = _alembic_config()
    alembic_config.set_main_option("sqlalchemy.url", _configparser_value(database_url))
    try:
        command.upgrade(alembic_config, "head")
    except Exception as exc:
        LOGGER.exception(
            "database_migration_failed",
            database_url=_redacted_database_url(database_url),
        )
        msg = "database migration failed"
        raise RuntimeError(msg) from exc


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
