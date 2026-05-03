from __future__ import annotations

import asyncio
import ipaddress
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from megaraid_dashboard.config import Settings
from megaraid_dashboard.web._whitelist import is_whitelisted

_WINDOW_SECONDS = 60.0
_RETRY_AFTER_SECONDS = 60
_GLOBAL_PRUNE_INTERVAL_SECONDS = 60.0
AUTH_RATE_LIMIT_NOTIFY_SCOPE_KEY = "megaraid_dashboard.auth_rate_limit_notify"

AuthRateLimitNotify = Callable[[bool], Awaitable[None]]


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
        self._next_global_prune_at = self._time_func() + _GLOBAL_PRUNE_INTERVAL_SECONDS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_whitelisted(str(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return

        client_ip = _client_ip(scope)
        reserved_slot = await self._reserve_attempt_slot(client_ip, self._time_func())
        if reserved_slot is None:
            response = JSONResponse(
                {"error": "rate_limit_exceeded"},
                status_code=429,
                headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
            )
            await response(scope, receive, send)
            return

        status_code: int | None = None
        auth_result_received = False

        async def notify_auth_result(credentials_valid: bool) -> None:
            nonlocal auth_result_received
            auth_result_received = True
            if credentials_valid:
                await self._release_attempt_slot(client_ip, reserved_slot)

        async def send_with_status_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        rate_limited_scope = dict(scope)
        rate_limited_scope[AUTH_RATE_LIMIT_NOTIFY_SCOPE_KEY] = notify_auth_result

        try:
            await self.app(rate_limited_scope, receive, send_with_status_capture)
        except asyncio.CancelledError:
            if not auth_result_received:
                await self._release_attempt_slot(client_ip, reserved_slot)
            raise
        except Exception:
            if not auth_result_received:
                await self._release_attempt_slot(client_ip, reserved_slot)
            raise

        if not auth_result_received and status_code != 401:
            await self._release_attempt_slot(client_ip, reserved_slot)

    async def _is_limited(self, client_ip: str, now: float) -> bool:
        async with self._lock:
            self._prune_expired_attempts(now)
            attempts = self._attempts.get(client_ip)
            if attempts is None:
                return False
            _evict_expired(attempts, now)
            if not attempts:
                self._attempts.pop(client_ip, None)
                return False
            return len(attempts) >= self.limit

    async def _record_failed_attempt(self, client_ip: str, now: float) -> None:
        async with self._lock:
            self._prune_expired_attempts(now)
            attempts = self._attempts[client_ip]
            _evict_expired(attempts, now)
            attempts.append(_AttemptSlot(recorded_at=now))

    async def _reserve_attempt_slot(self, client_ip: str, now: float) -> _AttemptSlot | None:
        async with self._lock:
            self._prune_expired_attempts(now)
            attempts = self._attempts[client_ip]
            _evict_expired(attempts, now)
            if len(attempts) >= self.limit:
                if not attempts:
                    self._attempts.pop(client_ip, None)
                return None
            slot = _AttemptSlot(recorded_at=now)
            attempts.append(slot)
            return slot

    async def _release_attempt_slot(self, client_ip: str, slot: _AttemptSlot) -> None:
        async with self._lock:
            attempts = self._attempts.get(client_ip)
            if attempts is None:
                return
            try:
                attempts.remove(slot)
            except ValueError:
                return
            if not attempts:
                self._attempts.pop(client_ip, None)

    def _prune_expired_attempts(self, now: float) -> None:
        if now < self._next_global_prune_at:
            return

        for client_ip, attempts in list(self._attempts.items()):
            _evict_expired(attempts, now)
            if not attempts:
                self._attempts.pop(client_ip, None)
        self._next_global_prune_at = now + _GLOBAL_PRUNE_INTERVAL_SECONDS


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
