from __future__ import annotations

from pathlib import Path

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


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
    config.attributes["connection"] = connection
    return config
