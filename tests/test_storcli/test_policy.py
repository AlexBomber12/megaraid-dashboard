"""Evidence/authorization contract tests; synthetic policy, no hardware or sudo writes."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from megaraid_dashboard.storcli import policy
from megaraid_dashboard.storcli.exceptions import StorcliCommandFailed, StorcliNotAvailable
from megaraid_dashboard.storcli.runner import run_storcli
from tests.safety.scenarios import ACTIONS


def policy_data():
    commands = [" ".join(action.command) for action in ACTIONS if action.name != "mark-ubad"]
    commands.extend(
        [
            "/c0 set patrolread=on mode=manual J",
            "/c0 set patrolread=off J",
            "/c0 set cc=seq J",
            "/c0/v0 start cc J",
        ]
    )
    return {
        "controller_serial": "SV00000001",
        "virtual_drive": 0,
        "disk_group": 0,
        "replacements": [{"slot": 0, "dg": 0, "array": 0, "row": 0}],
        "commands": [
            {"command": command, "evidence": "SYNTHETIC TEST ONLY"} for command in commands
        ],
    }


def test_route_matrix_and_exact_policy():
    value = policy.CommandPolicy.model_validate(policy_data())
    for action in ACTIONS:
        if action.name == "mark-ubad":
            assert "Unavailable" in policy.action_refusal(value, action.path)
        else:
            assert policy.action_refusal(value, action.path) is None
            assert list(action.command) in [
                cmd.split() for cmd in policy.action_commands(value, action.path)
            ]
    assert policy.action_commands(value, "/unrecognized") == []
    assert policy.action_commands(value, "/drives/252:1/replace/insert") == []
    assert policy.action_refusal(value, "/drives/252:1/locate/start")
    assert (
        policy.action_refusal(value, "/controller/buzzer/enable", ["/c0", "set", "alarm=on"])
        is None
    )
    assert value.permits(["/c0", "set", "alarm=on"])
    assert not value.permits(["/c0", "set", "alarm=on", "J", "force"])
    text = value.sudoers(("/c0 show all J",))
    assert "megaraid ALL=(root) NOPASSWD:" in text
    assert "*" not in text
    assert "set bad" not in text
    for command in value.commands:
        assert f"{policy.PINNED_BINARY} {command.command}" in text


@pytest.mark.parametrize(
    "change",
    [
        {"service_user": "bad ALL=(root)"},
        {"replacements": [{"slot": 0, "dg": 0, "array": 0, "row": 0}] * 2},
        {"commands": [{"command": "/c0/e252/s0 set bad J", "evidence": "test"}]},
        {"commands": [{"command": "/c0 show all J", "evidence": "test"}]},
        {"commands": [{"command": "/c0 set alarm=on", "evidence": "test"}]},
        {"commands": [{"command": "/c0/e253/s0 start locate J", "evidence": "test"}]},
        {"commands": [{"command": "/c0/v1 start cc J", "evidence": "test"}]},
        {"commands": [{"command": "/c0/e252/s0 add hotsparedrive dgs=1 J", "evidence": "test"}]},
        {"commands": [{"command": "/c0/e252/s1 insert dg=0 array=0 row=0 J", "evidence": "test"}]},
        {"commands": [{"command": "/c0/e252/s0 insert dg=0 array=1 row=0 J", "evidence": "test"}]},
        {"replacements": [{"slot": 0, "dg": 1, "array": 0, "row": 0}]},
        {"commands": [{"command": "/c0 set alarm=on J", "evidence": "test"}] * 2},
    ],
)
def test_invalid_contract(change):
    with pytest.raises(ValidationError):
        policy.CommandPolicy.model_validate(policy_data() | change)


def test_protected_paths(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="absolute"):
        policy.protected_path(Path("relative"))
    with pytest.raises(ValueError, match="absolute"):
        policy.protected_path(Path("/tmp/../file"))
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    # Real temp paths must be rejected, regardless of the current user's uid.
    with pytest.raises(ValueError, match="root-controlled"):
        policy.protected_path(link)
    actual = Path.lstat

    def safe_lstat(path):
        values = list(actual(path))
        values[4] = 0
        values[0] &= ~0o022
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", safe_lstat)
    with pytest.raises(ValueError, match="regular"):
        policy.protected_path(tmp_path)
    file = tmp_path / "policy.json"
    file.write_text(json.dumps(policy_data()))
    policy.protected_path(file)
    assert policy.load_policy(str(file)).controller_serial == "SV00000001"
    with pytest.raises(ValueError, match="symlinks"):
        policy.protected_path(link)


def test_verify_binary(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="pinned"):
        policy.verify_pinned_binary("/wrong")
    file = tmp_path / "binary"
    file.write_bytes(b"test")
    monkeypatch.setattr(policy, "PINNED_BINARY", str(file))
    monkeypatch.setattr(policy, "protected_path", lambda path: None)
    with pytest.raises(ValueError, match="SHA-256"):
        policy.verify_pinned_binary(str(file))
    import hashlib

    monkeypatch.setattr(policy, "PINNED_SHA256", hashlib.sha256(b"test").hexdigest())
    policy.verify_pinned_binary(str(file))


@pytest.fixture
def approved(tmp_path, monkeypatch):
    file = tmp_path / "policy.json"
    file.write_text(json.dumps(policy_data()))
    monkeypatch.setattr(policy, "protected_path", lambda path: None)
    monkeypatch.setattr(policy, "verify_pinned_binary", lambda path: None)
    return str(file)


async def test_wrapper_refuses_unapproved_before_subprocess(approved):
    with pytest.raises(StorcliNotAvailable, match="refused before execution"):
        await run_storcli(
            ["/c0/e252/s1", "start", "locate"],
            use_sudo=False,
            binary_path="never-executed",
            authorization_path=approved,
        )


@pytest.mark.parametrize(
    "result",
    [
        {"Controllers": [{"Command Status": {"Controller": 0, "Status": "Success"}}]},
        {"Controllers": [{"Command Status": {"Controller": 1, "Status": "Success"}}]},
        {"Controllers": [{"Command Status": {"Controller": False, "Status": "Success"}}]},
        {"Controllers": [{"Command Status": {"Controller": 0.0, "Status": "Success"}}]},
        {"Controllers": [{"Command Status": {"Status": "Success"}}]},
    ],
)
async def test_wrapper_full_identity_and_result(approved, scripted_storcli, result):
    from megaraid_dashboard.storcli.exceptions import StorcliParseError

    fixture = Path(__file__).parents[1] / "fixtures/storcli/redacted/c0_show_all.json"
    binary = scripted_storcli(
        {
            "/c0 show all J": (fixture.read_text(), 0, ""),
            "/c0 set alarm=silence J": (json.dumps(result), 0, ""),
        }
    )
    kwargs = dict(use_sudo=False, binary_path=str(binary), authorization_path=approved)
    controller_id = result["Controllers"][0]["Command Status"].get("Controller")
    if type(controller_id) is int and controller_id == 0:
        assert await run_storcli(["/c0", "set", "alarm=silence"], **kwargs) == result
    else:
        with pytest.raises(StorcliParseError, match="uncertain"):
            await run_storcli(["/c0", "set", "alarm=silence"], **kwargs)


async def test_wrapper_wrong_controller_serial(approved, scripted_storcli):
    fixture = Path(__file__).parents[1] / "fixtures/storcli/redacted/c0_show_all.json"
    binary = scripted_storcli(
        {"/c0 show all J": (fixture.read_text().replace("SV00000001", "OTHER"), 0, "")}
    )
    with pytest.raises(StorcliCommandFailed, match="identity changed"):
        await run_storcli(
            ["/c0", "set", "alarm=on"],
            use_sudo=False,
            binary_path=str(binary),
            authorization_path=approved,
        )


@pytest.mark.parametrize(
    "change", ["model", "vd-missing", "vd-count", "vd-row", "vd-identity", "alarm"]
)
async def test_wrapper_refuses_unapproved_live_state(approved, scripted_storcli, change):
    fixture = Path(__file__).parents[1] / "fixtures/storcli/redacted/c0_show_all.json"
    payload = json.loads(fixture.read_text())
    data = payload["Controllers"][0]["Response Data"]
    if change == "model":
        data["Basics"]["Model"] = "Other controller"
    elif change == "vd-missing":
        data.pop("VD LIST")
    elif change == "vd-count":
        data["VD LIST"].append(data["VD LIST"][0])
    elif change == "vd-row":
        data["VD LIST"] = [None]
    elif change == "vd-identity":
        data["VD LIST"][0]["DG/VD"] = "0/1"
    else:
        data["HwCfg"]["Alarm"] = "Unknown"
    binary = scripted_storcli({"/c0 show all J": (json.dumps(payload), 0, "")})
    command = (
        ["/c0", "set", "cc=conc"] if change.startswith("vd-") else ["/c0", "set", "alarm=silence"]
    )
    with pytest.raises(StorcliCommandFailed, match=r"identity|topology|Buzzer"):
        await run_storcli(
            command,
            use_sudo=False,
            binary_path=str(binary),
            authorization_path=approved,
        )


async def test_stop_recovery_does_not_require_virtual_drive_topology(approved, scripted_storcli):
    fixture = Path(__file__).parents[1] / "fixtures/storcli/redacted/c0_show_all.json"
    payload = json.loads(fixture.read_text())
    payload["Controllers"][0]["Response Data"].pop("VD LIST")
    result = {"Controllers": [{"Command Status": {"Controller": 0, "Status": "Success"}}]}
    binary = scripted_storcli(
        {
            "/c0 show all J": (json.dumps(payload), 0, ""),
            "/c0/e252/s0 stop locate J": (json.dumps(result), 0, ""),
        }
    )
    assert (
        await run_storcli(
            ["/c0/e252/s0", "stop", "locate"],
            use_sudo=False,
            binary_path=str(binary),
            authorization_path=approved,
        )
        == result
    )


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"Controllers": []},
        {"Controllers": [{"Command Status": {"Controller": 0, "Status": "Success"}}] * 2},
        {
            "Controllers": [
                {
                    "Command Status": {
                        "Controller": 0,
                        "Status": "Success",
                        "Detailed Status": [{"Controller": 0, "Status": "Failure"}],
                    }
                }
            ]
        },
        {
            "Controllers": [
                {
                    "Command Status": {
                        "Controller": 0,
                        "Status": "Success",
                        "Detailed Status": [{"Ctrl": 1, "Status": "Success"}],
                    }
                }
            ]
        },
    ],
)
async def test_ambiguous_write_result_is_never_success(approved, scripted_storcli, result):
    from megaraid_dashboard.storcli.exceptions import StorcliParseError

    fixture = Path(__file__).parents[1] / "fixtures/storcli/redacted/c0_show_all.json"
    binary = scripted_storcli(
        {
            "/c0 show all J": (fixture.read_text(), 0, ""),
            "/c0 set alarm=silence J": (json.dumps(result), 0, ""),
        }
    )
    with pytest.raises(StorcliParseError, match="uncertain"):
        await run_storcli(
            ["/c0", "set", "alarm=silence"],
            use_sudo=False,
            binary_path=str(binary),
            authorization_path=approved,
        )


@pytest.mark.parametrize("absent", ["offline", "missing", "insert"])
@pytest.mark.parametrize("entry", ["offline", "missing", "insert"])
async def test_wrapper_rechecks_the_complete_replacement_contract(
    approved, absent, entry, monkeypatch
):
    import asyncio

    file = Path(approved)
    data = json.loads(file.read_text())
    token = f"set {absent}" if absent != "insert" else "insert dg="
    data["commands"] = [item for item in data["commands"] if token not in item["command"]]
    file.write_text(json.dumps(data))

    async def forbidden(*args, **kwargs):
        raise AssertionError("Incomplete replacement must never reach a subprocess")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    command = (
        ["/c0/e252/s0", "insert", "dg=0", "array=0", "row=0"]
        if entry == "insert"
        else ["/c0/e252/s0", "set", entry]
    )
    with pytest.raises(
        StorcliNotAvailable, match=f"{absent.title()} command or topology is not approved"
    ):
        await run_storcli(
            command, use_sudo=False, binary_path="never-executed", authorization_path=approved
        )
