from __future__ import annotations

import re
from http.cookies import SimpleCookie
from pathlib import Path

import httpx
import pytest
from starlette.types import Receive, Scope, Send

from megaraid_dashboard.web.csrf import CsrfMiddleware, _generate_token

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


async def test_get_issues_csrf_cookie() -> None:
    async with _csrf_client() as client:
        response = await client.get("/")

    cookie = _csrf_cookie(response)
    assert cookie.value
    assert _TOKEN_RE.fullmatch(cookie.value) is not None
    assert cookie["secure"] is True
    assert cookie["httponly"] == ""
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == "/"
    assert cookie["domain"] == ""


async def test_second_get_with_cookie_does_not_reissue() -> None:
    async with _csrf_client() as client:
        first_response = await client.get("/")
        cookie = _csrf_cookie(first_response)

        second_response = await client.get("/", headers={"Cookie": f"__Host-csrf={cookie.value}"})

    assert "set-cookie" not in second_response.headers


async def test_post_without_cookie_returns_403() -> None:
    async with _csrf_client() as client:
        response = await client.post("/any")

    assert response.status_code == 403
    assert "csrf" in response.text


async def test_post_with_cookie_but_no_header_returns_403() -> None:
    async with _csrf_client() as client:
        response = await client.post("/any", headers={"Cookie": "__Host-csrf=abc"})

    assert response.status_code == 403
    assert "csrf" in response.text


async def test_post_with_mismatched_cookie_and_header_returns_403() -> None:
    async with _csrf_client() as client:
        response = await client.post(
            "/any",
            headers={"Cookie": "__Host-csrf=abc", "X-CSRF-Token": "def"},
        )

    assert response.status_code == 403
    assert "csrf" in response.text


async def test_post_with_matching_cookie_and_header_passes() -> None:
    async with _csrf_client() as client:
        response = await client.post(
            "/any",
            headers={"Cookie": "__Host-csrf=abc", "X-CSRF-Token": "abc"},
        )

    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.parametrize("path", ["/healthz", "/static/x", "/favicon.ico"])
async def test_post_to_whitelisted_paths_bypasses_csrf(path: str) -> None:
    async with _csrf_client() as client:
        response = await client.post(path)

    assert response.status_code == 200
    assert response.content == b"ok"


async def test_get_response_does_not_advertise_cookie_when_request_already_has_one() -> None:
    async with _csrf_client() as client:
        response = await client.get("/", headers={"Cookie": "__Host-csrf=abc"})

    assert "set-cookie" not in response.headers


def test_generate_token_returns_expected_urlsafe_base64_length() -> None:
    token = _generate_token()

    assert _TOKEN_RE.fullmatch(token) is not None


def test_generate_token_produces_distinct_values() -> None:
    tokens = {_generate_token() for _ in range(1000)}

    assert len(tokens) == 1000


def test_htmx_shim_injects_csrf_header() -> None:
    script = Path("src/megaraid_dashboard/static/js/csrf.js").read_text(encoding="utf-8")

    assert "__Host-csrf" in script
    assert "htmx:configRequest" in script
    assert 'evt.detail.headers["X-CSRF-Token"] = token;' in script


def _csrf_client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=CsrfMiddleware(_ok_app))
    return httpx.AsyncClient(transport=transport, base_url="https://testserver")


def _csrf_cookie(response: httpx.Response) -> SimpleCookie[str]:
    cookie = SimpleCookie[str]()
    cookie.load(response.headers["set-cookie"])
    csrf_cookie = cookie["__Host-csrf"]
    return csrf_cookie


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
