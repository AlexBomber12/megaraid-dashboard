from pathlib import Path

import pytest
from pydantic import ValidationError

from megaraid_dashboard.config import Settings

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
