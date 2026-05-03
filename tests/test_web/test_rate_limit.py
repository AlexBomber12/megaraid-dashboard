from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from pathlib import Path

import bcrypt
import httpx
import pytest
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import Settings, get_settings
from megaraid_dashboard.web.auth import BasicAuthMiddleware
from megaraid_dashboard.web.csrf import CsrfMiddleware
from megaraid_dashboard.web.middleware import ForwardedPrefixMiddleware
from megaraid_dashboard.web.rate_limit import AuthRateLimitMiddleware
from tests.conftest import TEST_ADMIN_PASSWORD_HASH

_TEST_PASSWORD = "test-password"
_TEST_HASH = bcrypt.hashpw(_TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        alert_smtp_host="smtp.example.test",
        alert_smtp_port=587,
        alert_smtp_user="alert@example.test",
        alert_smtp_password="test-token",
        alert_from="alert@example.test",
        alert_to="ops@example.test",
        admin_username="admin",
        admin_password_hash=_TEST_HASH,
        storcli_path="/usr/local/sbin/storcli64",
        metrics_interval_seconds=300,
        collector_enabled=False,
        database_url="sqlite:///:memory:",
        log_level="INFO",
        auth_rate_limit_per_minute=5,
        auth_rate_limit_burst=0,
    )


async def test_failed_attempts_over_limit_return_429(settings: Settings) -> None:
    async with _rate_limited_client(settings=settings) as client:
        responses = [
            await client.get("/", headers={"Authorization": _basic_header("admin", "wrong")})
            for _ in range(5)
        ]
        limited_response = await client.get(
            "/",
            headers={"Authorization": _basic_header("admin", "wrong")},
        )

    assert [response.status_code for response in responses] == [401] * 5
    assert limited_response.status_code == 429
    assert limited_response.headers["Retry-After"] == "60"
    assert limited_response.json() == {"error": "rate_limit_exceeded"}


async def test_in_flight_attempt_reserves_rate_limit_slot(settings: Settings) -> None:
    settings = settings.model_copy(update={"auth_rate_limit_per_minute": 1})
    inner_app = _SlowUnauthorizedApp()
    app = AuthRateLimitMiddleware(inner_app, settings=settings)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 12345))

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_task = asyncio.create_task(client.get("/"))
        await inner_app.entered.wait()

        second = await client.get("/")
        inner_app.release.set()
        first = await first_task

    assert first.status_code == 401
    assert second.status_code == 429
    assert inner_app.started == 1


async def test_window_expiry_allows_new_attempt(settings: Settings) -> None:
    clock = _Clock()
    async with _rate_limited_client(settings=settings, time_func=clock.monotonic) as client:
        for _ in range(5):
            response = await client.get(
                "/",
                headers={"Authorization": _basic_header("admin", "wrong")},
            )
            assert response.status_code == 401

        assert (
            await client.get("/", headers={"Authorization": _basic_header("admin", "wrong")})
        ).status_code == 429

        clock.advance(60.1)
        response = await client.get("/", headers={"Authorization": _basic_header("admin", "wrong")})

    assert response.status_code == 401


async def test_successful_response_does_not_reset_failed_attempt_bucket(settings: Settings) -> None:
    async with _rate_limited_client(settings=settings) as client:
        for _ in range(3):
            assert (
                await client.get("/", headers={"Authorization": _basic_header("admin", "wrong")})
            ).status_code == 401

        success = await client.get(
            "/", headers={"Authorization": _basic_header("admin", _TEST_PASSWORD)}
        )

        for _ in range(2):
            assert (
                await client.get("/", headers={"Authorization": _basic_header("admin", "wrong")})
            ).status_code == 401

        limited_response = await client.get(
            "/",
            headers={"Authorization": _basic_header("admin", "wrong")},
        )

    assert success.status_code == 200
    assert limited_response.status_code == 429


async def test_whitelisted_path_is_not_rate_limited(settings: Settings) -> None:
    async with _rate_limited_client(settings=settings) as client:
        responses = [await client.get("/healthz") for _ in range(10)]

    assert [response.status_code for response in responses] == [200] * 10


async def test_limiter_uses_last_x_forwarded_for_value(settings: Settings) -> None:
    settings = settings.model_copy(update={"auth_rate_limit_per_minute": 1})
    async with _rate_limited_client(settings=settings) as client:
        first = await client.get(
            "/",
            headers={
                "Authorization": _basic_header("admin", "wrong"),
                "X-Forwarded-For": "198.51.100.10, 203.0.113.20",
            },
        )
        second_same_proxy = await client.get(
            "/",
            headers={
                "Authorization": _basic_header("admin", "wrong"),
                "X-Forwarded-For": "192.0.2.55, 203.0.113.20",
            },
        )
        different_proxy = await client.get(
            "/",
            headers={
                "Authorization": _basic_header("admin", "wrong"),
                "X-Forwarded-For": "198.51.100.10, 203.0.113.21",
            },
        )

    assert first.status_code == 401
    assert second_same_proxy.status_code == 429
    assert different_proxy.status_code == 401


async def test_limiter_uses_client_host_without_x_forwarded_for(settings: Settings) -> None:
    settings = settings.model_copy(update={"auth_rate_limit_per_minute": 1})
    async with _rate_limited_client(settings=settings) as client:
        first = await client.get("/", headers={"Authorization": _basic_header("admin", "wrong")})
        second = await client.get("/", headers={"Authorization": _basic_header("admin", "wrong")})

    assert first.status_code == 401
    assert second.status_code == 429


def test_create_app_middleware_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()

    try:
        test_app = create_app()
    finally:
        get_settings.cache_clear()

    middleware_classes = [middleware.cls for middleware in test_app.user_middleware]
    assert middleware_classes[:4] == [
        AuthRateLimitMiddleware,
        BasicAuthMiddleware,
        CsrfMiddleware,
        ForwardedPrefixMiddleware,
    ]


def _basic_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _rate_limited_client(
    *,
    settings: Settings,
    time_func: Callable[[], float] | None = None,
) -> httpx.AsyncClient:
    inner_app = BasicAuthMiddleware(_ok_app, settings=settings)
    if time_func is None:
        app = AuthRateLimitMiddleware(inner_app, settings=settings)
    else:
        app = AuthRateLimitMiddleware(inner_app, settings=settings, time_func=time_func)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 12345))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    assert scope["type"] == "http"
    response = PlainTextResponse("ok")
    await response(scope, receive, send)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _SlowUnauthorizedApp:
    def __init__(self) -> None:
        self.started = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        assert scope["type"] == "http"
        self.started += 1
        self.entered.set()
        await self.release.wait()
        response = PlainTextResponse("Unauthorized", status_code=401)
        await response(scope, receive, send)
