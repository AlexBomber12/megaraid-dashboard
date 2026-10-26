from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from megaraid_dashboard.alerts import telegram as telegram_module
from megaraid_dashboard.alerts.telegram import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TelegramConfig,
    TelegramConfigError,
    TelegramSendError,
    TelegramTransport,
    load_telegram_config,
)
from megaraid_dashboard.config import Settings

FAKE_TOKEN = "123456:TEST-FAKE-TOKEN-NOT-A-REAL-SECRET-000"
CHAT_ID = "-1001234567890"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "alert_smtp_host": "smtp.example.test",
        "alert_smtp_port": 587,
        "alert_smtp_user": "alert@example.test",
        "alert_smtp_password": "test-token",
        "alert_from": "alert@example.test",
        "alert_to": "ops@example.test",
        "admin_username": "admin",
        "admin_password_hash": "hash",
        "storcli_path": "/usr/local/sbin/storcli64",
        "metrics_interval_seconds": 300,
        "log_level": "INFO",
        "telegram_chat_id": CHAT_ID,
    }
    base.update(overrides)
    return Settings(**base)


def _token_file(tmp_path: Path, content: bytes = FAKE_TOKEN.encode(), mode: int = 0o600) -> Path:
    path = tmp_path / "telegram_bot_token"
    path.write_bytes(content)
    path.chmod(mode)
    return path


def _transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> TelegramTransport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramTransport(TelegramConfig(chat_id=CHAT_ID, token=FAKE_TOKEN), client=client)


def _ok(request: httpx.Request) -> httpx.Response:
    del request
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o400])
def test_load_config_reads_protected_token_file(tmp_path: Path, mode: int) -> None:
    token_file = _token_file(tmp_path, content=f"  {FAKE_TOKEN}\n".encode(), mode=mode)

    config = load_telegram_config(_settings(telegram_bot_token_file=str(token_file)))

    assert config == TelegramConfig(chat_id=CHAT_ID, token=FAKE_TOKEN)
    assert FAKE_TOKEN not in repr(config)


def test_load_config_chat_id_override(tmp_path: Path) -> None:
    token_file = _token_file(tmp_path)

    config = load_telegram_config(
        _settings(telegram_bot_token_file=str(token_file), telegram_chat_id=""),
        chat_id=" -1009999999999 ",
    )

    assert config.chat_id == "-1009999999999"


@pytest.mark.parametrize("chat_id", ["", "abc", "@channel", "123", "-0012345", "12345 6"])
def test_load_config_rejects_non_numeric_chat_id(tmp_path: Path, chat_id: str) -> None:
    token_file = _token_file(tmp_path)

    with pytest.raises(TelegramConfigError, match="numeric chat id"):
        load_telegram_config(
            _settings(telegram_bot_token_file=str(token_file), telegram_chat_id=chat_id)
        )


def test_load_config_requires_token_file_setting() -> None:
    with pytest.raises(TelegramConfigError, match="is not set"):
        load_telegram_config(_settings(telegram_bot_token_file=" "))


def test_load_config_requires_absolute_token_path() -> None:
    with pytest.raises(TelegramConfigError, match="absolute path"):
        load_telegram_config(_settings(telegram_bot_token_file="relative/token"))


def test_load_config_reports_missing_file_without_path_contents(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(TelegramConfigError, match="cannot be opened") as exc_info:
        load_telegram_config(_settings(telegram_bot_token_file=str(missing)))

    assert exc_info.value.__cause__ is None


def test_load_config_rejects_symlinked_token_file(tmp_path: Path) -> None:
    target = _token_file(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(TelegramConfigError, match="cannot be opened"):
        load_telegram_config(_settings(telegram_bot_token_file=str(link)))


def test_load_config_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)

    with pytest.raises(TelegramConfigError, match="regular file"):
        load_telegram_config(_settings(telegram_bot_token_file=str(fifo)))


def test_load_config_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(TelegramConfigError, match="regular file"):
        load_telegram_config(_settings(telegram_bot_token_file=str(tmp_path)))


@pytest.mark.parametrize("mode", [0o604, 0o644, 0o660, 0o602, 0o601])
def test_load_config_rejects_loose_permissions(tmp_path: Path, mode: int) -> None:
    token_file = _token_file(tmp_path, mode=mode)

    with pytest.raises(TelegramConfigError, match="group-writable or accessible by others"):
        load_telegram_config(_settings(telegram_bot_token_file=str(token_file)))


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not-a-token",
        b"123456:short",
        # Cyrillic capital TE (UTF-8 d0 a2) in place of the Latin letter.
        b"123456:\xd0\xa2EST-FAKE-TOKEN-NOT-A-REAL-SECRET-000",
        b"\xff\xfe" + FAKE_TOKEN.encode(),
        FAKE_TOKEN.encode() + b" " * 300,
    ],
)
def test_load_config_rejects_invalid_token_content(tmp_path: Path, content: bytes) -> None:
    token_file = _token_file(tmp_path, content=content)

    with pytest.raises(TelegramConfigError, match="valid bot token") as exc_info:
        load_telegram_config(_settings(telegram_bot_token_file=str(token_file)))

    assert FAKE_TOKEN not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_send_posts_plain_text_message() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ok(request)

    with capture_logs() as logs:
        _transport(handler).send("hello")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": CHAT_ID,
        "text": "hello",
        "disable_web_page_preview": True,
    }
    assert request.extensions["timeout"] == {
        "connect": 5.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }
    assert [entry["event"] for entry in logs] == ["alert_telegram_send"]
    assert FAKE_TOKEN not in repr(logs)


