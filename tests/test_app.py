from __future__ import annotations

from pathlib import Path

import pytest

from megaraid_dashboard import app


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
