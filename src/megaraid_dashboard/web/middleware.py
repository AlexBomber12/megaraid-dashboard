from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send


class ForwardedPrefixMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            headers = Headers(scope=scope)
            forwarded_prefix = headers.get("x-forwarded-prefix")
            if forwarded_prefix:
                scope = dict(scope)
                scope["root_path"] = forwarded_prefix
        await self.app(scope, receive, send)
