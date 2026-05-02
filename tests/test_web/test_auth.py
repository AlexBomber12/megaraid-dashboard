from __future__ import annotations

import base64

import bcrypt
import httpx
import pytest
from starlette.types import Receive, Scope, Send

from megaraid_dashboard.config import Settings
from megaraid_dashboard.web.auth import BasicAuthMiddleware, _is_whitelisted, _verify_credentials

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
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/healthz", True),
        ("/favicon.ico", True),
        ("/static/css/app.css", True),
        ("/static/", True),
        ("/health", False),
        ("/", False),
        ("/static", False),
    ],
)
def test_is_whitelisted(path: str, expected: bool) -> None:
    assert _is_whitelisted(path) is expected


@pytest.mark.parametrize(
    "header_value",
    [
        None,
        "Basic notbase64",
        "Bearer xxx",
        base64.b64encode(b"admin:test-password").decode("ascii"),
        "Basic admin:test-password",
    ],
)
def test_verify_credentials_rejects_missing_or_malformed_header(
    settings: Settings,
    header_value: str | None,
) -> None:
    assert _verify_credentials(header_value, settings) is False


def test_verify_credentials_rejects_wrong_username(settings: Settings) -> None:
    assert _verify_credentials(_basic_header("root", _TEST_PASSWORD), settings) is False


def test_verify_credentials_rejects_wrong_password(settings: Settings) -> None:
    assert _verify_credentials(_basic_header("admin", "wrong-password"), settings) is False


def test_verify_credentials_rejects_invalid_bcrypt_hash(settings: Settings) -> None:
    invalid_settings = settings.model_copy(update={"admin_password_hash": "not-a-bcrypt-hash"})

    assert _verify_credentials(_basic_header("admin", _TEST_PASSWORD), invalid_settings) is False


def test_verify_credentials_accepts_correct_credentials(settings: Settings) -> None:
    assert _verify_credentials(_basic_header("admin", _TEST_PASSWORD), settings) is True


async def test_middleware_returns_401_for_missing_header(settings: Settings) -> None:
    async with _auth_client(settings) as client:
        response = await client.get("/")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="megaraid-dashboard"'
    assert response.text == "Unauthorized"


@pytest.mark.parametrize(
    "header_value",
    ["Basic notbase64", "Bearer xxx", base64.b64encode(b"admin:test-password").decode("ascii")],
)
async def test_middleware_returns_401_for_malformed_header(
    settings: Settings,
    header_value: str,
) -> None:
    async with _auth_client(settings) as client:
        response = await client.get("/", headers={"Authorization": header_value})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="megaraid-dashboard"'


async def test_middleware_returns_401_for_wrong_username(settings: Settings) -> None:
    async with _auth_client(settings) as client:
        response = await client.get(
            "/",
            headers={"Authorization": _basic_header("root", "test-password")},
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="megaraid-dashboard"'


async def test_middleware_returns_401_for_wrong_password(settings: Settings) -> None:
    async with _auth_client(settings) as client:
        response = await client.get("/", headers={"Authorization": _basic_header("admin", "wrong")})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="megaraid-dashboard"'


async def test_middleware_allows_correct_credentials(settings: Settings) -> None:
    async with _auth_client(settings) as client:
        response = await client.get(
            "/",
            headers={"Authorization": _basic_header("admin", _TEST_PASSWORD)},
        )

    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.parametrize("path", ["/healthz", "/favicon.ico", "/static/css/app.css"])
async def test_middleware_bypasses_whitelisted_paths_without_header(
    settings: Settings,
    path: str,
) -> None:
    async with _auth_client(settings) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.parametrize("path", ["/healthz", "/favicon.ico", "/static/css/app.css"])
async def test_middleware_bypasses_whitelisted_paths_with_wrong_header(
    settings: Settings,
    path: str,
) -> None:
    async with _auth_client(settings) as client:
        response = await client.get(path, headers={"Authorization": _basic_header("root", "wrong")})

    assert response.status_code == 200
    assert response.content == b"ok"


def _basic_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _auth_client(settings: Settings) -> httpx.AsyncClient:
    app = BasicAuthMiddleware(_ok_app, settings=settings)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    del receive

    assert scope["type"] == "http"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"ok"})
