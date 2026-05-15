from __future__ import annotations

import secrets
from http.cookies import CookieError, SimpleCookie

import httpx
import pytest
from starlette.datastructures import Headers
from starlette.types import Receive, Scope, Send

from megaraid_dashboard.web import csrf as csrf_module
from megaraid_dashboard.web.csrf import (
    CsrfMiddleware,
    _extract_cookie,
    _generate_token,
    _has_csrf_set_cookie,
)


def test_generate_token_raises_when_secrets_token_bytes_is_too_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets, "token_bytes", lambda _n: b"\x00")

    with pytest.raises(RuntimeError, match="unexpected csrf token length"):
        _generate_token()


def test_extract_cookie_returns_none_when_simplecookie_load_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(self: SimpleCookie[str], _rawdata: str) -> None:
        raise CookieError("synthetic load failure")

    monkeypatch.setattr(SimpleCookie, "load", _raise)
    headers = Headers({"cookie": "anything=value"})

    assert _extract_cookie(headers, "__Host-csrf") is None


def test_extract_cookie_returns_none_when_cookie_header_lacks_target_name() -> None:
    headers = Headers({"cookie": "other-cookie=value; another=here"})

    assert _extract_cookie(headers, "__Host-csrf") is None


def test_has_csrf_set_cookie_returns_true_when_downstream_already_set_csrf() -> None:
    message = {
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"set-cookie", b"__Host-csrf=existing; Path=/; SameSite=Strict; Secure"),
        ],
    }

    assert _has_csrf_set_cookie(message) is True


def test_has_csrf_set_cookie_returns_false_for_non_csrf_set_cookie() -> None:
    message = {
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"set-cookie", b"session=abc; Path=/"),
        ],
    }

    assert _has_csrf_set_cookie(message) is False


def test_has_csrf_set_cookie_skips_set_cookie_values_that_fail_to_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(self: SimpleCookie[str], _rawdata: str) -> None:
        raise CookieError("synthetic load failure")

    monkeypatch.setattr(SimpleCookie, "load", _raise)
    message = {
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"set-cookie", b"session=abc; Path=/"),
        ],
    }

    assert _has_csrf_set_cookie(message) is False


async def test_middleware_skips_reissue_when_downstream_already_set_csrf_cookie() -> None:
    async def _app_sets_csrf(scope: Scope, receive: Receive, send: Send) -> None:
        del receive
        assert scope["type"] == "http"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (
                        b"set-cookie",
                        b"__Host-csrf=downstream; Path=/; SameSite=Strict; Secure",
                    ),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    transport = httpx.ASGITransport(app=CsrfMiddleware(_app_sets_csrf))
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.get("/")

    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert "__Host-csrf=downstream" in cookies[0]


def test_csrf_module_exposes_helpers() -> None:
    assert csrf_module._extract_cookie is _extract_cookie
    assert csrf_module._has_csrf_set_cookie is _has_csrf_set_cookie
