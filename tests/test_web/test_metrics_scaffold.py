from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client.parser import text_string_to_metric_families
from pydantic import ValidationError

from megaraid_dashboard import app
from megaraid_dashboard.config import Settings, get_settings
from megaraid_dashboard.web.metrics import create_metrics_app
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", TEST_ADMIN_PASSWORD_HASH)
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")


def test_metrics_app_serves_prometheus_text() -> None:
    metrics_app = create_metrics_app()

    with TestClient(metrics_app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    families = list(text_string_to_metric_families(response.text))
    assert any(family.name == "megaraid_exporter_up" for family in families)


def test_metrics_app_reports_exporter_up() -> None:
    metrics_app = create_metrics_app()

    with TestClient(metrics_app) as client:
        response = client.get("/metrics")

    assert "megaraid_exporter_up 1.0" in response.text


def test_metrics_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)

    settings = Settings()

    assert settings.metrics_port == 8091
    assert settings.metrics_listen_address == "127.0.0.1"
    assert settings.metrics_enabled is True


def test_metrics_port_must_be_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("METRICS_PORT", "65536")

    with pytest.raises(ValidationError, match=r"metrics_port must be 1\.\.65535"):
        Settings()


def test_lifespan_skips_metrics_server_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    get_settings.cache_clear()

    test_app = app.create_app()

    try:
        with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
            response = client.get("/health")

        assert response.status_code == 200
        assert test_app.state.metrics_server is None
        assert test_app.state.metrics_task is None
    finally:
        get_settings.cache_clear()
