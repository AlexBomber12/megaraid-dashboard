from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from megaraid_dashboard.config import Settings
from megaraid_dashboard.web._whitelist import is_whitelisted

_WINDOW_SECONDS = 60.0
_RETRY_AFTER_SECONDS = 60


@dataclass(eq=False)
class _AttemptSlot:
    recorded_at: float


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
        self._attempts: defaultdict[str, deque[_AttemptSlot]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._time_func = time_func

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_whitelisted(str(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return

        client_ip = _client_ip(scope)
        attempt_slot = await self._reserve_attempt(client_ip, self._time_func())
        if attempt_slot is None:
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

        try:
            await self.app(scope, receive, send_with_status_capture)
        except Exception:
            await self._release_attempt(client_ip, attempt_slot, self._time_func())
            raise

        if status_code != 401:
            await self._release_attempt(client_ip, attempt_slot, self._time_func())

    async def _reserve_attempt(self, client_ip: str, now: float) -> _AttemptSlot | None:
        async with self._lock:
            attempts = self._attempts[client_ip]
            _evict_expired(attempts, now)
            if len(attempts) >= self.limit:
                if not attempts:
                    self._attempts.pop(client_ip, None)
                return None
            attempt_slot = _AttemptSlot(recorded_at=now)
            attempts.append(attempt_slot)
            return attempt_slot

    async def _release_attempt(
        self,
        client_ip: str,
        attempt_slot: _AttemptSlot,
        now: float,
    ) -> None:
        async with self._lock:
            attempts = self._attempts[client_ip]
            _evict_expired(attempts, now)
            with contextlib.suppress(ValueError):
                attempts.remove(attempt_slot)
            if not attempts:
                self._attempts.pop(client_ip, None)


def _evict_expired(attempts: deque[_AttemptSlot], now: float) -> None:
    expires_before = now - _WINDOW_SECONDS
    while attempts and attempts[0].recorded_at <= expires_before:
        attempts.popleft()


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    peer_ip = str(client[0]) if isinstance(client, tuple) and client else "unknown"

    headers = Headers(raw=cast("list[tuple[bytes, bytes]]", scope["headers"]))
    forwarded_for = headers.get("x-forwarded-for")
    if forwarded_for is not None and _is_trusted_proxy_peer(peer_ip):
        entries = [entry.strip() for entry in forwarded_for.split(",")]
        for entry in reversed(entries):
            if entry:
                return entry

    return peer_ip


def _is_trusted_proxy_peer(peer_ip: str) -> bool:
    try:
        return ipaddress.ip_address(peer_ip).is_loopback
    except ValueError:
        return False
