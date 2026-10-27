"""Offline rollout tests: wrapper boundary fake, never a real StorCLI executable."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from megaraid_dashboard.storcli import StorcliError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rollout", ROOT / "scripts/storcli_rollout.py")
assert spec and spec.loader
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)
FIXTURES = ROOT / "tests/fixtures/storcli/redacted"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict, AsyncMock]:
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
    controller["Controllers"][0]["Command Status"]["CLI Version"] = "1.23.02 test"
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
    fake = AsyncMock(side_effect=lambda args, **kwargs: copy.deepcopy(payloads[" ".join(args)]))
    monkeypatch.setattr(rollout, "run_storcli", fake)
    monkeypatch.setattr(rollout, "verify_binary", lambda *args: None)
    expected = {
        "controller_serial": "SV00000001",
        "drive_serials": {f"252:{slot}": f"SERIAL{slot}" for slot in range(8)},
    }
    return payloads, expected, fake


async def test_all_reads(reads: tuple) -> None:
    _, expected, fake = reads
    result = await rollout.probe(rollout.STAGED, expected)
    assert set(result) == set(rollout.READS)
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
    result = await rollout.probe(rollout.STAGED, expected)
    assert all(result[command]["unsupported"] for command in rollout.OPTIONAL)


@pytest.mark.parametrize("fault", ["version", "controller", "inventory", "drive", "rebuild", "cv"])
async def test_identity_and_schema_failures(reads: tuple, fault: str) -> None:
    payloads, expected, _ = reads
    if fault == "version":
        payloads["/c0 show all J"]["Controllers"][0]["Command Status"]["CLI Version"] = "7.37"
    elif fault == "controller":
        expected["controller_serial"] = "WRONG"
    elif fault == "inventory":
        expected["drive_serials"]["252:7"] = "WRONG"
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
    target = tmp_path / "new" / "storcli64"
    source.write_bytes(b"legacy fixture")
    original.write_bytes(b"original fixture")
    monkeypatch.setattr(rollout, "SOURCE", source)
    monkeypatch.setattr(rollout, "ORIGINAL", original)
    monkeypatch.setattr(rollout, "STAGED", target)
    monkeypatch.setattr(rollout.os, "geteuid", lambda: 0)
    monkeypatch.setattr(rollout.os, "fchown", lambda *args: None)
    checks = []
    monkeypatch.setattr(rollout, "verify_binary", lambda *args: checks.append(args))
    real_lstat = Path.lstat

    def root_owned(path: Path):
        values = list(real_lstat(path))
        values[4] = 0
        values[0] &= ~0o022
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", root_owned)
    before = (source.read_bytes(), original.read_bytes())
    rollout.stage()
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
        await rollout.probe(rollout.STAGED, {})
    fake.assert_not_called()
