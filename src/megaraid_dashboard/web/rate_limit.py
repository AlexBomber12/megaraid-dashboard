from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import cast

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from megaraid_dashboard.config import Settings
from megaraid_dashboard.web._whitelist import is_whitelisted

_WINDOW_SECONDS = 60.0
_RETRY_AFTER_SECONDS = 60


class AuthRateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.limit = settings.auth_rate_limit_per_minute + settings.auth_rate_limit_burst
        self._attempts: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._time_func = time_func

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_whitelisted(str(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return

        client_ip = _client_ip(scope)
        now = self._time_func()
        if await self._is_limited(client_ip, now):
            response = JSONResponse(
                {"error": "rate_limit_exceeded"},
                status_code=429,
                headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
            )
            await response(scope, receive, send)
            return

        status_code: int | None = None

        async def send_with_status_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        await self.app(scope, receive, send_with_status_capture)

        if status_code == 401:
            await self._record_attempt(client_ip, self._time_func())

    async def _is_limited(self, client_ip: str, now: float) -> bool:
        async with self._lock:
            attempts = self._attempts[client_ip]
            _evict_expired(attempts, now)
            if len(attempts) >= self.limit:
                return True
            if not attempts:
                self._attempts.pop(client_ip, None)
            return False

    async def _record_attempt(self, client_ip: str, now: float) -> None:
        async with self._lock:
            attempts = self._attempts[client_ip]
            _evict_expired(attempts, now)
            attempts.append(now)


def _evict_expired(attempts: deque[float], now: float) -> None:
    expires_before = now - _WINDOW_SECONDS
    while attempts and attempts[0] <= expires_before:
        attempts.popleft()


def _client_ip(scope: Scope) -> str:
    headers = Headers(raw=cast("list[tuple[bytes, bytes]]", scope["headers"]))
    forwarded_for = headers.get("x-forwarded-for")
    if forwarded_for is not None:
        entries = [entry.strip() for entry in forwarded_for.split(",")]
        for entry in reversed(entries):
            if entry:
                return entry

    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])
    return "unknown"
