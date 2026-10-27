"""Nonzero JSON must remain a failure, including for controller actions."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from megaraid_dashboard.storcli import (
    StorcliCommandFailed,
    StorcliNotAvailable,
    StorcliResponseFailed,
    run_storcli,
)


def failed_status() -> dict[str, Any]:
    return {
        "Controller": 0,
        "Status": "Failure",
        "Description": "command failed",
        "Detailed Status": [{"Status": "Failed", "Ctrl": 0, "ErrMsg": "use /cx/cv"}],
    }


@pytest.mark.parametrize(
    "command", ["/c0/bbu show all J", "/c0/fall delete J", "/c0/e252/s0 set offline J"]
)
@pytest.mark.parametrize(
    "detail",
    [
        None,
        {"ErrMsg": "use /cx/cv"},
        {"Controller": 0, "Status": "Failure", "ErrMsg": "use /cx/cv"},
        {"Ctrl": 0, "Status": "Failed", "ErrMsg": "use /cx/cv"},
    ],
)
async def test_nonzero_explicit_failure_remains_private_failure(
    scripted_storcli: Callable[[dict[str, tuple[str, int, str]]], Path],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    command: str,
    detail: dict[str, Any] | None,
) -> None:
    payload = {"Controllers": [{"Command Status": failed_status(), "SN": "PRIVATE-SERIAL"}]}
    if detail is None:
        del payload["Controllers"][0]["Command Status"]["Detailed Status"]
    else:
        payload["Controllers"][0]["Command Status"]["Detailed Status"] = [detail]
    binary = scripted_storcli({command: (json.dumps(payload), 255, "")})
    with pytest.raises(StorcliCommandFailed) as caught:
        await run_storcli(command.split(), binary_path=str(binary), use_sudo=False)
    error = caught.value
    assert isinstance(error, StorcliResponseFailed)
    assert error.payload == payload
    assert error.exit_code == 255
    assert error.err_msg is None
    captured = capsys.readouterr()
    assert (
        "PRIVATE-SERIAL" not in str(error) + repr(error) + caplog.text + captured.out + captured.err
    )
    assert "use /cx/cv" not in str(error)


@pytest.mark.parametrize(
    "status",
    [
        {},
        {"Controller": 0, "Status": "Success"},
        {"Controller": 0, "Status": "Unknown"},
        {
            "Controller": 0,
            "Status": "Failure",
            "Detailed Status": [{"Ctrl": 1, "ErrMsg": "use /cx/cv"}],
        },
        {"Status": "Failure"},
        {"Controller": True, "Status": "Failure"},
        {"Controller": 0, "Status": "Failure", "Detailed Status": []},
        {
            "Controller": 0,
            "Status": "Failure",
            "Detailed Status": [{"Status": "Success", "ErrMsg": "use /cx/cv"}],
        },
        {
            "Controller": 0,
            "Status": "Failure",
            "Detailed Status": [{"Controller": 1, "ErrMsg": "use /cx/cv"}],
        },
    ],
)
async def test_nonzero_unproven_or_contradictory_status_has_no_payload(
    scripted_storcli: Callable[[dict[str, tuple[str, int, str]]], Path], status: dict[str, Any]
) -> None:
    payload = {"Controllers": [{"Command Status": status}]}
    binary = scripted_storcli({"/c0/bbu show all J": (json.dumps(payload), 255, "")})
    with pytest.raises(StorcliCommandFailed) as caught:
        await run_storcli(["/c0/bbu", "show", "all"], binary_path=str(binary), use_sudo=False)
    assert not isinstance(caught.value, StorcliResponseFailed)


@pytest.mark.parametrize(
    "stdout", ["PRIVATE-SERIAL invalid JSON", "[]", "{}", '{"Controllers":[]}']
)
async def test_malformed_nonzero_is_fatal_without_stdout(
    scripted_storcli: Callable[[dict[str, tuple[str, int, str]]], Path], stdout: str
) -> None:
    binary = scripted_storcli({"/c0/bbu show all J": (stdout, 255, "")})
    with pytest.raises(StorcliCommandFailed) as caught:
        await run_storcli(["/c0/bbu", "show", "all"], binary_path=str(binary), use_sudo=False)
    assert not isinstance(caught.value, StorcliResponseFailed)
    assert "PRIVATE-SERIAL" not in str(caught.value)


async def test_sudo_denial_takes_precedence_over_failed_json(
    scripted_storcli: Callable[[dict[str, tuple[str, int, str]]], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    payload = {"Controllers": [{"Command Status": failed_status()}]}
    binary = scripted_storcli({"/c0/bbu show all J": (json.dumps(payload), 255, "")})
    sudo = binary.parent / "sudo"
    sudo.write_text('#!/bin/sh\necho "sudo: a password is required" >&2\nshift\nexec "$@"\n')
    sudo.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    with pytest.raises(StorcliNotAvailable):
        await run_storcli(["/c0/bbu", "show", "all"], binary_path=str(binary), use_sudo=True)
