"""Offline rollout tests: wrapper boundary fake, never a real StorCLI executable."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from megaraid_dashboard.storcli import StorcliError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rollout", ROOT / "scripts/storcli_rollout.py")
assert spec and spec.loader
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)
VERIFY_BINARY = rollout.verify_binary
FIXTURES = ROOT / "tests/fixtures/storcli/redacted"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def protected_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "approved"
    directory.mkdir(mode=0o755)
    baseline = directory / "inventory.json"
    baseline.write_text("{}")
    baseline.chmod(0o640)
    real_lstat = Path.lstat
    real_access = os.access

    def root_owned(path: Path):
        values = list(real_lstat(path))
        values[4] = 0
        values[5] = 0
        if path == tmp_path or path in tmp_path.parents:
            values[0] &= ~0o022  # Model protected ancestors outside our temporary sandbox.
        return os.stat_result(values)

    def service_access(path: Path, mode: int, **kwargs):
        if mode == os.W_OK:
            return bool(path.lstat().st_mode & 0o022)
        if mode == os.R_OK:
            return bool(path.lstat().st_mode & 0o044)
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(Path, "lstat", root_owned)
    monkeypatch.setattr(rollout.os, "access", service_access)
    return baseline


@pytest.fixture
def reads(
    monkeypatch: pytest.MonkeyPatch,
    protected_inventory: Path,
) -> tuple[dict, Path, AsyncMock]:
    monkeypatch.setattr(rollout.pwd, "getpwuid", lambda _: SimpleNamespace(pw_name="megaraid"))
    inventory = load("eall_sall_show_all.json")
    # Redacted baseline has two drives; synthesize distinct identities for all 8.
    data = inventory["Controllers"][0]["Response Data"]
    original = copy.deepcopy(data)
    data.clear()
    for slot in range(8):
        for key, value in original.items():
            if "/s0" in key:
                text = json.dumps({key: value}).replace("/s0", f"/s{slot}")
                text = text.replace("252:0", f"252:{slot}").replace(
                    "WD-WM00000001", f"SERIAL{slot}"
                )
                data.update(json.loads(text))
    controller = load("c0_show_all.json")
    # The legacy evidence does not include CLI Version. Do not manufacture it.
    controller["Controllers"][0]["Command Status"].pop("CLI Version")
    summary = controller["Controllers"][0]["Response Data"]
    summary["Physical Drives"] = 8
    summary["PD LIST"] = [
        value[0]
        for key, value in data.items()
        if key.startswith("Drive ") and isinstance(value, list)
    ]
    # The completeness counters are in PD LIST/VD LIST in some firmware; adjust fixture counts.
    payloads = {
        "/c0 show all J": controller,
        "/c0/vall show all J": load("vall_show_all.json"),
        "/c0/eall/sall show all J": inventory,
        "/c0/cv show all J": load("123_cv_invalid_learn.json"),
        "/c0/bbu show all J": load("bbu_show_all.json"),
        "/c0/fall show all J": load("c0_fall_show_all_absent.json"),
        "/c0 show patrolread J": {
            "Controllers": [
                {
                    "Command Status": {"Status": "Success"},
                    "Response Data": {"PR Mode": "Auto", "PR State": "Stopped"},
                }
            ]
        },
        "/c0 show cc J": load("123_cc_show.json"),
        "/c0/vall show cc J": load("123_cc_progress.json"),
    }
    for slot in range(8):
        detail = copy.deepcopy(inventory)
        detail["Controllers"][0]["Response Data"] = {
            key: value for key, value in data.items() if f"/s{slot}" in key
        }
        payloads[f"/c0/e252/s{slot} show all J"] = detail
        payloads[f"/c0/e252/s{slot} show rebuild J"] = json.loads(
            json.dumps(load("123_rebuild_idle.json")).replace("/s0", f"/s{slot}")
        )
    for payload in payloads.values():
        payload["Controllers"][0]["Command Status"]["Controller"] = 0
    fake = AsyncMock(side_effect=lambda args, **kwargs: copy.deepcopy(payloads[" ".join(args)]))
    monkeypatch.setattr(rollout, "run_storcli", fake)
    monkeypatch.setattr(rollout, "verify_binary", Mock())
    expected = {
        "controller_serial": "SV00000001",
        "drive_serials": {f"252:{slot}": f"SERIAL{slot}" for slot in range(8)},
    }
    protected_inventory.write_text(json.dumps(expected))
    return payloads, protected_inventory, fake


async def test_all_reads(reads: tuple) -> None:
    _, expected, fake = reads
    result = await rollout.probe(rollout.STAGED, expected)
    assert set(result) == {*rollout.READS, "_binary_identity"}
    assert result["_binary_identity"] == {
        "sha256": rollout.LEGACY_HASH,
        "pinned_version": "1.23.02",
        "json_cli_version": None,
        "version_metadata": "unavailable",
    }
    assert fake.await_count == 25
    assert all(call.kwargs["use_sudo"] for call in fake.await_args_list)


@pytest.mark.parametrize("slot", range(8))
@pytest.mark.parametrize("kind", ["all", "rebuild"])
async def test_every_drive_failure_is_required(reads: tuple, slot: int, kind: str) -> None:
    payloads, expected, _ = reads
    payloads[f"/c0/e252/s{slot} show {kind} J"]["Controllers"][0]["Command Status"]["Status"] = (
        "Failure"
    )
    with pytest.raises((StorcliError, ValueError), match="failed"):
        await rollout.probe(rollout.STAGED, expected)


@pytest.mark.parametrize("command", rollout.READS)
@pytest.mark.parametrize("status", ["Failure", None, "Unknown"])
async def test_status_is_mandatory(reads: tuple, command: str, status: str | None) -> None:
    payloads, expected, _ = reads
    payloads[command]["Controllers"][0]["Command Status"] = {
        "Status": status,
        "Description": "device error",
    }
    with pytest.raises((StorcliError, ValueError)):
        await rollout.probe(rollout.STAGED, expected)


async def test_only_explicit_optional_unsupported_is_tolerated(reads: tuple) -> None:
    payloads, expected, _ = reads
    for command in rollout.OPTIONAL:
        payloads[command] = load("737_patrol_unsupported.json")
        payloads[command]["Controllers"][0]["Command Status"]["Controller"] = 0
    result = await rollout.probe(rollout.STAGED, expected)
    assert all(result[command]["unsupported"] for command in rollout.OPTIONAL)


@pytest.mark.parametrize("fault", ["version", "controller", "inventory", "drive", "rebuild", "cv"])
async def test_identity_and_schema_failures(reads: tuple, fault: str) -> None:
    payloads, expected, _ = reads
    if fault == "version":
        payloads["/c0 show all J"]["Controllers"][0]["Command Status"]["CLI Version"] = "7.37"
    elif fault == "controller":
        baseline = json.loads(expected.read_text())
        baseline["controller_serial"] = "WRONG"
        expected.write_text(json.dumps(baseline))
    elif fault == "inventory":
        baseline = json.loads(expected.read_text())
        baseline["drive_serials"]["252:7"] = "WRONG"
        expected.write_text(json.dumps(baseline))
    elif fault == "drive":
        data = payloads["/c0/e252/s7 show all J"]["Controllers"][0]["Response Data"]
        data["Drive /c0/e252/s7 - Detailed Information"]["Drive /c0/e252/s7 Device attributes"][
            "SN"
        ] = "WRONG"
    elif fault == "rebuild":
        payloads["/c0/e252/s7 show rebuild J"] = load("123_rebuild_idle.json")
    else:
        payloads["/c0/cv show all J"]["Controllers"][0]["Response Data"] = {}
    with pytest.raises((StorcliError, ValueError)):
        await rollout.probe(rollout.STAGED, expected)


def test_policy_exact_authorization(tmp_path: Path) -> None:
    for binary in (rollout.STAGED, rollout.ORIGINAL):
        policy = rollout.policy(binary)
        assert "*" not in policy
        assert len(rollout.READS) == 25
        for slot in range(8):
            for kind in ("all", "rebuild"):
                assert f"{binary} /c0/e252/s{slot} show {kind} J" in policy
        expected = {
            f"{target} show {kind} J"
            for target, kinds in [
                ("/c0", ["all", "patrolread", "cc"]),
                ("/c0/vall", ["all", "cc"]),
                ("/c0/eall/sall", ["all"]),
                ("/c0/cv", ["all"]),
                ("/c0/bbu", ["all"]),
                ("/c0/fall", ["all"]),
                *[(f"/c0/e252/s{slot}", ["all", "rebuild"]) for slot in range(8)],
            ]
            for kind in kinds
        }
        # Decode the generated sudoers alias and compare all authorized argv, including J.
        alias, grant = policy.replace("\\\n", "").strip().splitlines()
        allowed = {
            item.strip().removeprefix(str(binary) + " ")
            for item in alias.split(" = ", 1)[1].split(",")
        }
        assert allowed == expected
        assert grant == "megaraid ALL=(root) NOPASSWD: MEGARAID_RO_READS"
        from megaraid_dashboard.storcli.runner import _validate_command

        for command in allowed:
            _validate_command(command.split())
        assert all(" show " in command for command in allowed)
        path = tmp_path / "sudoers"
        path.write_text(policy)
        subprocess.run(["visudo", "-c", "-f", str(path)], check=True, capture_output=True)
    with pytest.raises(ValueError):
        rollout.policy(Path("/tmp/other"))


def test_secure_paths_and_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Root-owned fake metadata allows unprivileged tests; production checks stay strict.
    real_lstat = Path.lstat

    def root_owned(path: Path):
        info = real_lstat(path)
        values = list(info)
        values[4] = 0
        if path in tmp_path.parents or path == tmp_path:
            values[0] &= ~0o022
        return type(info)(values)

    monkeypatch.setattr(Path, "lstat", root_owned)
    executable = tmp_path / "fake"
    executable.write_bytes(b"fake executable, never executed")
    executable.chmod(0o755)
    rollout.verify_binary(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="SHA-256"):
        rollout.verify_binary(executable, "0" * 64)
    executable.chmod(0o777)
    with pytest.raises(ValueError, match="Unsafe"):
        rollout.secure_path(executable)
    executable.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(executable)
    with pytest.raises(ValueError, match="symlink"):
        rollout.secure_path(link)
    directory = tmp_path / "unsafe"
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    with pytest.raises(ValueError, match="Unsafe"):
        rollout.secure_path(directory / "missing")
    with pytest.raises(ValueError):
        rollout.secure_path(Path("relative"))


def test_stage_preserves_originals_and_never_overwrites(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    original = tmp_path / "original"
    existing = tmp_path / "existing"
    existing.mkdir(mode=0o751)
    existing.chmod(0o751)
    target = existing / "new" / "nested" / "storcli64"
    source.write_bytes(b"legacy fixture")
    original.write_bytes(b"original fixture")
    monkeypatch.setattr(rollout, "SOURCE", source)
    monkeypatch.setattr(rollout, "ORIGINAL", original)
    monkeypatch.setattr(rollout, "STAGED", target)
    monkeypatch.setattr(rollout.os, "geteuid", lambda: 0)
    monkeypatch.setattr(rollout.os, "fchown", lambda *args: None)
    ownership_changes = []
    monkeypatch.setattr(
        rollout.os,
        "chown",
        lambda path, uid, gid, **kwargs: ownership_changes.append((path, uid, gid)),
    )
    checks = []
    monkeypatch.setattr(rollout, "verify_binary", lambda *args: checks.append(args))
    real_lstat = Path.lstat

    def root_owned(path: Path):
        values = list(real_lstat(path))
        values[4] = 0
        values[5] = 0
        values[0] &= ~0o022
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", root_owned)
    before = (source.read_bytes(), original.read_bytes())
    previous_umask = os.umask(0o077)
    try:
        rollout.stage()
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(existing.stat().st_mode) == 0o751
    assert ownership_changes == [(existing / "new", 0, 0), (target.parent, 0, 0)]
    for directory in (existing / "new", target.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o755
        assert directory.stat().st_mode & stat.S_IXOTH  # Unprivileged service can traverse.
    assert target.read_bytes() == before[0]
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert (source.read_bytes(), original.read_bytes()) == before
    assert checks == [
        (source, rollout.LEGACY_HASH),
        (original, rollout.ORIGINAL_HASH),
        (target, rollout.LEGACY_HASH),
        (source, rollout.LEGACY_HASH),
        (original, rollout.ORIGINAL_HASH),
    ]
    with pytest.raises(FileExistsError):
        rollout.stage()
    monkeypatch.setattr(rollout.os, "geteuid", lambda: 1000)
    with pytest.raises(ValueError, match="root shell"):
        rollout.stage()


async def test_probe_requires_service_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rollout.pwd, "getpwuid", lambda _: SimpleNamespace(pw_name="root"))
    fake = AsyncMock(side_effect=AssertionError("Must not execute"))
    monkeypatch.setattr(rollout, "run_storcli", fake)
    with pytest.raises(ValueError, match="Run probe as megaraid"):
        await rollout.probe(rollout.STAGED, Path("/unused-inventory.json"))
    fake.assert_not_called()


@pytest.mark.parametrize("version", ["1.23.02 test", "001.2302.0000.0000 legacy"])
async def test_optional_version_metadata_is_consistent(reads: tuple, version: str) -> None:
    payloads, baseline, _ = reads
    payloads["/c0 show all J"]["Controllers"][0]["Command Status"]["CLI Version"] = version
    result = await rollout.probe(rollout.STAGED, baseline)
    assert result["_binary_identity"]["json_cli_version"] == version
    assert result["_binary_identity"]["version_metadata"] == "reported"


@pytest.mark.parametrize("version", ["7.37", "", None, 123])
async def test_present_version_must_match_pin(reads: tuple, version: object) -> None:
    payloads, baseline, _ = reads
    payloads["/c0 show all J"]["Controllers"][0]["Command Status"]["CLI Version"] = version
    with pytest.raises(ValueError, match="Unexpected CLI version"):
        await rollout.probe(rollout.STAGED, baseline)


async def test_missing_version_still_requires_verified_hash(reads: tuple) -> None:
    _, baseline, fake = reads
    result = await rollout.probe(rollout.STAGED, baseline)
    assert result["_binary_identity"]["version_metadata"] == "unavailable"
    assert rollout.verify_binary.call_args_list == [
        ((rollout.STAGED, rollout.LEGACY_HASH),),
        ((rollout.STAGED, rollout.LEGACY_HASH),),
    ]
    fake.reset_mock()
    rollout.verify_binary.side_effect = ValueError("SHA-256 mismatch")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        await rollout.probe(rollout.STAGED, baseline)
    fake.assert_not_called()


@pytest.mark.parametrize(
    "fault",
    [
        "group-writable-parent",
        "service-writable-parent",
        "writable-file",
        "symlink-file",
        "symlink-parent",
    ],
)
async def test_probe_rejects_mutable_or_symlink_baseline(
    reads: tuple,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    _, baseline, fake = reads
    if fault == "group-writable-parent":
        baseline.parent.chmod(0o770)  # File remains root:root 0640 in simulated metadata.
        assert baseline.lstat().st_uid == 0
        assert stat.S_IMODE(baseline.stat().st_mode) == 0o640
    elif fault == "service-writable-parent":
        access = os.access
        monkeypatch.setattr(
            rollout.os,
            "access",
            lambda path, mode, **kwargs: (
                True
                if path == baseline.parent and mode == os.W_OK
                else access(path, mode, **kwargs)
            ),
        )
    elif fault == "writable-file":
        baseline.chmod(0o660)
    elif fault == "symlink-file":
        target = baseline.with_name("real.json")
        baseline.rename(target)
        baseline.symlink_to(target)
    else:
        parent = baseline.parent
        target = parent.with_name("real-approved")
        parent.rename(target)
        parent.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match=r"Unsafe|Service-writable"):
        await rollout.probe(rollout.STAGED, baseline)
    fake.assert_not_called()
    rollout.verify_binary.assert_not_called()


async def test_hash_verified_fake_binary_without_version_metadata(
    reads: tuple,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, baseline, fake = reads
    binary = tmp_path / "fake-storcli"
    binary.write_bytes(b"Offline fake bytes; subprocess boundary remains mocked")
    binary.chmod(0o755)
    # Use a fixture pin for fake bytes, while exercising the real hash/path verification.
    fixture_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(rollout, "STAGED", binary)
    monkeypatch.setattr(rollout, "LEGACY_HASH", fixture_hash)
    monkeypatch.setattr(rollout, "verify_binary", VERIFY_BINARY)
    result = await rollout.probe(binary, baseline)
    assert result["_binary_identity"]["sha256"] == fixture_hash
    assert result["_binary_identity"]["version_metadata"] == "unavailable"
    assert fake.await_count == 25
    binary.write_bytes(b"Changed bytes")
    fake.reset_mock()
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        await rollout.probe(binary, baseline)
    fake.assert_not_called()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "unrelated",
        "wrong-controller",
        "multiple",
        "missing-identity",
        "required",
        "serial",
        "success-nonzero",
        "detail-success",
        "multiple-details",
        "malformed",
        "stderr",
        "substring",
    ],
)
async def test_executable_full_probe_bbu_redirect(
    reads: tuple,
    scripted_storcli: Callable[[dict[str, tuple[str, int, str]]], Path],
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    from megaraid_dashboard.storcli import run_storcli

    payloads, baseline, _ = reads
    bbu = {
        "Command Status": {
            "Controller": 0,
            "Status": "Failure",
            "Description": "command failed",
            "Detailed Status": [{"Status": "Failed", "Ctrl": 0, "ErrMsg": "use /cx/cv"}],
        }
    }
    payloads["/c0/bbu show all J"] = {"Controllers": [bbu]}
    # Actual 1.23.02 foreign-absent result is a successful response.
    payloads["/c0/fall show all J"] = {
        "Controllers": [
            {
                "Command Status": {
                    "Controller": 0,
                    "Status": "Success",
                    "Description": "Couldn't find any foreign Configuration",
                }
            }
        ]
    }
    if fault == "unrelated":
        bbu["Command Status"]["Detailed Status"][0]["ErrMsg"] = "device error"
    elif fault == "wrong-controller":
        bbu["Command Status"]["Controller"] = 1
    elif fault == "multiple":
        payloads["/c0/bbu show all J"]["Controllers"].append(copy.deepcopy(bbu))
    elif fault == "missing-identity":
        del bbu["Command Status"]["Controller"]
    elif fault == "required":
        payloads["/c0/cv show all J"] = {"Controllers": [bbu]}
    elif fault == "success-nonzero":
        bbu["Command Status"]["Status"] = "Success"
    elif fault == "detail-success":
        bbu["Command Status"]["Detailed Status"][0]["Status"] = "Success"
    elif fault == "multiple-details":
        bbu["Command Status"]["Detailed Status"].append({"ErrMsg": "device error"})
    elif fault == "substring":
        bbu["Command Status"]["Detailed Status"][0]["ErrMsg"] = "use /cx/cv after device error"
    elif fault == "serial":
        expected = json.loads(baseline.read_text())
        expected["drive_serials"]["252:7"] = "WRONG"
        baseline.write_text(json.dumps(expected))
    responses = {
        command: (json.dumps(payload), 255 if payload["Controllers"][0] is bbu else 0, "")
        for command, payload in payloads.items()
    }
    if fault == "malformed":
        responses["/c0/bbu show all J"] = ("not JSON", 255, "")
    elif fault == "stderr":
        responses["/c0/bbu show all J"] = (json.dumps({"Controllers": [bbu]}), 255, "device error")
    binary = scripted_storcli(responses)
    # Exercise the real runner and sudo argv without using system sudo.
    sudo = binary.parent / "sudo"
    sudo.write_text('#!/bin/sh\ntest "$1" = "-n" || exit 1\nshift\nexec "$@"\n')
    sudo.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(rollout, "STAGED", binary)
    runner = AsyncMock(wraps=run_storcli)
    monkeypatch.setattr(rollout, "run_storcli", runner)
    if fault:
        with pytest.raises((StorcliError, ValueError)):
            await rollout.probe(binary, baseline)
    else:
        result = await rollout.probe(binary, baseline)
        assert result["/c0/bbu show all J"]["unsupported"] is True
        assert rollout.parse_foreign_config(result["/c0/fall show all J"]).present is False
        assert runner.await_count == 25
        assert set(result) == {*rollout.READS, "_binary_identity"}
