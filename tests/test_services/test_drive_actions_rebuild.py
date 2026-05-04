from __future__ import annotations

from typing import Any

import pytest

from megaraid_dashboard.services.drive_actions import (
    build_rebuild_status_command,
    parse_rebuild_status,
)
from megaraid_dashboard.storcli import StorcliCommandFailed, StorcliParseError


def test_build_rebuild_status_command() -> None:
    assert build_rebuild_status_command(2, 0) == [
        "/c0/e2/s0",
        "show",
        "rebuild",
        "J",
    ]


def test_parse_rebuild_status_in_progress() -> None:
    status = parse_rebuild_status(
        _payload(
            {
                "Drive /c0/e2/s0 - Rebuild Progress": [
                    {
                        "Progress%": "42%",
                        "State": "In progress",
                        "Estimated Time Left": "1234 Minutes",
                    }
                ]
            }
        )
    )

    assert status.percent_complete == 42
    assert status.state == "In progress"
    assert status.time_remaining_minutes == 1234


def test_parse_rebuild_status_complete() -> None:
    status = parse_rebuild_status(
        _payload(
            {
                "Drive /c0/e2/s0 - Rebuild Progress": [
                    {
                        "Progress%": "100%",
                        "State": "Complete",
                    }
                ]
            }
        )
    )

    assert status.percent_complete == 100
    assert status.state == "Complete"
    assert status.time_remaining_minutes is None


def test_parse_rebuild_status_not_in_progress() -> None:
    status = parse_rebuild_status(
        _payload(
            {
                "Drive /c0/e2/s0 - Rebuild Progress": [
                    {
                        "Progress%": "0%",
                        "State": "Not in progress",
                        "Estimated Time Left": "0 Minutes",
                    }
                ]
            }
        )
    )

    assert status.percent_complete == 0
    assert status.state == "Not in progress"
    assert status.time_remaining_minutes == 0


def test_parse_rebuild_status_converts_seconds_to_minutes() -> None:
    status = parse_rebuild_status(
        _payload(
            {
                "Drive /c0/e2/s0 - Rebuild Progress": [
                    {
                        "Progress%": "42%",
                        "State": "In progress",
                        "Estimated Time Left": "30 Seconds",
                    }
                ]
            }
        )
    )

    assert status.percent_complete == 42
    assert status.state == "In progress"
    assert status.time_remaining_minutes == 0


def test_parse_rebuild_status_rbld_at_zero_percent_is_in_progress() -> None:
    status = parse_rebuild_status(
        _payload(
            {
                "Drive /c0/e2/s0 - Rebuild Progress": [
                    {
                        "Progress%": "0%",
                        "State": "Rbld",
                        "Estimated Time Left": "1234 Minutes",
                    }
                ]
            }
        )
    )

    assert status.percent_complete == 0
    assert status.state == "In progress"
    assert status.time_remaining_minutes == 1234


def test_parse_rebuild_status_raises_on_malformed_input() -> None:
    with pytest.raises(StorcliParseError):
        parse_rebuild_status({"Controllers": [{"Response Data": {"Drive": [{"State": "Onln"}]}}]})


def test_parse_rebuild_status_raises_when_command_status_failed() -> None:
    with pytest.raises(StorcliCommandFailed, match="drive missing") as exc_info:
        parse_rebuild_status(
            {
                "Controllers": [
                    {
                        "Command Status": {
                            "Status": "Failure",
                            "Description": "None",
                            "Detailed Status": [{"ErrMsg": "drive missing"}],
                        },
                        "Response Data": {
                            "Drive /c0/e2/s0 - Rebuild Progress": [
                                {
                                    "Progress%": "100%",
                                    "State": "Complete",
                                }
                            ]
                        },
                    }
                ]
            }
        )

    assert exc_info.value.err_msg == "drive missing"


def _payload(response_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": response_data,
            }
        ]
    }
