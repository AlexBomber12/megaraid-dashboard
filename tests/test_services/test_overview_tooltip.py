from __future__ import annotations

from collections.abc import Iterator

import pytest

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.services.overview import (
    RocTemperatureSection,
    _DriveSummary,
    _load_bbu_tile,
    _load_max_temp_tile,
    _load_roc_tile,
)


@pytest.fixture(autouse=True)
def overview_tooltip_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-bcrypt-hash")
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("TEMP_WARNING_CELSIUS", "55")
    monkeypatch.setenv("TEMP_CRITICAL_CELSIUS", "60")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_roc_tile_has_temperature_threshold_tooltip() -> None:
    tile = _load_roc_tile(
        RocTemperatureSection(
            value=78,
            status="optimal",
            label="78 C",
            warning_threshold=95,
            critical_threshold=105,
        )
    )

    assert tile.tooltip == "Current 78 C / Warning 95 C / Critical 105 C"


def test_max_temp_tile_has_temperature_threshold_tooltip() -> None:
    tile = _load_max_temp_tile(
        _DriveSummary(
            drive_count=8,
            max_temperature_celsius=58,
            elevated_drive_count=1,
            critical_drive_count=0,
            worst_state_severity="optimal",
            hottest_drive_url="/drives/252/2",
        ),
        settings=get_settings(),
    )

    assert tile.tooltip == "Current 58 C / Warning 55 C / Critical 60 C"


def test_bbu_tile_has_no_tooltip() -> None:
    tile = _load_bbu_tile(None)

    assert tile.tooltip is None
