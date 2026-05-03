from __future__ import annotations

import base64
import hmac
import secrets
from http.cookies import SimpleCookie

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_COOKIE_NAME = "__Host-csrf"
_HEADER_NAME = "X-CSRF-Token"
_TOKEN_BYTES = 32
_PROTECTED_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})
_WHITELIST_EXACT = frozenset({"/healthz", "/favicon.ico"})
_WHITELIST_PREFIX = ("/static/",)
_COOKIE_VALUE_LENGTH = 43


class CsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_whitelisted(str(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        cookie_token = _extract_cookie(headers, _COOKIE_NAME)

        if str(scope.get("method", "")).upper() in _PROTECTED_METHODS:
            header_token = headers.get(_HEADER_NAME)
            if (
                cookie_token is None
                or header_token is None
                or not hmac.compare_digest(cookie_token, header_token)
            ):
                response = PlainTextResponse("csrf token missing or mismatched", status_code=403)
                await response(scope, receive, send)
                return

        if cookie_token is not None:
            await self.app(scope, receive, send)
            return

        async def send_with_csrf_cookie(message: Message) -> None:
            if message["type"] == "http.response.start" and not _has_csrf_set_cookie(message):
                mutable_headers = MutableHeaders(scope=message)
                mutable_headers.append("Set-Cookie", _build_cookie(_generate_token()))
            await send(message)

        await self.app(scope, receive, send_with_csrf_cookie)


def _generate_token() -> str:
    token = base64.urlsafe_b64encode(secrets.token_bytes(_TOKEN_BYTES)).rstrip(b"=").decode()
    if len(token) != _COOKIE_VALUE_LENGTH:
        msg = "unexpected csrf token length"
        raise RuntimeError(msg)
    return token


def _extract_cookie(headers: Headers, name: str) -> str | None:
    cookie_header = headers.get("cookie")
    if cookie_header is None:
        return None

    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return None

    morsel = cookie.get(name)
    if morsel is None:
        return None
    return morsel.value


def _is_whitelisted(path: str) -> bool:
    return path in _WHITELIST_EXACT or path.startswith(_WHITELIST_PREFIX)


def _has_csrf_set_cookie(message: Message) -> bool:
    headers = Headers(raw=message["headers"])
    for header_value in headers.getlist("set-cookie"):
        cookie = SimpleCookie()
        try:
            cookie.load(header_value)
        except Exception:
            continue
        if _COOKIE_NAME in cookie:
            return True
    return False


def _build_cookie(token: str) -> str:
    return f"{_COOKIE_NAME}={token}; Path=/; SameSite=Strict; Secure"
