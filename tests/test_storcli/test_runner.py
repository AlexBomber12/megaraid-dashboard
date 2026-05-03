from __future__ import annotations

import asyncio
from typing import Any

import pytest

from megaraid_dashboard.storcli import (
    StorcliCommandFailed,
    StorcliNotAvailable,
    StorcliParseError,
    run_storcli,
)


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        return None

    async def wait(self) -> None:
        return None


@pytest.mark.asyncio
async def test_successful_run_returns_parsed_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_create_subprocess_exec(
        *argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        captured["argv"] = argv
        return FakeProcess(b'{"Controllers":[]}', b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await run_storcli(["/c0", "show", "all"], use_sudo=False, binary_path="storcli64")

    assert result == {"Controllers": []}
    assert captured["argv"] == ("storcli64", "/c0", "show", "all", "J")


@pytest.mark.asyncio
async def test_non_zero_exit_raises_command_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        return FakeProcess(b"", b"adapter failed loudly", 12)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliCommandFailed, match="adapter failed loudly"):
        await run_storcli(["/c0", "show", "all"], use_sudo=False, binary_path="storcli64")


@pytest.mark.asyncio
async def test_non_sudo_permission_denied_raises_command_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        return FakeProcess(b"", b"permission denied by device", 1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliCommandFailed, match="permission denied by device"):
        await run_storcli(["/c0", "show", "all"], use_sudo=False, binary_path="storcli64")


@pytest.mark.asyncio
async def test_sudo_permission_denied_raises_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        return FakeProcess(b"", b"sudo: a password is required", 1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliNotAvailable, match="sudo access"):
        await run_storcli(["/c0", "show", "all"], use_sudo=True, binary_path="storcli64")


@pytest.mark.asyncio
async def test_sudo_storcli_permission_denied_raises_command_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        return FakeProcess(b"", b"storcli: permission denied by device", 1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliCommandFailed, match="permission denied by device"):
        await run_storcli(["/c0", "show", "all"], use_sudo=True, binary_path="storcli64")


@pytest.mark.asyncio
async def test_invalid_json_raises_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        return FakeProcess(b"not-json", b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliParseError):
        await run_storcli(["/c0", "show", "all"], use_sudo=False, binary_path="storcli64")


@pytest.mark.asyncio
async def test_file_not_found_raises_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        raise FileNotFoundError("missing storcli")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliNotAvailable):
        await run_storcli(["/c0", "show", "all"], use_sudo=False, binary_path="storcli64")


@pytest.mark.asyncio
async def test_sudo_prefix_is_added(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_create_subprocess_exec(
        *argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        captured["argv"] = argv
        return FakeProcess(b'{"Controllers":[]}', b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await run_storcli(
        ["/c0", "show", "all"],
        use_sudo=True,
        binary_path="/usr/local/sbin/storcli64",
    )

    assert captured["argv"][:3] == ("sudo", "-n", "/usr/local/sbin/storcli64")


@pytest.mark.asyncio
async def test_json_flag_is_always_last(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_create_subprocess_exec(
        *argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        captured["argv"] = argv
        return FakeProcess(b'{"Controllers":[]}', b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await run_storcli(["/c0", "show", "all"], use_sudo=False, binary_path="storcli64")

    assert captured["argv"][-1] == "J"


@pytest.mark.asyncio
async def test_locate_template_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_create_subprocess_exec(
        *argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        captured["argv"] = argv
        return FakeProcess(b'{"Controllers":[]}', b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await run_storcli(
        ["/c0/e2/s0", "start", "locate", "J"],
        use_sudo=False,
        binary_path="storcli64",
    )

    assert captured["argv"] == ("storcli64", "/c0/e2/s0", "start", "locate", "J")


@pytest.mark.asyncio
async def test_disallowed_command_is_rejected_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        raise AssertionError("subprocess should not be called for disallowed commands")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliCommandFailed, match="not allowed"):
        await run_storcli(
            ["/c0/e2/s0", "start; rm -rf /", "locate", "J"],
            use_sudo=False,
            binary_path="storcli64",
        )


@pytest.mark.asyncio
async def test_whitelist_rejects_tokens_with_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        raise AssertionError("subprocess should not be called for malformed argv")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliCommandFailed, match="not allowed"):
        await run_storcli(
            ["/c0/e2/s0 start", "locate", "J"],
            use_sudo=False,
            binary_path="storcli64",
        )


@pytest.mark.asyncio
async def test_show_drive_template_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_create_subprocess_exec(
        *argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        captured["argv"] = argv
        return FakeProcess(b'{"Controllers":[]}', b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await run_storcli(
        ["/c0/e2/s0", "show", "all", "J"],
        use_sudo=False,
        binary_path="storcli64",
    )

    assert captured["argv"] == ("storcli64", "/c0/e2/s0", "show", "all", "J")


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["offline", "missing"])
async def test_set_offline_and_missing_templates_are_allowed(
    monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_create_subprocess_exec(
        *argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        captured["argv"] = argv
        return FakeProcess(b'{"Controllers":[]}', b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await run_storcli(
        ["/c0/e2/s0", "set", verb, "J"],
        use_sudo=False,
        binary_path="storcli64",
    )

    assert captured["argv"] == ("storcli64", "/c0/e2/s0", "set", verb, "J")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "argv",
    [
        ["/c0/e2/s0", "set", "good", "J"],
        ["/c0/e2/s0", "set", "online", "J"],
        ["/c0/e2/s0", "delete", "missing", "J"],
        ["/c0", "set", "offline", "J"],
    ],
)
async def test_set_template_rejects_unknown_verbs(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    async def fake_create_subprocess_exec(
        *_argv: str,
        **_kwargs: Any,
    ) -> FakeProcess:
        raise AssertionError("subprocess should not be called for disallowed commands")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(StorcliCommandFailed, match="not allowed"):
        await run_storcli(argv, use_sudo=False, binary_path="storcli64")
