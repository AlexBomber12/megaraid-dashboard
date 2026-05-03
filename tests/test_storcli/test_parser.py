from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from megaraid_dashboard.storcli import (
    BbuInfo,
    StorcliCommandFailed,
    StorcliParseError,
    parse_bbu,
    parse_cachevault,
    parse_controller_show_all,
    parse_drive_state,
    parse_physical_drives,
    parse_virtual_drives,
)

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "storcli" / "redacted"


def load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_parse_controller_show_all() -> None:
    controller = parse_controller_show_all(load_fixture("c0_show_all.json"))

    assert "9270CV" in controller.model_name
    assert controller.serial_number == "SV00000001"
    assert controller.bbu_present is True


def test_parse_controller_show_all_with_roc_temperature() -> None:
    controller = parse_controller_show_all(load_fixture("controller_show_all_with_roc.json"))

    assert controller.roc_temperature_celsius == 78


def test_parse_controller_show_all_without_roc_temperature() -> None:
    controller = parse_controller_show_all(load_fixture("controller_show_all_no_roc.json"))

    assert controller.roc_temperature_celsius is None


def test_parse_controller_show_all_with_roc_temperature_na() -> None:
    controller = parse_controller_show_all(load_fixture("controller_show_all_roc_na.json"))

    assert controller.roc_temperature_celsius is None


def test_parse_controller_show_all_raises_for_structural_roc_temperature() -> None:
    payload = deepcopy(load_fixture("controller_show_all_with_roc.json"))
    hwcfg = payload["Controllers"][0]["Response Data"]["HwCfg"]
    hwcfg["ROC temperature(Degree Celsius)"] = {"value": 78}

    with pytest.raises(StorcliParseError):
        parse_controller_show_all(payload)


def test_parse_virtual_drives() -> None:
    virtual_drives = parse_virtual_drives(load_fixture("vall_show_all.json"))

    assert len(virtual_drives) == 1
    virtual_drive = virtual_drives[0]
    assert virtual_drive.raid_level == "RAID5"
    assert virtual_drive.state == "Optl"
    assert abs(virtual_drive.size_bytes - int(19.099 * 10**12)) < int(0.02 * 19.099 * 10**12)


def test_parse_physical_drives() -> None:
    physical_drives = parse_physical_drives(load_fixture("eall_sall_show_all.json"))

    assert len(physical_drives) == 8
    for drive in physical_drives:
        assert drive.state == "Onln"
        assert drive.media_errors == 0
        assert drive.predictive_failures == 0
        assert drive.smart_alert is False
        assert isinstance(drive.temperature_celsius, int)
        assert 30 <= drive.temperature_celsius <= 70


def test_parse_cachevault_success() -> None:
    cachevault = parse_cachevault(load_fixture("cv_show_all.json"))

    assert cachevault is not None
    assert cachevault.state
    assert cachevault.capacitance_percent == 89


def test_parse_cachevault_from_bbu_failure_returns_none() -> None:
    assert parse_cachevault(load_fixture("bbu_show_all.json")) is None


def test_parse_cachevault_raises_on_unexpected_failure() -> None:
    with pytest.raises(StorcliCommandFailed, match="firmware fault"):
        parse_cachevault(unexpected_failure_payload("firmware fault"))


@pytest.mark.parametrize(
    "err_msg",
    [
        "cachevault module not present",
        "cachevault query not supported by firmware",
        "controller path does not exist",
    ],
)
def test_parse_cachevault_raises_on_generic_failure_markers(err_msg: str) -> None:
    with pytest.raises(StorcliCommandFailed, match=err_msg):
        parse_cachevault(unexpected_failure_payload(err_msg))


def test_parse_bbu_failure_returns_none() -> None:
    assert parse_bbu(load_fixture("bbu_show_all.json")) is None


def test_parse_bbu_success_validates_response_data() -> None:
    bbu = parse_bbu(success_payload({"BBU_Info": [{"State": "Optimal"}]}))

    assert isinstance(bbu, BbuInfo)
    assert bbu.response_data == {"BBU_Info": [{"State": "Optimal"}]}


def test_parse_bbu_raises_on_unexpected_failure() -> None:
    with pytest.raises(StorcliCommandFailed, match="controller busy"):
        parse_bbu(unexpected_failure_payload("controller busy"))


@pytest.mark.parametrize(
    "parser",
    [
        parse_controller_show_all,
        parse_virtual_drives,
        parse_physical_drives,
        parse_cachevault,
        parse_bbu,
    ],
)
def test_successful_parsers_wrap_malformed_response_data(
    parser: Callable[[dict[str, Any]], object],
) -> None:
    with pytest.raises(StorcliParseError):
        parser(success_payload(None))


@pytest.mark.parametrize("state", ["Onln", "Offln", "Failed", "UBad", "UGood", "Rbld"])
def test_parse_drive_state_returns_summary_state(state: str) -> None:
    payload = success_payload(
        {
            "Drive /c0/e2/s0": [
                {
                    "EID:Slt": "2:0",
                    "DID": 14,
                    "State": state,
                    "Intf": "SATA",
                }
            ],
        }
    )

    assert parse_drive_state(payload) == state


def test_parse_drive_state_raises_on_failure() -> None:
    with pytest.raises(StorcliCommandFailed, match="device removed"):
        parse_drive_state(unexpected_failure_payload("device removed"))


def test_parse_drive_state_raises_when_state_field_missing() -> None:
    payload = success_payload(
        {
            "Drive /c0/e2/s0": [
                {
                    "EID:Slt": "2:0",
                    "DID": 14,
                    "Intf": "SATA",
                }
            ],
        }
    )

    with pytest.raises(StorcliParseError, match="State"):
        parse_drive_state(payload)


def success_payload(response_data: Any) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {
                    "Status": "Success",
                    "Description": "None",
                },
                "Response Data": response_data,
            }
        ]
    }


def unexpected_failure_payload(err_msg: str) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {
                    "Status": "Failure",
                    "Description": "None",
                    "Detailed Status": [{"ErrMsg": err_msg}],
                }
            }
        ]
    }
