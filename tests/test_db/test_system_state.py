from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from megaraid_dashboard.db import get_engine
from megaraid_dashboard.db.dao import delete_state, get_state, set_state
from megaraid_dashboard.db.models import SystemState

PROJECT_ROOT = Path(__file__).parents[2]


def test_system_state_migration_round_trip() -> None:
    engine = get_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _alembic_config(connection)

            command.upgrade(config, "head")
            columns = {
                column["name"]: column for column in inspect(connection).get_columns("system_state")
            }
            assert set(columns) == {"key", "value", "created_at", "updated_at"}
            assert isinstance(columns["key"]["type"], sa.String)
            assert isinstance(columns["value"]["type"], sa.String)
            assert columns["value"]["nullable"] is False
            assert columns["updated_at"]["nullable"] is False

            command.downgrade(config, "0006_pd_disk_group")
            assert "system_state" not in inspect(connection).get_table_names()

            command.upgrade(config, "head")
            assert "system_state" in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_get_state_returns_none_for_unknown_key(session: Session) -> None:
    assert get_state(session, "missing") is None


def test_set_state_creates_and_updates_existing_row(session: Session) -> None:
    set_state(session, "sample", "first")
    session.commit()
    first = session.scalars(select(SystemState).where(SystemState.key == "sample")).one()
    first_updated_at = first.updated_at

    set_state(session, "sample", "second")
    session.commit()
    second = session.scalars(select(SystemState).where(SystemState.key == "sample")).one()

    assert second.value == "second"
    assert second.created_at == first.created_at
    assert second.updated_at >= first_updated_at
    assert len(session.scalars(select(SystemState)).all()) == 1


def test_delete_state_removes_row(session: Session) -> None:
    set_state(session, "sample", "value")
    session.commit()

    delete_state(session, "sample")
    session.commit()

    assert get_state(session, "sample") is None


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
    config.attributes["connection"] = connection
    return config
