from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import structlog

from megaraid_dashboard.config import Settings

_LOG = structlog.get_logger(__name__)

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_RETRY_AFTER_MAX_SECONDS = 3600
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_TOTAL_TIMEOUT_SECONDS = 10.0
_RESPONSE_MAX_BYTES = 64 * 1024
_TOKEN_PATTERN = re.compile(r"^[0-9]{5,16}:[A-Za-z0-9_-]{30,64}$")
_CHAT_ID_PATTERN = re.compile(r"^-?[1-9][0-9]{4,19}$")
_TOKEN_FILE_MAX_BYTES = 256
_ERROR_DESCRIPTION_MAX_LENGTH = 160
# httpx logs every request URL at INFO, and the Bot API puts the token in the URL path.
_HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


class TelegramConfigError(Exception):
    """Telegram is enabled but its configuration is unusable; nothing is sent."""


class TelegramSendError(Exception):
    """A send attempt failed. The message never contains the bot token."""

    def __init__(self, reason: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        # Validate before any timedelta/float conversion. Untrusted JSON integers
        # can exceed platform or datetime ranges; bool is not a duration.
        if retry_after_seconds is None:
            self.retry_after_seconds = None
        elif (
            isinstance(retry_after_seconds, int)
            and not isinstance(retry_after_seconds, bool)
            and retry_after_seconds > 0
        ):
            self.retry_after_seconds = min(retry_after_seconds, TELEGRAM_RETRY_AFTER_MAX_SECONDS)
            if retry_after_seconds > TELEGRAM_RETRY_AFTER_MAX_SECONDS:
                _LOG.warning("telegram_retry_after_clamped", max_seconds=3600)
        else:
            self.retry_after_seconds = 60


@dataclass(frozen=True)
class TelegramConfig:
    chat_id: str
    token: str = field(repr=False)


@dataclass
class _ResponseState:
    status_code: int | None = None


def load_telegram_config(settings: Settings, *, chat_id: str | None = None) -> TelegramConfig:
    resolved_chat_id = (chat_id if chat_id is not None else settings.telegram_chat_id).strip()
    if not _CHAT_ID_PATTERN.fullmatch(resolved_chat_id):
        msg = "TELEGRAM_CHAT_ID must be a numeric chat id"
        raise TelegramConfigError(msg)
    return TelegramConfig(
        chat_id=resolved_chat_id,
        token=_read_token_file(settings.telegram_bot_token_file),
    )


def _read_token_file(raw_path: str) -> str:
    if not raw_path.strip():
        msg = "TELEGRAM_BOT_TOKEN_FILE is not set"
        raise TelegramConfigError(msg)
    path = Path(raw_path)
    if not path.is_absolute():
        msg = "TELEGRAM_BOT_TOKEN_FILE must be an absolute path"
        raise TelegramConfigError(msg)
    try:
        # O_NONBLOCK keeps a FIFO from hanging the notifier; it is rejected as non-regular.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        msg = f"TELEGRAM_BOT_TOKEN_FILE cannot be opened: {exc.strerror}"
        raise TelegramConfigError(msg) from None
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            msg = "TELEGRAM_BOT_TOKEN_FILE must be a regular file"
            raise TelegramConfigError(msg)
        if file_stat.st_mode & (stat.S_IRWXO | stat.S_IWGRP):
            msg = "TELEGRAM_BOT_TOKEN_FILE must not be group-writable or accessible by others"
            raise TelegramConfigError(msg)
        raw = os.read(fd, _TOKEN_FILE_MAX_BYTES + 1)
    finally:
        os.close(fd)
    try:
        token = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        token = ""
    if len(raw) > _TOKEN_FILE_MAX_BYTES or not _TOKEN_PATTERN.fullmatch(token):
        msg = "TELEGRAM_BOT_TOKEN_FILE does not contain a valid bot token"
        raise TelegramConfigError(msg)
    return token


class TelegramTransport:
    def __init__(self, config: TelegramConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client
        for logger_name in _HTTP_CLIENT_LOGGERS:
            logging.getLogger(logger_name).setLevel(logging.WARNING)

    @property
    def chat_id(self) -> str:
        return self._config.chat_id

    def send(self, text: str) -> None:
        asyncio.run(self.send_async(text))

    async def send_async(self, text: str) -> None:
        text = text[:TELEGRAM_MAX_MESSAGE_LENGTH]
        payload: dict[str, str | bool] = {
            "chat_id": self._config.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        start = time.monotonic()
        state = _ResponseState()
        try:
            response = await self._send_bounded(payload, state)
        except TelegramSendError as exc:
            if state.status_code == 429:
                raise TelegramSendError(exc.reason, retry_after_seconds=60) from None
            raise
        except (httpx.TimeoutException, TimeoutError):
            raise TelegramSendError(
                "timeout",
                retry_after_seconds=60 if state.status_code == 429 else None,
            ) from None
        except Exception as exc:
            # httpx exception text can embed the request URL, so only the type is kept.
            raise TelegramSendError(
                f"network error: {type(exc).__name__}",
                retry_after_seconds=60 if state.status_code == 429 else None,
            ) from None
        self._raise_for_response(response)
        _LOG.info(
            "alert_telegram_send",
            elapsed_ms=int((time.monotonic() - start) * 1000),
            text_length=len(text),
        )

    async def _send_bounded(
        self,
        payload: dict[str, str | bool],
        state: _ResponseState,
    ) -> httpx.Response:
        # The absolute deadline includes headers, every body chunk and client cleanup.
        # HTTPX's read timeout alone resets after each received chunk.
        async with asyncio.timeout(_TOTAL_TIMEOUT_SECONDS):
            if self._client is not None:
                return await self._read_response(self._client, payload, state)
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
                return await self._read_response(client, payload, state)

    async def _read_response(
        self, client: httpx.AsyncClient, payload: dict[str, str | bool], state: _ResponseState
    ) -> httpx.Response:
        # Never bind the token-bearing URL in our locals or include it in exceptions.
        async with client.stream(
            "POST",
            self._url(),
            json=payload,
            timeout=_TIMEOUT,
            headers={"Accept-Encoding": "identity"},
            follow_redirects=False,
        ) as response:
            state.status_code = response.status_code
            # Reject compression before HTTPX can allocate a decompressed chunk.
            if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
                raise TelegramSendError("unexpected response encoding")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _RESPONSE_MAX_BYTES:
                    raise TelegramSendError("response too large")
                body.extend(chunk)
            return httpx.Response(response.status_code, content=bytes(body))

    def _url(self) -> str:
        return f"{TELEGRAM_API_BASE_URL}/bot{self._config.token}/sendMessage"

    def _raise_for_response(self, response: httpx.Response) -> None:
        body = _json_body(response)
        if response.status_code == 200 and body.get("ok") is True:
            return
        description = self._redact(str(body.get("description") or ""))
        description = description[:_ERROR_DESCRIPTION_MAX_LENGTH]
        if response.status_code == 429:
            raise TelegramSendError(
                "rate limited",
                retry_after_seconds=_retry_after_seconds(body) or 60,
            )
        reason = f"HTTP {response.status_code}"
        if description:
            reason = f"{reason}: {description}"
        raise TelegramSendError(reason)

    def _redact(self, value: str) -> str:
        return value.replace(self._config.token, "[redacted]")


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except (ValueError, RecursionError):
        return {}
    return body if isinstance(body, dict) else {}


def _retry_after_seconds(body: dict[str, Any]) -> int | None:
    parameters = body.get("parameters")
    if not isinstance(parameters, dict):
        return None
    retry_after = parameters.get("retry_after")
    if isinstance(retry_after, int) and not isinstance(retry_after, bool) and retry_after > 0:
        return retry_after
    return None
