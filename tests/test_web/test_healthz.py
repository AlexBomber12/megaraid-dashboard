from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from tests.conftest import TEST_ADMIN_PASSWORD_HASH


@pytest.fixture(autouse=True)
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
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
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_healthz_returns_ok_for_healthy_database_and_running_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    test_app = create_app()

    with TestClient(test_app) as client:
        test_app.state.collector = object()
        test_app.state.collector_lock_fd = 1

        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "collector": "ok"}


def test_healthz_returns_degraded_when_database_check_fails() -> None:
    test_app = create_app()

    with TestClient(test_app) as client:
        test_app.state.health_engine = _BrokenEngine()

        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "error", "collector": "idle"}


def test_healthz_returns_degraded_when_database_check_is_already_running() -> None:
    test_app = create_app()

    with TestClient(test_app) as client:
        test_app.state.health_probe_lock = _LockedProbe()

        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "error", "collector": "idle"}


def test_healthz_reports_disabled_collector_as_idle() -> None:
    test_app = create_app()

    with TestClient(test_app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "collector": "idle"}


def test_healthz_reports_collector_lock_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    test_app = create_app()

    with TestClient(test_app) as client:
        test_app.state.collector = None
        test_app.state.collector_lock_fd = None
        test_app.state.collector_retry_task = _RetryTask()

        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "collector": "lock_held"}


def test_healthz_sets_no_store_cache_header() -> None:
    test_app = create_app()

    with TestClient(test_app) as client:
        response = client.get("/healthz")

    assert response.headers["Cache-Control"] == "no-store"


def test_healthz_is_callable_without_authorization_header() -> None:
    test_app = create_app()

    with TestClient(test_app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200


class _BrokenEngine:
    def connect(self) -> None:
        raise RuntimeError("database unavailable")


class _LockedProbe:
    def locked(self) -> bool:
        return True


class _RetryTask:
    def done(self) -> bool:
        return False
