from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
from httpx import Headers

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER

EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}


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
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _assert_security_headers(headers: Headers) -> None:
    for name, expected in EXPECTED_HEADERS.items():
        assert headers.get(name) == expected, (
            f"header {name!r} expected {expected!r}, got {headers.get(name)!r}"
        )


def test_healthz_includes_security_headers() -> None:
    test_app = create_app()

    with TestClient(test_app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    _assert_security_headers(response.headers)


def test_unauthorized_response_includes_security_headers() -> None:
    test_app = create_app()

    with TestClient(test_app) as client:
        response = client.get("/")

    assert response.status_code == 401
    _assert_security_headers(response.headers)


def test_authenticated_post_includes_security_headers(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()

    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        csrf = csrf_headers(client)
        token = csrf["X-CSRF-Token"]
        request_headers = {**csrf, "Cookie": f"__Host-csrf={token}"}
        response = client.post(
            "/maintenance/start",
            headers=request_headers,
            json={"duration_minutes": 30, "reason": "headers smoke test"},
        )

    assert response.status_code == 200
    _assert_security_headers(response.headers)


def test_error_response_includes_security_headers() -> None:
    test_app = create_app()

    boom_router = APIRouter()

    @boom_router.get("/_boom")
    def _boom() -> None:
        raise HTTPException(status_code=500, detail="intentional boom")

    test_app.include_router(boom_router)

    with TestClient(test_app) as client:
        response = client.get("/_boom", headers=TEST_AUTH_HEADER)

    assert response.status_code == 500
    _assert_security_headers(response.headers)


def test_unhandled_exception_response_includes_security_headers() -> None:
    # Unhandled exceptions (not HTTPException) are caught by Starlette's
    # ServerErrorMiddleware, which sits outside the user middleware stack.
    # SecurityHeadersMiddleware must wrap that layer too, otherwise the
    # synthetic 500 it produces has no security headers.
    test_app = create_app()

    crash_router = APIRouter()

    @crash_router.get("/_crash")
    def _crash() -> None:
        msg = "intentional crash"
        raise RuntimeError(msg)

    test_app.include_router(crash_router)

    with TestClient(test_app, raise_server_exceptions=False) as client:
        response = client.get("/_crash", headers=TEST_AUTH_HEADER)

    assert response.status_code == 500
    _assert_security_headers(response.headers)
