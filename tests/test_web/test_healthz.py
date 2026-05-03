from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.web.routes import _database_health_for_request
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


async def test_database_health_waits_when_probe_is_already_running() -> None:
    probe_lock = asyncio.Lock()
    await probe_lock.acquire()
    executor = ThreadPoolExecutor(max_workers=1)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                health_probe_lock=probe_lock,
                health_engine=_HealthyEngine(),
                health_executor=executor,
            )
        )
    )

    try:
        database_health = asyncio.create_task(_database_health_for_request(request))
        await asyncio.sleep(0)

        assert not database_health.done()

        probe_lock.release()
        assert await asyncio.wait_for(database_health, timeout=1.0) == "ok"
    finally:
        if probe_lock.locked():
            probe_lock.release()
        executor.shutdown(wait=True)


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


class _HealthyEngine:
    def connect(self) -> _HealthyConnection:
        return _HealthyConnection()


class _HealthyConnection:
    def __enter__(self) -> _HealthyConnection:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def execution_options(self, *, isolation_level: str) -> _HealthyConnection:
        return self

    def execute(self, statement: object) -> None:
        return None


class _RetryTask:
    def done(self) -> bool:
        return False
