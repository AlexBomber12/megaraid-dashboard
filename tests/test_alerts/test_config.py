from __future__ import annotations

import pytest
from pydantic import ValidationError

from megaraid_dashboard.config import Settings, get_settings
from tests.test_config import set_required_env


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_alert_runtime_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)

    settings = Settings()

    assert settings.alert_smtp_use_starttls is True
    assert settings.alert_severity_threshold == "critical"
    assert settings.alert_suppress_window_minutes == 60
    assert settings.alert_throttle_per_hour == 20


def test_alert_smtp_port_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("ALERT_SMTP_PORT", "0")

    with pytest.raises(ValidationError, match="alert_smtp_port"):
        Settings()


def test_alert_severity_threshold_must_be_in_allowed_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("ALERT_SEVERITY_THRESHOLD", "extreme")

    with pytest.raises(ValidationError, match="alert_severity_threshold"):
        Settings()


@pytest.mark.parametrize("value", ["info", "warning", "critical"])
def test_alert_severity_threshold_accepts_allowed_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("ALERT_SEVERITY_THRESHOLD", value)

    settings = Settings()
    assert settings.alert_severity_threshold == value


def test_alert_suppress_window_minutes_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("ALERT_SUPPRESS_WINDOW_MINUTES", "0")

    with pytest.raises(ValidationError, match="alert_suppress_window_minutes"):
        Settings()


def test_alert_throttle_per_hour_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("ALERT_THROTTLE_PER_HOUR", "0")

    with pytest.raises(ValidationError, match="alert_throttle_per_hour"):
        Settings()
