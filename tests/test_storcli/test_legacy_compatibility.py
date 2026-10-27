"""Redacted reconstructions of operator-supplied observations, never hardware calls."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import structlog

from megaraid_dashboard.services.collector import collect_storcli_snapshot
from megaraid_dashboard.services.drive_actions import (
    parse_consistency_check_status,
    parse_patrol_read_status,
    parse_rebuild_status,
)
from megaraid_dashboard.storcli import StorcliCommandFailed, StorcliParseError, parse_cachevault
from megaraid_dashboard.storcli.models import CacheVault
from tests.test_services.test_collector import _settings

FIXTURES = Path(__file__).parents[1] / "fixtures/storcli/redacted"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_legacy_idle_rebuild_is_unknown_progress() -> None:
    status = parse_rebuild_status(load("123_rebuild_idle.json"), requested_drive="/c0/e252/s0")
    assert status.percent_complete is None
    assert status.time_remaining_minutes is None
    assert status.state == "Not in progress"


@pytest.mark.parametrize(
    "response",
    [
        [],
        [{}, {}],
        ["bad"],
        {"unexpected": []},
        {"a": [], "b": []},
        [{"Drive-ID": "/c0/e252/s1", "Progress%": "100%"}],
        [{"Drive-ID": 42, "Progress%": "100%"}],
        [{"Drive-ID": "invalid", "Progress%": "100%"}],
        [{"Status": 3}],
        [{"Progress%": "-"}],
        [{"Progress%": "101"}],
        [{"Progress%": True}],
        [{"Progress%": "broken"}],
        {"Drive /c0/e252/s0 - Rebuild Progress": [{"Drive-ID": "/c0/e252/s1"}]},
        {"Drive /c0/e252/s1 - Rebuild Progress": [{"Progress%": 100}]},
    ],
)
def test_rebuild_rejects_ambiguous_mismatched_or_malformed_rows(response: Any) -> None:
    payload = load("123_rebuild_idle.json")
    payload["Controllers"][0]["Response Data"] = response
    with pytest.raises(StorcliParseError):
        parse_rebuild_status(payload, requested_drive="/c0/e252/s0")


@pytest.mark.parametrize("state", ["Not in progress", "Complete", "In progress"])
def test_missing_rebuild_progress_never_proves_completion(state: str) -> None:
    payload = load("123_rebuild_idle.json")
    row = payload["Controllers"][0]["Response Data"][0]
    del row["Progress%"]
    row["Status"] = state
    result = parse_rebuild_status(payload)
    assert result.percent_complete is None
    assert result.state != "Complete"


def test_rebuild_failed_command_cannot_report_completion() -> None:
    payload = load("123_rebuild_idle.json")
    payload["Controllers"][0]["Command Status"]["Status"] = "Failure"
    with pytest.raises(StorcliCommandFailed):
        parse_rebuild_status(payload)


def test_legacy_cc_and_new_patrol_failure() -> None:
    result = parse_consistency_check_status(load("123_cc_show.json"), load("123_cc_progress.json"))
    assert result.mode == "concurrent"
    assert result.state == "stopped"
    assert result.progress_percent is None
    with pytest.raises(StorcliCommandFailed, match="Un-supported command"):
        parse_patrol_read_status(load("737_patrol_unsupported.json"))


def test_invalid_optional_learn_date_warns_and_preserves_input() -> None:
    payload = load("123_cv_invalid_learn.json")
    original = copy.deepcopy(payload)
    with structlog.testing.capture_logs() as logs:
        cv = parse_cachevault(payload)
    assert cv is not None
    assert cv.next_learn_cycle is None
    assert (cv.state, cv.replacement_required, cv.temperature_celsius, cv.capacitance_percent) == (
        "Optimal",
        False,
        29,
        89,
    )
    assert any(event["event"] == "cachevault_next_learn_time_invalid" for event in logs)
    assert payload == original
    assert CacheVault.parse_next_learn_cycle("2026/11/07 17:50:43 (847389043 seconds)") == datetime(
        2026, 11, 7, 17, 50, 43
    )
    assert CacheVault.parse_next_learn_cycle({"invalid": "type"}) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("State", ""),
        ("State", []),
        ("Replacement required", "garbage"),
        ("Replacement required", None),
        ("Temperature", "bad"),
        ("Temperature", True),
        ("Capacitance", "bad"),
        ("Capacitance", "101 %"),
        ("Capacitance", True),
    ],
)
def test_bad_learn_date_does_not_mask_invalid_health(field: str, value: Any) -> None:
    payload = load("123_cv_invalid_learn.json")
    for rows in payload["Controllers"][0]["Response Data"].values():
        for row in rows:
            if row.get("Property") == field:
                row["Value"] = value
    with pytest.raises(StorcliParseError):
        parse_cachevault(payload)


async def test_collection_continues_with_raw_invalid_date(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = {
        "/c0 show all": load("c0_show_all.json"),
        "/c0/vall show all": load("vall_show_all.json"),
        "/c0/eall/sall show all": load("eall_sall_show_all.json"),
        "/c0/cv show all": load("123_cv_invalid_learn.json"),
        "/c0/bbu show all": load("bbu_show_all.json"),
        "/c0/fall show all": load("c0_fall_show_all_absent.json"),
        "/c0 show patrolread": load("737_patrol_unsupported.json"),
        "/c0 show cc": load("123_cc_show.json"),
        "/c0/vall show cc": load("123_cc_progress.json"),
    }
    calls = []

    async def fake_run(args: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(" ".join(args))
        return payloads[calls[-1]]

    monkeypatch.setattr("megaraid_dashboard.services.collector.run_storcli", fake_run)
    snapshot, raw = await collect_storcli_snapshot(settings=_settings())
    assert len(calls) == 9
    assert snapshot.cachevault is not None
    assert snapshot.cachevault.next_learn_cycle is None
    assert raw["cachevault"] == payloads["/c0/cv show all"]
    assert raw["patrol_read"] is None
    assert raw["consistency_check"]["mode"] == "concurrent"


def test_undocumented_and_mismatched_commands_cannot_reach_subprocess() -> None:
    from megaraid_dashboard.storcli.runner import _validate_command

    for command in (
        ["/c0", "set", "consistencycheck=on", "mode=auto", "J"],
        ["/c0", "set", "consistencycheck=on", "mode=manual", "J"],
        ["/c0/e252/s0", "set", "bad", "J"],
    ):
        with pytest.raises(StorcliCommandFailed, match="not allowed"):
            _validate_command(command)
    for mode in ("conc", "seq"):
        _validate_command(["/c0", "set", f"cc={mode}", "J"])


@pytest.mark.parametrize("field", ["State", "Replacement required", "Temperature"])
def test_missing_required_cachevault_health_is_rejected(field: str) -> None:
    payload = load("123_cv_invalid_learn.json")
    response = payload["Controllers"][0]["Response Data"]
    for name, rows in response.items():
        response[name] = [row for row in rows if row["Property"] != field]
    with pytest.raises(StorcliParseError):
        parse_cachevault(payload)


@pytest.mark.parametrize("status", [None, {}, {"Status": "Unknown"}])
def test_unknown_command_status_cannot_prove_rebuild_completion(status: Any) -> None:
    payload = load("123_rebuild_idle.json")
    controller = payload["Controllers"][0]
    controller["Command Status"] = status
    controller["Response Data"][0].update({"Progress%": "100", "Status": "Complete"})
    with pytest.raises(StorcliParseError):
        parse_rebuild_status(payload)


@pytest.mark.parametrize("state", ["Not in progress", "Failed", "Aborted", "Stopped"])
def test_terminal_unsuccessful_status_overrides_percent(state: str) -> None:
    payload = load("123_rebuild_idle.json")
    payload["Controllers"][0]["Response Data"][0].update({"Status": state, "Progress%": "100"})
    assert parse_rebuild_status(payload).state != "Complete"


@pytest.mark.parametrize("value", [None, "N/A", "-"])
def test_explicit_unknown_capacitance_stays_unknown(value: Any) -> None:
    assert CacheVault.parse_capacitance_percent(value) is None


@pytest.mark.parametrize("value", [True, False])
def test_model_replacement_boolean_is_preserved(value: bool) -> None:
    assert CacheVault.parse_replacement_required(value) is value


@pytest.mark.parametrize("state", ["-", "N/A"])
def test_unknown_cachevault_state_is_not_healthy(state: str) -> None:
    with pytest.raises(ValueError):
        CacheVault.validate_health_text(state)


def test_matching_rebuild_table_and_drive_id() -> None:
    payload = load("123_rebuild_idle.json")
    controller = payload["Controllers"][0]
    rows = controller["Response Data"]
    controller["Response Data"] = {"Drive /c0/e252/s0 - Rebuild Progress": rows}
    assert parse_rebuild_status(payload, requested_drive="/c0/e252/s0").percent_complete is None
    controller["Response Data"] = {"Drive /c0/e252/s0 - Rebuild Progress": {}}
    with pytest.raises(StorcliParseError):
        parse_rebuild_status(payload)
