from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect

from megaraid_dashboard import app
from megaraid_dashboard.db import get_engine


def test_alembic_paths_use_source_checkout_when_available() -> None:
    config_path, script_location = app._alembic_paths()

    assert config_path.name == "alembic.ini"
    assert config_path.exists()
    assert script_location.name == "migrations"
    assert script_location.exists()


def test_alembic_paths_fall_back_to_packaged_files(monkeypatch: pytest.MonkeyPatch) -> None:
    missing_root = Path("/tmp/megaraid-dashboard-missing-root")
    monkeypatch.setattr(app, "_project_root", lambda: missing_root)

    config_path, script_location = app._alembic_paths()

    package_root = Path(app.__file__).resolve().parent
    assert config_path == package_root / "alembic.ini"
    assert script_location == package_root / "migrations"


def test_redacted_database_url_hides_password() -> None:
    redacted_url = app._redacted_database_url("postgresql://user:secret@example.test/db")

    assert "secret" not in redacted_url
    assert redacted_url == "postgresql://user:***@example.test/db"


def test_configparser_value_escapes_percent_for_alembic() -> None:
    database_url = "postgresql://user:p%40ss@example.test/db"
    config = Config()

    config.set_main_option("sqlalchemy.url", app._configparser_value(database_url))

    assert config.get_main_option("sqlalchemy.url") == database_url


def test_upgrade_database_uses_existing_in_memory_connection() -> None:
    engine = get_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            app._upgrade_database("sqlite:///:memory:", connection=connection)

        assert "controller_snapshots" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
