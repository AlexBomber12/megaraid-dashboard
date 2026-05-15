from __future__ import annotations

import runpy
import sys
from unittest.mock import MagicMock, patch

import pytest

from megaraid_dashboard.config import Settings, get_settings
from tests.test_config import set_required_env


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Settings:
    set_required_env(monkeypatch)
    get_settings.cache_clear()
    yield Settings()
    get_settings.cache_clear()


def test_running_alerts_as_module_executes_main(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = MagicMock()
    monkeypatch.setattr(sys, "argv", ["python -m megaraid_dashboard.alerts", "test"])
    with (
        patch("megaraid_dashboard.alerts.build_default_transport", return_value=transport),
        pytest.raises(SystemExit) as exc_info,
    ):
        runpy.run_module("megaraid_dashboard.alerts", run_name="__main__")
    assert exc_info.value.code == 0
    transport.send.assert_called_once()
