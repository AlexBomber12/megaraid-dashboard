from __future__ import annotations

import base64
import binascii
import hmac
import re

import bcrypt
import structlog
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from megaraid_dashboard.config import Settings

LOGGER = structlog.get_logger(__name__)

_REALM = "megaraid-dashboard"
_WHITELIST_EXACT = frozenset({"/healthz", "/favicon.ico"})
_WHITELIST_PREFIX = ("/static/",)
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

        headers = dict(scope["headers"])
        header_value = headers.get(b"authorization")
        authorization = header_value.decode("latin-1") if header_value is not None else None
        if not _verify_credentials(authorization, self.settings):
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": _AUTHENTICATE_HEADER},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _is_whitelisted(path: str) -> bool:
    return path in _WHITELIST_EXACT or path.startswith(_WHITELIST_PREFIX)


def _verify_credentials(header_value: str | None, settings: Settings) -> bool:
    if header_value is None or not header_value.startswith("Basic "):
        LOGGER.info("auth_failure", reason="malformed_header")
        return False

    token = header_value.removeprefix("Basic ")
    if _BASIC_TOKEN_RE.fullmatch(token) is None:
        LOGGER.info("auth_failure", reason="malformed_header")
        return False

    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        LOGGER.info("auth_failure", reason="malformed_header")
        return False

    if not hmac.compare_digest(username.encode(), settings.admin_username.encode()):
        LOGGER.info("auth_failure", reason="unknown_user", username=username[:64])
        return False

    try:
        password_valid = bcrypt.checkpw(
            password.encode(),
            settings.admin_password_hash.encode(),
        )
    except ValueError:
        LOGGER.error("bcrypt_hash_invalid")
        LOGGER.info("auth_failure", reason="bcrypt_hash_invalid", username=username[:64])
        return False

    if not password_valid:
        LOGGER.info("auth_failure", reason="bad_password", username=username[:64])
        return False

    return True
