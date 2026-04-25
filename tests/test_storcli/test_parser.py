from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from megaraid_dashboard.storcli import (
    StorcliCommandFailed,
    parse_bbu,
    parse_cachevault,
    parse_controller_show_all,
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


def test_parse_bbu_raises_on_unexpected_failure() -> None:
    with pytest.raises(StorcliCommandFailed, match="controller busy"):
        parse_bbu(unexpected_failure_payload("controller busy"))


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
