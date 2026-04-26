from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from megaraid_dashboard.db import get_engine

PROJECT_ROOT = Path(__file__).parents[2]
EXPECTED_TABLES = {
    "alerts_sent",
    "audit_logs",
    "controller_snapshots",
    "cv_snapshots",
    "events",
    "pd_metrics_daily",
    "pd_metrics_hourly",
    "pd_snapshots",
    "pd_temp_states",
    "vd_snapshots",
}


def test_alembic_upgrade_downgrade_upgrade_is_idempotent() -> None:
    engine = get_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _alembic_config(connection)

            command.upgrade(config, "head")
            assert set(inspect(connection).get_table_names()) >= EXPECTED_TABLES

            command.downgrade(config, "base")
            assert EXPECTED_TABLES.isdisjoint(inspect(connection).get_table_names())

            command.upgrade(config, "head")
            assert set(inspect(connection).get_table_names()) >= EXPECTED_TABLES
    finally:
        engine.dispose()


def test_alembic_uses_database_url_without_full_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'alembic.db'}"
    for name in (
        "ALERT_SMTP_HOST",
        "ALERT_SMTP_PORT",
        "ALERT_SMTP_USER",
        "ALERT_SMTP_PASSWORD",
        "ALERT_FROM",
        "ALERT_TO",
        "ADMIN_USERNAME",
        "ADMIN_PASSWORD_HASH",
        "STORCLI_PATH",
        "METRICS_INTERVAL_SECONDS",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    command.upgrade(config, "head")

    engine = get_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
    finally:
        engine.dispose()


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
    config.attributes["connection"] = connection
    return config
