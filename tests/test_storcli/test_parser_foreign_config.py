from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from megaraid_dashboard.storcli import StorcliCommandFailed, parse_foreign_config

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "storcli" / "redacted"


def load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_parse_foreign_config_present() -> None:
    foreign_config = parse_foreign_config(load_fixture("c0_fall_show_all_present.json"))

    assert foreign_config.present is True
    assert foreign_config.dg_count == 1
    assert foreign_config.drive_count == 4
    assert foreign_config.total_size_bytes is not None
    assert foreign_config.total_size_bytes > 0
    assert len(foreign_config.disk_groups) == 1
    only_dg = foreign_config.disk_groups[0]
    assert only_dg.dg_id == 0
    assert only_dg.drive_count == 4
    assert foreign_config.digest.startswith("FC-DG1-PD4-")
    assert "dg0:4" in foreign_config.digest


def test_parse_foreign_config_absent_marker() -> None:
    foreign_config = parse_foreign_config(load_fixture("c0_fall_show_all_absent.json"))

    assert foreign_config.present is False
    assert foreign_config.dg_count == 0
    assert foreign_config.drive_count == 0
    assert foreign_config.disk_groups == []
    assert foreign_config.digest == ""


def test_parse_foreign_config_unrelated_failure_raises() -> None:
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {
                    "Status": "Failure",
                    "Description": "Adapter failed",
                    "Detailed Status": [
                        {"Status": "Failure", "ErrCd": 99, "ErrMsg": "adapter offline"}
                    ],
                }
            }
        ]
    }
    with pytest.raises(StorcliCommandFailed):
        parse_foreign_config(payload)


def test_parse_foreign_config_empty_response_treated_as_absent() -> None:
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {},
            }
        ]
    }
    foreign_config = parse_foreign_config(payload)

    assert foreign_config.present is False
    assert foreign_config.dg_count == 0
    assert foreign_config.digest == ""


@pytest.mark.parametrize("response_data", [[], "unexpected", 0])
def test_parse_foreign_config_non_mapping_response_treated_as_absent(
    response_data: Any,
) -> None:
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": response_data,
            }
        ]
    }
    foreign_config = parse_foreign_config(payload)

    assert foreign_config.present is False
    assert foreign_config.dg_count == 0
    assert foreign_config.digest == ""


def test_parse_foreign_config_digest_is_stable() -> None:
    payload = load_fixture("c0_fall_show_all_present.json")
    first = parse_foreign_config(payload)
    second = parse_foreign_config(payload)

    assert first.digest == second.digest
