from __future__ import annotations

import re

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

_SAFE_PREFIX_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)+$")


class ForwardedPrefixMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            headers = Headers(scope=scope)
            forwarded_prefix = headers.get("x-forwarded-prefix")
            if forwarded_prefix and _is_safe_forwarded_prefix(forwarded_prefix):
                scope = dict(scope)
                scope["root_path"] = forwarded_prefix
        await self.app(scope, receive, send)


def _is_safe_forwarded_prefix(value: str) -> bool:
    if _SAFE_PREFIX_RE.fullmatch(value) is None:
        return False
    return all(segment not in {".", ".."} for segment in value.split("/")[1:])
