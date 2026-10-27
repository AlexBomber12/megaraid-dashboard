from __future__ import annotations

import asyncio
import json
import re
from time import monotonic
from typing import Any, Literal, cast

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from megaraid_dashboard.storcli.exceptions import (
    StorcliCommandFailed,
    StorcliNotAvailable,
    StorcliParseError,
    StorcliResponseFailed,
)

LOGGER = structlog.get_logger(__name__)
_ALLOWED_COMMAND_PATTERNS = (
    re.compile(r"^/c0 show all J$"),
    re.compile(r"^/c0/vall show all J$"),
    re.compile(r"^/c0/eall/sall show all J$"),
    re.compile(r"^/c0/cv show all J$"),
    re.compile(r"^/c0/bbu show all J$"),
    re.compile(r"^/c0 show patrolread J$"),
    re.compile(r"^/c0 (start|stop) patrolread J$"),
    re.compile(r"^/c0 set patrolread=on mode=(auto|manual) J$"),
    re.compile(r"^/c0 set patrolread=off J$"),
    re.compile(r"^/c0 show cc J$"),
    re.compile(r"^/c0/vall show cc J$"),
    re.compile(r"^/c0/v(?:all|\d+) start cc J$"),
    re.compile(r"^/c0/vall stop cc J$"),
    re.compile(r"^/c0 set cc=(conc|seq) J$"),
    re.compile(r"^/c0 set alarm=(silence|off|on) J$"),
    re.compile(r"^/c0/fall show all J$"),
    re.compile(r"^/c0/fall import J$"),
    re.compile(r"^/c0/fall delete J$"),
    re.compile(r"^/c0/e\d+/s\d+ show all J$"),
    re.compile(r"^/c0/e\d+/s\d+ show rebuild J$"),
    re.compile(r"^/c0/e\d+/s\d+ (start|stop) locate J$"),
    re.compile(r"^/c0/e\d+/s\d+ set (offline|missing|good) J$"),
    re.compile(r"^/c0/e\d+/s\d+ spindown J$"),
    re.compile(r"^/c0/e\d+/s\d+ add hotsparedrive dgs=\d+ J$"),
    re.compile(r"^/c0/e\d+/s\d+ insert dg=\d+ array=\d+ row=\d+ J$"),
)


class _FailedDetail(BaseModel):
    model_config = ConfigDict(strict=True)
    status: Literal["Failure", "Failed"] | None = Field(default=None, alias="Status")
    controller: int | None = Field(default=None, alias="Controller")
    ctrl: int | None = Field(default=None, alias="Ctrl")
    reason: str = Field(alias="ErrMsg", min_length=1)


class _FailedStatus(BaseModel):
    model_config = ConfigDict(strict=True)
    controller: int = Field(alias="Controller")
    status: Literal["Failure"] = Field(alias="Status")
    details: list[_FailedDetail] | None = Field(default=None, alias="Detailed Status", min_length=1)


class _FailedController(BaseModel):
    status: _FailedStatus = Field(alias="Command Status")


class _FailedResponse(BaseModel):
    controllers: list[_FailedController] = Field(alias="Controllers", min_length=1, max_length=1)


def _failed_response(stdout: bytes) -> dict[str, Any] | None:
    """Retain only unambiguous failed JSON, without leaking validation input."""
    try:
        payload = json.loads(stdout)
        response = _FailedResponse.model_validate(payload)
    except (ValueError, UnicodeDecodeError, ValidationError):
        return None
    status = response.controllers[0].status
    if status.details and any(
        identity is not None and identity != status.controller
        for detail in status.details
        for identity in (detail.controller, detail.ctrl)
    ):
        return None
    return dict(payload)


async def run_storcli(
    args: list[str],
    *,
    use_sudo: bool,
    binary_path: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    command_args = _with_json_flag(args)
    _validate_command(command_args)
    argv = [binary_path, *command_args]
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
        # Any stderr (including sudo diagnostics) remains a transport failure.
        # Never return a failed payload as a successful command, even for writes.
        payload = _failed_response(stdout) if not stderr_text else None
        if payload is not None:
            raise StorcliResponseFailed(cast(int, process.returncode), payload)
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


def _with_json_flag(args: list[str]) -> list[str]:
    if args and args[-1] == "J":
        return list(args)
    return [*args, "J"]


def _validate_command(args: list[str]) -> None:
    if any(arg == "" or re.search(r"\s", arg) is not None for arg in args):
        command = " ".join(args)
        raise StorcliCommandFailed(f"storcli command is not allowed: {command!r}", err_msg=command)
    command = " ".join(args)
    if any(pattern.fullmatch(command) for pattern in _ALLOWED_COMMAND_PATTERNS):
        return
    raise StorcliCommandFailed(f"storcli command is not allowed: {command!r}", err_msg=command)


def _sudo_blocked(stderr_text: str) -> bool:
    lowered = stderr_text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "a password is required",
            "a terminal is required",
            "not in the sudoers",
        )
    )


def _tail(text: str, *, max_length: int = 500) -> str:
    if not text:
        return ""
    return text[-max_length:]
