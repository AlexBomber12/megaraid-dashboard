from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from megaraid_dashboard.config import Settings
from megaraid_dashboard.web.rate_limit import (
    _GLOBAL_PRUNE_INTERVAL_SECONDS,
    AUTH_RATE_LIMIT_NOTIFY_SCOPE_KEY,
    AuthRateLimitMiddleware,
    _AttemptSlot,
    _client_ip,
    _is_trusted_proxy_peer,
    _parse_trusted_proxy_networks,
)
from tests.conftest import TEST_ADMIN_PASSWORD_HASH


@pytest.fixture
def settings() -> Settings:
    return Settings(
        alert_smtp_host="smtp.example.test",
        alert_smtp_port=587,
        alert_smtp_user="alert@example.test",
        alert_smtp_password="test-token",
        alert_from="alert@example.test",
        alert_to="ops@example.test",
        admin_username="admin",
        admin_password_hash=TEST_ADMIN_PASSWORD_HASH,
        storcli_path="/usr/local/sbin/storcli64",
        metrics_interval_seconds=300,
        collector_enabled=False,
        database_url="sqlite:///:memory:",
        log_level="INFO",
        auth_rate_limit_per_minute=2,
        auth_rate_limit_burst=0,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _build_scope(client: tuple[str, int] | None = ("203.0.113.10", 12345)) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": client,
    }


async def _empty_receive() -> dict:
    return {"type": "http.disconnect"}


def _collect_send() -> tuple[list[dict], Callable[[dict], Awaitable[None]]]:
    messages: list[dict] = []

    async def send(message: dict) -> None:
        messages.append(message)

    return messages, send


async def test_release_slot_on_cancelled_error(settings: Settings) -> None:
    async def cancelled_app(scope: object, receive: object, send: object) -> None:
        del scope, receive, send
        raise asyncio.CancelledError

    limiter = AuthRateLimitMiddleware(cancelled_app, settings=settings)
    messages, send = _collect_send()

    with pytest.raises(asyncio.CancelledError):
        await limiter(_build_scope(), _empty_receive, send)

    assert messages == []
    assert dict(limiter._attempts) == {}


async def test_cancelled_error_after_notify_does_not_double_release(
    settings: Settings,
) -> None:
    async def notified_then_cancelled(scope: dict, receive: object, send: object) -> None:
        del receive, send
        await scope[AUTH_RATE_LIMIT_NOTIFY_SCOPE_KEY](True)
        raise asyncio.CancelledError

    limiter = AuthRateLimitMiddleware(notified_then_cancelled, settings=settings)
    messages, send = _collect_send()

    with pytest.raises(asyncio.CancelledError):
        await limiter(_build_scope(), _empty_receive, send)

    assert messages == []
    assert dict(limiter._attempts) == {}


async def test_release_slot_on_generic_exception(settings: Settings) -> None:
    class _BoomError(RuntimeError):
        pass

    async def exploding_app(scope: object, receive: object, send: object) -> None:
        del scope, receive, send
        raise _BoomError("kaboom")

    limiter = AuthRateLimitMiddleware(exploding_app, settings=settings)
    messages, send = _collect_send()

    with pytest.raises(_BoomError):
        await limiter(_build_scope(), _empty_receive, send)

    assert messages == []
    assert dict(limiter._attempts) == {}


async def test_generic_exception_after_notify_does_not_double_release(
    settings: Settings,
) -> None:
    class _BoomError(RuntimeError):
        pass

    async def notified_then_boom(scope: dict, receive: object, send: object) -> None:
        del receive, send
        await scope[AUTH_RATE_LIMIT_NOTIFY_SCOPE_KEY](True)
        raise _BoomError("kaboom after notify")

    limiter = AuthRateLimitMiddleware(notified_then_boom, settings=settings)
    messages, send = _collect_send()

    with pytest.raises(_BoomError):
        await limiter(_build_scope(), _empty_receive, send)

    assert messages == []
    assert dict(limiter._attempts) == {}


async def test_release_slot_on_non_401_response_without_notify(settings: Settings) -> None:
    async def ok_app(scope: object, receive: object, send: object) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    limiter = AuthRateLimitMiddleware(ok_app, settings=settings)
    messages, send = _collect_send()
    await limiter(_build_scope(), _empty_receive, send)

    assert any(msg["type"] == "http.response.start" for msg in messages)
    assert dict(limiter._attempts) == {}


async def test_release_slot_on_500_response_without_notify(settings: Settings) -> None:
    async def server_error_app(scope: object, receive: object, send: object) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 500, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    limiter = AuthRateLimitMiddleware(server_error_app, settings=settings)
    messages, send = _collect_send()
    await limiter(_build_scope(), _empty_receive, send)

    assert dict(limiter._attempts) == {}


async def test_401_response_without_notify_retains_slot(settings: Settings) -> None:
    async def unauthorized_app(scope: object, receive: object, send: object) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    limiter = AuthRateLimitMiddleware(unauthorized_app, settings=settings)
    messages, send = _collect_send()
    await limiter(_build_scope(), _empty_receive, send)

    attempts = limiter._attempts
    assert len(attempts) == 1
    bucket = next(iter(attempts.values()))
    assert len(bucket) == 1


