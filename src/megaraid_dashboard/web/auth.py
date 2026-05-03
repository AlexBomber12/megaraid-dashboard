from __future__ import annotations

import base64
import binascii
import hmac
import re
from collections.abc import Awaitable, Callable
from typing import cast

import bcrypt
import structlog
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from megaraid_dashboard.config import Settings
from megaraid_dashboard.web._whitelist import is_whitelisted
from megaraid_dashboard.web.rate_limit import AUTH_RATE_LIMIT_NOTIFY_SCOPE_KEY

LOGGER = structlog.get_logger(__name__)

_REALM = "megaraid-dashboard"
_BASIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=]+$")
_AUTHENTICATE_HEADER = f'Basic realm="{_REALM}"'


class BasicAuthMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_whitelisted(str(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return

        header_value = _get_authorization_header(scope)
        authorization = header_value.decode("latin-1") if header_value is not None else None
        verified_username = _verified_username(authorization, self.settings)
        credentials_valid = verified_username is not None
        notify_auth_result = cast(
            "Callable[[bool], Awaitable[None]] | None",
            scope.get(AUTH_RATE_LIMIT_NOTIFY_SCOPE_KEY),
        )
        if notify_auth_result is not None:
            await notify_auth_result(credentials_valid)

        if not credentials_valid:
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": _AUTHENTICATE_HEADER},
            )
            await response(scope, receive, send)
            return

        scope["user_username"] = verified_username
        await self.app(scope, receive, send)


def _is_whitelisted(path: str) -> bool:
    return is_whitelisted(path)


def _get_authorization_header(scope: Scope) -> bytes | None:
    headers = cast("list[tuple[bytes, bytes]]", scope["headers"])
    for name, value in headers:
        if name.lower() == b"authorization":
            return value
    return None


def _verify_credentials(header_value: str | None, settings: Settings) -> bool:
    return _verified_username(header_value, settings) is not None


def _verified_username(header_value: str | None, settings: Settings) -> str | None:
    if header_value is None:
        LOGGER.info("auth_failure", reason="malformed_header")
        return None

    try:
        scheme, token = header_value.split(" ", 1)
    except ValueError:
        LOGGER.info("auth_failure", reason="malformed_header")
        return None

    if scheme.lower() != "basic":
        LOGGER.info("auth_failure", reason="malformed_header")
        return None

    if _BASIC_TOKEN_RE.fullmatch(token) is None:
        LOGGER.info("auth_failure", reason="malformed_header")
        return None

    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        LOGGER.info("auth_failure", reason="malformed_header")
        return None

    if not hmac.compare_digest(username.encode(), settings.admin_username.encode()):
        LOGGER.info("auth_failure", reason="unknown_user", username=username[:64])
        return None

    try:
        password_valid = bcrypt.checkpw(
            password.encode(),
            settings.admin_password_hash.encode(),
        )
    except ValueError:
        LOGGER.error("bcrypt_hash_invalid")
        LOGGER.info("auth_failure", reason="bcrypt_hash_invalid", username=username[:64])
        return None

    if not password_valid:
        LOGGER.info("auth_failure", reason="bad_password", username=username[:64])
        return None

    return username
