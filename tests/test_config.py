from pathlib import Path

import pytest
from pydantic import ValidationError

from megaraid_dashboard.config import Settings, get_database_url

REQUIRED_ENV = {
    "ALERT_SMTP_HOST": "smtp.example.test",
    "ALERT_SMTP_PORT": "587",
    "ALERT_SMTP_USER": "alert@example.test",
    "ALERT_SMTP_PASSWORD": "test-token",
    "ALERT_FROM": "alert@example.test",
    "ALERT_TO": "ops@example.test",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD_HASH": "test-bcrypt-hash",
    "STORCLI_PATH": "/usr/local/sbin/storcli64",
    "METRICS_INTERVAL_SECONDS": "300",
    "DATABASE_URL": "sqlite:///./megaraid.db",
    "LOG_LEVEL": "INFO",
}


def set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)


def missing_fields(error: ValidationError) -> set[str]:
    return {str(detail["loc"][0]) for detail in error.errors()}


def test_admin_credentials_are_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    set_required_env(monkeypatch)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert {"admin_username", "admin_password_hash"} <= missing_fields(exc_info.value)


def test_smtp_credentials_are_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    set_required_env(monkeypatch)
    monkeypatch.delenv("ALERT_SMTP_USER", raising=False)
    monkeypatch.delenv("ALERT_SMTP_PASSWORD", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert {"alert_smtp_user", "alert_smtp_password"} <= missing_fields(exc_info.value)


def test_database_url_loads_without_full_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///custom.db")

    assert get_database_url() == "sqlite:///custom.db"


def test_temperature_threshold_defaults_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)

    settings = Settings()

    assert settings.temp_warning_celsius == 55
    assert settings.temp_critical_celsius == 60
    assert settings.temp_hysteresis_celsius == 5
    assert settings.cv_capacitance_warning_percent == 70
    assert settings.collector_enabled is True
    assert settings.collector_lock_path == "/tmp/megaraid-dashboard-collector.lock"


def test_temperature_critical_must_exceed_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("TEMP_WARNING_CELSIUS", "60")
    monkeypatch.setenv("TEMP_CRITICAL_CELSIUS", "60")

    with pytest.raises(ValidationError, match="temp_critical_celsius"):
        Settings()


def test_temperature_hysteresis_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("TEMP_HYSTERESIS_CELSIUS", "0")

    with pytest.raises(ValidationError, match="temp_hysteresis_celsius"):
        Settings()


def test_metrics_interval_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "0")

    with pytest.raises(ValidationError, match="metrics_interval_seconds"):
        Settings()


def test_collector_lock_path_must_not_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", " ")

    with pytest.raises(ValidationError, match="collector_lock_path"):
        Settings()


@pytest.mark.parametrize(
    ("env_name", "error_match"),
    [
        ("METRICS_RAW_RETENTION_DAYS", "metrics_raw_retention_days"),
        ("METRICS_HOURLY_RETENTION_DAYS", "metrics_hourly_retention_days"),
    ],
)
def test_retention_windows_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    error_match: str,
) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv(env_name, "0")

    with pytest.raises(ValidationError, match=error_match):
        Settings()


@pytest.mark.parametrize("value", ["0", "101"])
def test_cv_capacitance_warning_percent_must_be_percent_range(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("CV_CAPACITANCE_WARNING_PERCENT", value)

    with pytest.raises(ValidationError, match="cv_capacitance_warning_percent"):
        Settings()