async def test_notify_with_invalid_credentials_keeps_slot(settings: Settings) -> None:
    async def notify_app(scope: dict, receive: object, send: object) -> None:
        del receive
        notify = scope[AUTH_RATE_LIMIT_NOTIFY_SCOPE_KEY]
        await notify(False)
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    limiter = AuthRateLimitMiddleware(notify_app, settings=settings)
    messages, send = _collect_send()
    await limiter(_build_scope(), _empty_receive, send)

    attempts = limiter._attempts
    assert len(attempts) == 1
    bucket = next(iter(attempts.values()))
    assert len(bucket) == 1


async def test_is_limited_returns_false_when_ip_unknown(settings: Settings) -> None:
    clock = _Clock()
    limiter = AuthRateLimitMiddleware(_unused_app, settings=settings, time_func=clock.monotonic)

    assert await limiter._is_limited("203.0.113.99", clock.monotonic()) is False


async def test_is_limited_drops_bucket_when_only_expired_attempts(settings: Settings) -> None:
    clock = _Clock()
    limiter = AuthRateLimitMiddleware(_unused_app, settings=settings, time_func=clock.monotonic)

    await limiter._record_failed_attempt("203.0.113.50", clock.monotonic())
    limiter._next_global_prune_at = clock.monotonic() + 1e9
    clock.advance(60.1)

    assert await limiter._is_limited("203.0.113.50", clock.monotonic()) is False
    assert "203.0.113.50" not in limiter._attempts


async def test_is_limited_returns_true_when_bucket_is_at_limit(settings: Settings) -> None:
    clock = _Clock()
    limiter = AuthRateLimitMiddleware(_unused_app, settings=settings, time_func=clock.monotonic)

    for _ in range(limiter.limit):
        await limiter._record_failed_attempt("203.0.113.30", clock.monotonic())

    assert await limiter._is_limited("203.0.113.30", clock.monotonic()) is True


async def test_reserve_attempt_slot_pops_empty_bucket_when_limit_is_zero(
    settings: Settings,
) -> None:
    limiter = AuthRateLimitMiddleware(_unused_app, settings=settings)
    limiter.limit = 0

    slot = await limiter._reserve_attempt_slot("203.0.113.77", 0.0)

    assert slot is None
    assert "203.0.113.77" not in limiter._attempts


async def test_release_attempt_slot_is_no_op_when_ip_missing(settings: Settings) -> None:
    limiter = AuthRateLimitMiddleware(_unused_app, settings=settings)
    rogue = _AttemptSlot(recorded_at=0.0)

    await limiter._release_attempt_slot("203.0.113.55", rogue)

    assert "203.0.113.55" not in limiter._attempts


async def test_release_attempt_slot_is_no_op_when_slot_not_in_bucket(
    settings: Settings,
) -> None:
    limiter = AuthRateLimitMiddleware(_unused_app, settings=settings)
    real_slot = await limiter._reserve_attempt_slot("203.0.113.66", 0.0)
    assert real_slot is not None
    stranger = _AttemptSlot(recorded_at=0.0)

    await limiter._release_attempt_slot("203.0.113.66", stranger)

    bucket = limiter._attempts["203.0.113.66"]
    assert list(bucket) == [real_slot]


async def test_prune_loop_continues_when_some_buckets_still_have_fresh_slots(
    settings: Settings,
) -> None:
    clock = _Clock()
    limiter = AuthRateLimitMiddleware(_unused_app, settings=settings, time_func=clock.monotonic)

    await limiter._record_failed_attempt("203.0.113.1", clock.monotonic())
    clock.advance(_GLOBAL_PRUNE_INTERVAL_SECONDS - 1.0)
    await limiter._record_failed_attempt("203.0.113.2", clock.monotonic())

    clock.advance(2.0)
    await limiter._record_failed_attempt("203.0.113.3", clock.monotonic())

    assert sorted(limiter._attempts) == ["203.0.113.2", "203.0.113.3"]


def test_is_trusted_proxy_peer_returns_false_for_invalid_ip() -> None:
    networks = _parse_trusted_proxy_networks("127.0.0.1")

    assert _is_trusted_proxy_peer("not-an-ip", networks) is False
    assert _is_trusted_proxy_peer("unknown", networks) is False


def test_client_ip_uses_peer_when_all_forwarded_entries_are_empty() -> None:
    networks = _parse_trusted_proxy_networks("127.0.0.1")
    scope = {
        "client": ("127.0.0.1", 12345),
        "headers": [(b"x-forwarded-for", b", , ,")],
    }

    assert _client_ip(scope, networks) == "127.0.0.1"


def test_client_ip_skips_empty_entries_in_forwarded_chain() -> None:
    networks = _parse_trusted_proxy_networks("127.0.0.1")
    scope = {
        "client": ("127.0.0.1", 12345),
        "headers": [(b"x-forwarded-for", b"198.51.100.10, ")],
    }

    assert _client_ip(scope, networks) == "198.51.100.10"


def test_client_ip_returns_unknown_when_client_tuple_missing() -> None:
    networks = _parse_trusted_proxy_networks("127.0.0.1")
    scope = {
        "client": None,
        "headers": [(b"x-forwarded-for", b"198.51.100.10")],
    }

    assert _client_ip(scope, networks) == "unknown"


async def _unused_app(scope: object, receive: object, send: object) -> None:
    del scope, receive, send
    raise AssertionError("inner app should not be invoked")
