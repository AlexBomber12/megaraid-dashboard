from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

import structlog

from megaraid_dashboard.storcli.exceptions import (
    StorcliCommandFailed,
    StorcliNotAvailable,
    StorcliParseError,
)

LOGGER = structlog.get_logger(__name__)


async def run_storcli(
    args: list[str],
    *,
    use_sudo: bool,
    binary_path: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    argv = [binary_path, *args, "J"]
    if use_sudo:
        argv = ["sudo", "-n", *argv]

    started_at = monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise StorcliNotAvailable(f"storcli is not available: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise StorcliCommandFailed(f"storcli timed out after {timeout_seconds} seconds") from exc

    duration_seconds = monotonic() - started_at
    stderr_text = stderr.decode(errors="replace").strip()
    LOGGER.debug(
        "storcli_invocation",
        argv=argv,
        duration_seconds=duration_seconds,
        exit_code=process.returncode,
    )

    if use_sudo and _sudo_blocked(stderr_text):
        raise StorcliNotAvailable(f"storcli sudo access is not available: {_tail(stderr_text)}")
    if process.returncode != 0:
        err_msg = _tail(stderr_text)
        raise StorcliCommandFailed(
            f"storcli exited with code {process.returncode}: {err_msg}",
            err_msg=err_msg,
        )

    stdout_text = stdout.decode(errors="replace")
    try:
        parsed = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise StorcliParseError("storcli stdout is not valid JSON") from exc

    if not isinstance(parsed, dict):
        raise StorcliParseError("storcli JSON root is not an object")
    return parsed


def _sudo_blocked(stderr_text: str) -> bool:
    lowered = stderr_text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "a password is required",
            "a terminal is required",
            "not in the sudoers",
            "permission denied",
        )
    )


def _tail(text: str, *, max_length: int = 500) -> str:
    if not text:
        return ""
    return text[-max_length:]