def test_send_truncates_to_telegram_limit() -> None:
    texts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts.append(json.loads(request.content)["text"])
        return _ok(request)

    _transport(handler).send("x" * (TELEGRAM_MAX_MESSAGE_LENGTH + 50))

    assert len(texts[0]) == TELEGRAM_MAX_MESSAGE_LENGTH


def test_send_without_injected_client_uses_bounded_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []
    real_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        created.append(kwargs)
        return real_client(transport=httpx.MockTransport(_ok), **kwargs)

    monkeypatch.setattr(telegram_module.httpx, "AsyncClient", client_factory)
    transport = TelegramTransport(TelegramConfig(chat_id=CHAT_ID, token=FAKE_TOKEN))

    transport.send("hello")

    assert created == [{"timeout": telegram_module._TIMEOUT, "follow_redirects": False}]
    assert transport.chat_id == CHAT_ID


def test_transport_silences_http_client_request_logging() -> None:
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)

    _transport(_ok)

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_send_timeout_is_reported_without_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out calling {request.url}", request=request)

    with pytest.raises(TelegramSendError) as exc_info:
        _transport(handler).send("hello")

    assert exc_info.value.reason == "timeout"
    assert exc_info.value.retry_after_seconds is None
    assert exc_info.value.__suppress_context__ is True
    assert exc_info.value.__cause__ is None


def test_send_network_error_hides_exception_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(TelegramSendError) as exc_info:
        _transport(handler).send("hello")

    assert exc_info.value.reason == "network error: ConnectError"
    assert FAKE_TOKEN not in str(exc_info.value)
    assert exc_info.value.__suppress_context__ is True


def test_send_rate_limit_reports_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            429,
            json={
                "ok": False,
                "description": "Too Many Requests: retry after 7",
                "parameters": {"retry_after": 7},
            },
        )

    with pytest.raises(TelegramSendError) as exc_info:
        _transport(handler).send("hello")

    assert exc_info.value.reason == "rate limited"
    assert exc_info.value.retry_after_seconds == 7


@pytest.mark.parametrize(
    "body",
    [
        {"ok": False},
        {"ok": False, "parameters": "soon"},
        {"ok": False, "parameters": {"retry_after": True}},
        {"ok": False, "parameters": {"retry_after": 0}},
        {"ok": False, "parameters": {"retry_after": "5"}},
    ],
)
def test_send_rate_limit_without_usable_retry_after(body: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, json=body)

    with pytest.raises(TelegramSendError) as exc_info:
        _transport(handler).send("hello")

    assert exc_info.value.retry_after_seconds == 60


def test_send_api_error_redacts_token_from_description() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            401,
            json={"ok": False, "description": f"Unauthorized bot{FAKE_TOKEN} " + "x" * 400},
        )

    with pytest.raises(TelegramSendError) as exc_info:
        _transport(handler).send("hello")

    reason = exc_info.value.reason
    assert reason.startswith("HTTP 401: Unauthorized bot[redacted] ")
    assert FAKE_TOKEN not in reason
    assert len(reason) <= len("HTTP 401: ") + 160


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(502, text="<html>bad gateway</html>"), "HTTP 502"),
        (httpx.Response(200, json=["unexpected"]), "HTTP 200"),
        (httpx.Response(200, json={"ok": False, "description": "Bad Request"}), "HTTP 200: Bad"),
        (httpx.Response(400, json={"ok": False, "description": ""}), "HTTP 400"),
    ],
)
def test_send_rejects_unsuccessful_responses(response: httpx.Response, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return response

    with pytest.raises(TelegramSendError) as exc_info:
        _transport(handler).send("hello")

    assert exc_info.value.reason.startswith(expected)


def test_token_file_check_uses_nofollow_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_file = _token_file(tmp_path)
    flags_seen: list[int] = []
    real_open = os.open

    def tracking_open(path: Any, flags: int, *args: Any) -> int:
        flags_seen.append(flags)
        return real_open(path, flags, *args)

    monkeypatch.setattr(telegram_module.os, "open", tracking_open)

    load_telegram_config(_settings(telegram_bot_token_file=str(token_file)))

    assert flags_seen and flags_seen[0] & os.O_NOFOLLOW and flags_seen[0] & os.O_NONBLOCK


class _Stream(httpx.AsyncByteStream):
    def __init__(
        self, chunks: list[bytes], *, delay: float = 0, error: Exception | None = None
    ) -> None:
        self.chunks = chunks
        self.delay = delay
        self.error = error
        self.reads = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            self.reads += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status", [200, 429])
def test_absolute_deadline_stops_trickling_response_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(telegram_module, "_TOTAL_TIMEOUT_SECONDS", 0.05)
    stream = _Stream([b" " for _ in range(100)], delay=0.01)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(status, stream=stream)

    with pytest.raises(TelegramSendError, match=r"^timeout$") as error:
        _transport(handler).send("hello")

    assert stream.closed
    assert stream.reads < 100
    assert error.value.retry_after_seconds == (60 if status == 429 else None)
    assert FAKE_TOKEN not in str(error.value)


def test_absolute_deadline_also_covers_response_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_module, "_TOTAL_TIMEOUT_SECONDS", 0.01)
    cancelled: list[bool] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.append(True)
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(TelegramSendError, match=r"^timeout$"):
        _transport(handler).send("hello")
    assert cancelled == [True]


