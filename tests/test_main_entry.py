from __future__ import annotations

import runpy
from unittest.mock import MagicMock, patch

import pytest

from megaraid_dashboard import __main__ as dashboard_main
from megaraid_dashboard.config import Settings, get_settings
from tests.test_config import set_required_env


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Settings:
    set_required_env(monkeypatch)
    get_settings.cache_clear()
    yield Settings()
    get_settings.cache_clear()


def test_main_invokes_uvicorn_run() -> None:
    fake_app = MagicMock()
    with (
        patch.object(dashboard_main, "create_app", return_value=fake_app) as mock_create,
        patch.object(dashboard_main.uvicorn, "run") as mock_run,
    ):
        dashboard_main.main()

    mock_create.assert_called_once_with()
    mock_run.assert_called_once_with(fake_app, host="127.0.0.1", port=8090)


def test_running_dashboard_as_module_invokes_uvicorn() -> None:
    fake_app = MagicMock()
    with (
        patch("megaraid_dashboard.app.create_app", return_value=fake_app) as mock_create,
        patch("uvicorn.run") as mock_run,
    ):
        runpy.run_module("megaraid_dashboard", run_name="__main__")

    mock_create.assert_called_once_with()
    mock_run.assert_called_once_with(fake_app, host="127.0.0.1", port=8090)
