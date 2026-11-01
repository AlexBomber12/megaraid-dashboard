from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from time import monotonic
from typing import Any, Literal, cast

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from megaraid_dashboard.storcli.exceptions import (
    StorcliCommandFailed,
    StorcliError,
    StorcliNotAvailable,
    StorcliParseError,
    StorcliResponseFailed,
)
from megaraid_dashboard.storcli.states import normalize_alarm_state

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


class _WriteDetail(BaseModel):
    model_config = ConfigDict(strict=True)
    controller: int | None = Field(default=None, alias="Controller", ge=0, le=0)
    ctrl: int | None = Field(default=None, alias="Ctrl", ge=0, le=0)
    status: Literal["Success"] = Field(alias="Status")


class _WriteStatus(BaseModel):
    model_config = ConfigDict(strict=True)
    controller: int = Field(alias="Controller", ge=0, le=0)
    status: Literal["Success", "Failure"] = Field(alias="Status")
    details: list[_WriteDetail] | None = Field(default=None, alias="Detailed Status")


class _WriteController(BaseModel):
    status: _WriteStatus = Field(alias="Command Status")


class _WriteResponse(BaseModel):
    controllers: list[_WriteController] = Field(alias="Controllers", min_length=1, max_length=1)


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


async def check_storcli_prewrite(
    args: list[str],
    *,
    use_sudo: bool,
    binary_path: str,
    authorization_path: str,
    expected_foreign_digest: str | None = None,
    timeout_seconds: float = 30.0,
) -> None:
    """Validate live evidence for an approved write without executing that write."""
    command_args = _with_json_flag(args)
    _validate_command(command_args)
    from megaraid_dashboard.storcli.policy import (
        load_policy,
        replacement_command_refusal,
        verify_pinned_binary,
    )

    try:
        verify_pinned_binary(binary_path)
        policy = load_policy(authorization_path)
        refusal = replacement_command_refusal(policy, command_args)
        if refusal is not None:
            raise ValueError(refusal)
        if not policy.permits(command_args):
            raise ValueError("Command lacks reviewed syntax/topology approval")
    except (OSError, ValueError) as exc:
        raise StorcliNotAvailable(
            f"Action refused before execution: {exc}. Ask the administrator to review policy."
        ) from exc
    identity = await run_storcli(
        ["/c0", "show", "all", "J"],
        use_sudo=use_sudo,
        binary_path=binary_path,
        timeout_seconds=timeout_seconds,
    )
    from megaraid_dashboard.storcli.parser import parse_controller_show_all

    controller = parse_controller_show_all(identity)
    if (
        controller.serial_number != policy.controller_serial
        or controller.model_name != "LSI MegaRAID SAS 9270CV-8i"
    ):
        raise StorcliCommandFailed(
            "Controller identity changed. Stop and have the administrator verify inventory."
        )
    alarm_states = {
        "alarm=on": {"Off"},
        "alarm=off": {"On", "Silenced"},
        "alarm=silence": {"On"},
    }
    if (
        command_args[2] in alarm_states
        and normalize_alarm_state(controller.alarm_state) not in alarm_states[command_args[2]]
    ):
        raise StorcliCommandFailed(
            "Buzzer state is unknown or conflicts. Refresh controller status."
        )
    from megaraid_dashboard.storcli.prewrite import check_action_topology, check_foreign_safety

    check_action_topology(command_args, identity, policy)
    if command_args[0] == "/c0/fall":

        async def read(args: list[str]) -> dict[str, Any]:
            return await run_storcli(
                args,
                use_sudo=use_sudo,
                binary_path=binary_path,
                timeout_seconds=timeout_seconds,
            )

        await check_foreign_safety(identity, policy, read, expected_foreign_digest)


async def run_storcli(
    args: list[str],
    *,
    use_sudo: bool,
    binary_path: str,
    timeout_seconds: float = 30.0,
    authorization_path: str | None = None,
    expected_foreign_digest: str | None = None,
    before_write: Callable[[], None] | None = None,
) -> dict[str, Any]:
    command_args = _with_json_flag(args)
    _validate_command(command_args)
    try:
        if authorization_path is not None and "show" not in command_args:
            from megaraid_dashboard.storcli.policy import load_policy, verify_pinned_binary

            try:
                approved = load_policy(authorization_path)
            except (OSError, ValueError) as exc:
                raise StorcliNotAvailable(
                    "Authorization cannot be verified; no write sent"
                ) from exc
            await check_storcli_prewrite(
                command_args,
                use_sudo=use_sudo,
                binary_path=binary_path,
                authorization_path=authorization_path,
                expected_foreign_digest=expected_foreign_digest,
                timeout_seconds=timeout_seconds,
            )
            try:
                if load_policy(authorization_path) != approved:
                    raise ValueError(
                        "Command policy changed during preflight; reload and review again"
                    )
                verify_pinned_binary(binary_path)
            except (OSError, ValueError) as exc:
                raise StorcliNotAvailable(
                    "Authorization changed before execution; no write sent"
                ) from exc
        if before_write is not None and "show" not in command_args:
            before_write()
    except StorcliError as exc:
        # All failures in this block precede write dispatch, including failed safety reads.
        exc.write_sent = False
        raise
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
    if authorization_path is not None and "show" not in command_args:
        try:
            _WriteResponse.model_validate(parsed)
        except ValidationError as exc:
            raise StorcliParseError(
                "Controller result is uncertain. Inspect hardware and audit before retrying."
            ) from exc
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
