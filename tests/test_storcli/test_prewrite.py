"""Live prewrite evidence uses fake reads only; no hardware or privileged executable."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from megaraid_dashboard.storcli import StorcliCommandFailed
from megaraid_dashboard.storcli.policy import CommandPolicy
from megaraid_dashboard.storcli.prewrite import (
    _IdleRebuildProof,
    check_action_topology,
    check_foreign_safety,
)
from tests.test_storcli.test_policy import policy_data

FIXTURES = Path(__file__).parents[1] / "fixtures/storcli/redacted"


def payload(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    "defect",
    [
        "count",
        "no-drives",
        "duplicate-slot",
        "duplicate-id",
        "wrong-slot",
        "unknown-state",
        "active-state",
        "metadata",
        "read-failure",
        "malformed-rebuild",
        "not-available",
        "not-supported",
        "no-digest",
    ],
)
async def test_unproven_foreign_safety_refuses_with_next_action(defect):
    from megaraid_dashboard.storcli import parse_foreign_config

    identity = payload("c0_show_all.json")
    foreign = payload("c0_fall_show_all_present.json")
    data = identity["Controllers"][0]["Response Data"]
    if defect == "count":
        data["Physical Drives"] = 1
    elif defect == "no-drives":
        data.update({"Physical Drives": 0, "PD LIST": []})
    elif defect == "duplicate-slot":
        data["PD LIST"][1]["EID:Slt"] = data["PD LIST"][0]["EID:Slt"]
    elif defect == "duplicate-id":
        data["PD LIST"][1]["DID"] = data["PD LIST"][0]["DID"]
    elif defect == "wrong-slot":
        data["PD LIST"][0]["EID:Slt"] = "252:99"
    elif defect == "unknown-state":
        data["PD LIST"][0]["State"] = "Unknown"
    elif defect == "active-state":
        data["PD LIST"][0]["State"] = "Rbld"
    elif defect == "metadata":
        identity["Controllers"][0]["Command Status"]["Controller"] = 1
    calls = []

    async def read(args):
        calls.append(args)
        if defect == "read-failure":
            raise StorcliCommandFailed("isolated denied read")
        if args[0] == "/c0/fall":
            return foreign
        result = payload("123_rebuild_idle.json")
        result["Controllers"][0]["Response Data"][0]["Drive-ID"] = args[0]
        if defect == "malformed-rebuild":
            result["Controllers"][0]["Response Data"] = {"unknown": []}
        if defect in {"not-available", "not-supported"}:
            result["Controllers"][0]["Response Data"][0]["Status"] = defect.replace("-", " ")
        return result

    with pytest.raises(StorcliCommandFailed, match=r"Refresh|Reload"):
        await check_foreign_safety(
            identity,
            CommandPolicy.model_validate(policy_data()),
            read,
            None if defect == "no-digest" else parse_foreign_config(foreign).digest,
        )
    assert all("show" in args for args in calls)


@pytest.mark.parametrize("named,complete", [(False, False), (True, False), (False, True)])
async def test_explicit_idle_or_completed_rebuild_proof(named, complete):
    from megaraid_dashboard.storcli import parse_foreign_config

    identity = payload("c0_show_all.json")
    foreign = payload("c0_fall_show_all_present.json")
    calls = []

    async def read(args):
        calls.append(args)
        if args == ["/c0", "show", "all", "J"]:
            return copy.deepcopy(identity)
        if args[0] == "/c0/fall":
            return foreign
        result = payload("123_rebuild_idle.json")
        row = result["Controllers"][0]["Response Data"][0]
        row["Drive-ID"] = args[0]
        if complete:
            row.update({"Status": "Completed", "Progress%": 100})
        if named:
            result["Controllers"][0]["Response Data"] = {
                f"Drive {args[0]} - Rebuild Progress": [row]
            }
        return result

    await check_foreign_safety(
        identity,
        CommandPolicy.model_validate(policy_data()),
        read,
        parse_foreign_config(foreign).digest,
    )
    assert len(calls) == 11
    assert all("show" in args for args in calls)


@pytest.mark.parametrize("value", [None, False, 0, "Not available", "Not supported", "Unknown"])
def test_idle_proof_does_not_accept_unknown_or_non_string_status(value):
    with pytest.raises(ValidationError):
        _IdleRebuildProof.model_validate({"Status": value})


@pytest.mark.parametrize(
    "defect",
    ["duplicate-vd", "count-vd", "group-count", "duplicate-group", "missing-group", "group-schema"],
)
def test_topology_evidence_must_match_the_specific_target(defect):
    identity = payload("c0_show_all.json")
    data = identity["Controllers"][0]["Response Data"]
    if defect == "duplicate-vd":
        data["Virtual Drives"] = 2
        data["VD LIST"] *= 2
    elif defect == "count-vd":
        data["Virtual Drives"] = 2
    elif defect == "group-count":
        data["Drive Groups"] = 2
    elif defect == "duplicate-group":
        data["TOPOLOGY"].append(data["TOPOLOGY"][0])
        data["Drive Groups"] = 2
    elif defect == "missing-group":
        data["TOPOLOGY"][0]["DG"] = 1
    else:
        data.pop("TOPOLOGY")
    command = (
        ["/c0/v0", "start", "cc", "J"]
        if defect.endswith("vd")
        else ["/c0/e252/s0", "add", "hotsparedrive", "dgs=0", "J"]
    )
    with pytest.raises(StorcliCommandFailed, match="Refresh current controller topology"):
        check_action_topology(command, identity, CommandPolicy.model_validate(policy_data()))