@pytest.mark.parametrize("status", [200, 429])
def test_stream_byte_cap_ignores_false_content_length_and_stops_reading(status: int) -> None:
    stream = _Stream([b" " * telegram_module._RESPONSE_MAX_BYTES, b"x", b"must not be read"])

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, headers={"Content-Length": "1"}, stream=stream)

    with pytest.raises(TelegramSendError, match=r"^response too large$") as error:
        _transport(handler).send("hello")
    assert stream.reads == 2
    assert stream.closed
    assert error.value.retry_after_seconds == (60 if status == 429 else None)


def test_response_exactly_at_byte_cap_is_accepted_and_closed() -> None:
    body = b'{"ok": true}'
    stream = _Stream([body, b" " * (telegram_module._RESPONSE_MAX_BYTES - len(body))])
    _transport(lambda _: httpx.Response(200, stream=stream)).send("hello")
    assert stream.closed and stream.reads == 2


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br"])
def test_compressed_response_is_rejected_before_decompression(encoding: str) -> None:
    stream = _Stream([b"untrusted compressed bytes"])
    with pytest.raises(TelegramSendError, match=r"^unexpected response encoding$"):
        _transport(
            lambda _: httpx.Response(200, headers={"Content-Encoding": encoding}, stream=stream)
        ).send("hello")
    assert stream.closed and stream.reads == 0


@pytest.mark.parametrize("status", [200, 429])
def test_body_network_failure_closes_stream_and_keeps_token_out_of_error(status: int) -> None:
    stream = _Stream([b"{"], error=httpx.ReadError(f"failed bot{FAKE_TOKEN}"))
    with pytest.raises(TelegramSendError, match=r"^network error: ReadError$") as error:
        _transport(lambda _: httpx.Response(status, stream=stream)).send("hello")
    assert stream.closed
    assert error.value.retry_after_seconds == (60 if status == 429 else None)
    assert FAKE_TOKEN not in str(error.value)


def test_deeply_nested_response_is_rejected_without_leaking_its_contents() -> None:
    body = b"[" * 2000 + json.dumps(FAKE_TOKEN).encode() + b"]" * 2000
    with pytest.raises(TelegramSendError, match=r"^HTTP 200$") as error:
        _transport(lambda _: httpx.Response(200, content=body)).send("hello")
    assert FAKE_TOKEN not in str(error.value)


def test_redirect_is_never_followed_even_if_injected_client_allows_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://untrusted.invalid/"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    sender = TelegramTransport(TelegramConfig(chat_id=CHAT_ID, token=FAKE_TOKEN), client=client)
    with pytest.raises(TelegramSendError, match=r"^HTTP 302$"):
        sender.send("hello")
    assert len(requests) == 1 and requests[0].url.host == "api.telegram.org"


@pytest.mark.parametrize("hint", [1, 3599, 3600, 3601, 315360000, 10**400])
def test_http_429_caps_hint_before_any_duration_conversion(hint: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"parameters": {"retry_after": hint}})

    with pytest.raises(TelegramSendError) as caught:
        _transport(handler).send("hello")
    assert caught.value.reason == "rate limited"
    assert caught.value.retry_after_seconds == min(hint, 3600)


def test_http_429_integer_beyond_json_parser_limit_uses_safe_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b'{"parameters":{"retry_after":' + b"9" * 5000 + b"}}")

    with pytest.raises(TelegramSendError) as caught:
        _transport(handler).send("hello")
    assert caught.value.retry_after_seconds == 60


@pytest.mark.parametrize("hint", [0, -1, True, False, "3600", 1.5, float("inf")])
def test_invalid_injected_retry_hint_uses_safe_fallback(hint: Any) -> None:
    assert TelegramSendError("rate limited", retry_after_seconds=hint).retry_after_seconds == 60
