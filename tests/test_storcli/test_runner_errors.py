from __future__ import annotations

import asyncio
from typing import Any

import pytest

from megaraid_dashboard.storcli import (
    StorcliCommandFailed,
    StorcliParseError,
    run_storcli,
)


class _Process:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        self.waited = True


class _SlowProcess(_Process):
    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(1)
        return b"", b""


@pytest.mark.asyncio
async def test_timeout_kills_process_and_raises_command_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _SlowProcess(b"", b"", 0)

    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> _SlowProcess:
        return process

    async def fake_wait_for(awaitable: Any, _timeout: float) -> Any:
        # Cancel the awaitable to clean up the running task before we raise.
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise TimeoutError("timed out")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    with pytest.raises(StorcliCommandFailed, match="timed out"):
        await run_storcli(
            ["/c0", "show", "all"],
            use_sudo=False,
            binary_path="storcli64",
            timeout_seconds=0.01,
        )

    assert process.killed is True
    assert process.waited is True


@pytest.mark.asyncio
async def test_json_root_not_object_raises_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> _Process:
        return _Process(b"[1, 2, 3]", b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliParseError, match="JSON root is not an object"):
        await run_storcli(["/c0", "show", "all"], use_sudo=False, binary_path="storcli64")


@pytest.mark.asyncio
async def test_empty_stderr_returns_empty_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    # When stderr is empty and the exit code is non-zero, _tail("") -> "" is exercised.
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> _Process:
        return _Process(b"", b"", 5)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliCommandFailed) as exc_info:
        await run_storcli(["/c0", "show", "all"], use_sudo=False, binary_path="storcli64")
    assert "exited with code 5" in str(exc_info.value)
