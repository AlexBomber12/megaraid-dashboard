"""Coverage-focused tests for ``services/drive_actions``.

These tests target previously uncovered lines and branches so that the module
reaches 100% line and (near-)100% branch coverage. Each test maps to a specific
missed line or partial branch reported by ``coverage --branch``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from megaraid_dashboard.services import drive_actions
from megaraid_dashboard.services.drive_actions import (
    ConsistencyCheckStatus,
    PatrolReadStatus,
    ReplaceStep,
    _command_error_message,
    _find_consistency_check_inconsistency,
    _find_consistency_check_operation_text,
    _find_consistency_check_progress_percent,
    _find_consistency_check_text,
    _find_patrol_read_progress_percent,
    _find_patrol_read_text,
    _is_negative_consistency_check_inconsistency_detail,
    _is_patrol_read_progress_key,
    _normalize_patrol_read_state,
    _normalize_rebuild_state,
    _parse_consistency_check_progress_candidate,
    _parse_int,
    _parse_minutes,
    _single_controller_response_data,
    build_consistency_check_start_command,
    can_transition,
    consistency_check_can_stop,
    parse_consistency_check_status,
    parse_patrol_read_status,
    parse_rebuild_status,
)
from megaraid_dashboard.storcli import StorcliCommandFailed, StorcliParseError

# ---------------------------------------------------------------------------
# Line 171: can_transition with an unknown requested step
# ---------------------------------------------------------------------------


def test_can_transition_unknown_step_returns_false() -> None:
    assert can_transition("Onln", cast(ReplaceStep, "delete")) is False


# ---------------------------------------------------------------------------
# Line 219: ConsistencyCheckStatus.is_running getter
# ---------------------------------------------------------------------------


def test_consistency_check_status_is_running_true_when_active() -> None:
    status = ConsistencyCheckStatus(
        mode="manual",
        state="active",
        progress_percent=50,
        last_run_timestamp=None,
        inconsistency_count=None,
        inconsistency_detail=None,
    )

    assert status.is_running is True


def test_consistency_check_status_is_running_false_when_stopped() -> None:
    status = ConsistencyCheckStatus(
        mode="manual",
        state="stopped",
        progress_percent=None,
        last_run_timestamp=None,
        inconsistency_count=None,
        inconsistency_detail=None,
    )

    assert status.is_running is False


# ---------------------------------------------------------------------------
# Line 244: parse_rebuild_status fills resolved_percent when state == "Complete"
# ---------------------------------------------------------------------------


def test_parse_rebuild_status_complete_state_without_percent() -> None:
    status = parse_rebuild_status(
        {
            "Controllers": [
                {
                    "Command Status": {"Status": "Success"},
                    "Response Data": {
                        "Drive Rebuild": [{"State": "Complete"}],
                    },
                }
            ]
        }
    )

    assert status.state == "Complete"
    assert status.percent_complete == 100


# ---------------------------------------------------------------------------
# Branch 293->295: parse_consistency_check_status with state_raw is None
# ---------------------------------------------------------------------------


def test_parse_consistency_check_status_with_no_state_information() -> None:
    show_payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "Controller Properties": [
                        {"Ctrl_Prop": "CC Mode", "Value": "Manual"},
                    ]
                },
            }
        ]
    }
    progress_payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {},
            }
        ]
    }

    status = parse_consistency_check_status(show_payload, progress_payload)

    assert status.state == "unknown"
    assert status.progress_percent is None


# ---------------------------------------------------------------------------
# Line 327: consistency_check_can_stop
# ---------------------------------------------------------------------------


def test_consistency_check_can_stop_when_running() -> None:
    status = ConsistencyCheckStatus(
        mode="manual",
        state="active",
        progress_percent=42,
        last_run_timestamp=None,
        inconsistency_count=None,
        inconsistency_detail=None,
    )

    assert consistency_check_can_stop(status) is True


def test_consistency_check_can_stop_when_stopped() -> None:
    status = ConsistencyCheckStatus(
        mode="manual",
        state="stopped",
        progress_percent=None,
        last_run_timestamp=None,
        inconsistency_count=None,
        inconsistency_detail=None,
    )

    assert consistency_check_can_stop(status) is False


# ---------------------------------------------------------------------------
# Lines 333, 336, 346: _single_controller_response_data error paths
# ---------------------------------------------------------------------------


def test_single_controller_response_data_missing_controllers() -> None:
    with pytest.raises(StorcliParseError, match="missing Controllers"):
        _single_controller_response_data({})


def test_single_controller_response_data_empty_controllers() -> None:
    with pytest.raises(StorcliParseError, match="missing Controllers"):
        _single_controller_response_data({"Controllers": []})


def test_single_controller_response_data_controllers_not_list() -> None:
    with pytest.raises(StorcliParseError, match="missing Controllers"):
        _single_controller_response_data({"Controllers": "nope"})


def test_single_controller_response_data_controller_not_object() -> None:
    with pytest.raises(StorcliParseError, match="controller is not an object"):
        _single_controller_response_data({"Controllers": ["not-a-dict"]})


def test_single_controller_response_data_missing_response_data() -> None:
    with pytest.raises(StorcliParseError, match="missing Response Data"):
        _single_controller_response_data(
            {
                "Controllers": [
                    {"Command Status": {"Status": "Success"}},
                ]
            }
        )


# ---------------------------------------------------------------------------
# Line 356 & branch 354->353: _command_error_message fallback paths
# ---------------------------------------------------------------------------


def test_command_error_message_falls_back_to_description() -> None:
    message = _command_error_message(
        {
            "Detailed Status": [{"Other": "no errmsg"}],
            "Description": "fallback description",
            "Status": "Failure",
        }
    )

    assert message == "fallback description"


def test_command_error_message_falls_back_to_status() -> None:
    message = _command_error_message(
        {
            "Detailed Status": [],
            "Status": "Failure",
        }
    )

    assert message == "Failure"


def test_command_error_message_falls_back_to_default() -> None:
    message = _command_error_message({"Detailed Status": "not a list"})

    assert message == "unknown error"


def test_command_error_message_skips_non_dict_detailed_items() -> None:
    # A non-dict item exercises the False branch of ``isinstance(item, dict)``
    # at line 354, falling through to line 356.
    message = _command_error_message(
        {
            "Detailed Status": ["raw string"],
            "Status": "Failure",
        }
    )

    assert message == "Failure"


# ---------------------------------------------------------------------------
# Lines 377, 385, 389: progress-percent fallback walks
# ---------------------------------------------------------------------------


def test_find_patrol_read_progress_percent_from_plain_key_values() -> None:
    # No Ctrl_Prop entries; the storcli-properties walk yields nothing, so the
    # fallback ``_walk_key_values`` walk on line 374-377 is exercised.
    percent = _find_patrol_read_progress_percent({"PR Progress": "73%"})

    assert percent == 73


def test_find_consistency_check_progress_percent_from_ctrl_prop() -> None:
    payload = {
        "Controller Properties": [
            {"Ctrl_Prop": "CC Progress", "Value": "55%"},
        ]
    }

    percent = _find_consistency_check_progress_percent(payload)

    assert percent == 55


def test_find_consistency_check_progress_percent_from_plain_key_values() -> None:
    payload = {"CC Progress": "33%"}

    percent = _find_consistency_check_progress_percent(payload)

    assert percent == 33


# ---------------------------------------------------------------------------
# Lines 402, 405-410: _parse_consistency_check_progress_candidate branches
# ---------------------------------------------------------------------------


def test_parse_consistency_check_progress_candidate_rejects_rate() -> None:
    assert _parse_consistency_check_progress_candidate("CC Rate", "10%") is None


def test_parse_consistency_check_progress_candidate_accepts_consistency_key() -> None:
    assert _parse_consistency_check_progress_candidate("consistency progress", "42%") == 42


def test_parse_consistency_check_progress_candidate_accepts_cc_word_boundary() -> None:
    assert _parse_consistency_check_progress_candidate("CC Progress", "17%") == 17


def test_parse_consistency_check_progress_candidate_returns_none_for_unrelated_key() -> None:
    # ``progress`` present but no consistency/cc word: line 410 fall-through.
    assert _parse_consistency_check_progress_candidate("Patrol Progress", "21%") is None


def test_parse_consistency_check_progress_candidate_returns_none_for_unrelated_metric() -> None:
    # No ``progress`` / ``percentcomplete`` token → early return on line 404.
    assert _parse_consistency_check_progress_candidate("CC Foo", "5%") is None


# ---------------------------------------------------------------------------
# Lines 417, 421: _is_patrol_read_progress_key branches
# ---------------------------------------------------------------------------


def test_is_patrol_read_progress_key_rejects_rate_key() -> None:
    assert _is_patrol_read_progress_key("PR Rate") is False


def test_is_patrol_read_progress_key_accepts_patrol_progress() -> None:
    assert _is_patrol_read_progress_key("Patrol Read Progress") is True


def test_is_patrol_read_progress_key_accepts_pr_word_boundary() -> None:
    assert _is_patrol_read_progress_key("PR Progress") is True


def test_is_patrol_read_progress_key_rejects_unrelated() -> None:
    assert _is_patrol_read_progress_key("CC Progress") is False


def test_is_patrol_read_progress_key_rejects_no_progress_token() -> None:
    assert _is_patrol_read_progress_key("PR Mode") is False


# ---------------------------------------------------------------------------
# Branch 458->454 & lines 460-466: _find_patrol_read_text fallback walk
# ---------------------------------------------------------------------------


def test_find_patrol_read_text_falls_back_to_plain_keys() -> None:
    # No Ctrl_Prop entries → storcli-property walk finds nothing and we drop
    # into the plain ``_walk_key_values`` loop on lines 460-466.
    assert _find_patrol_read_text({"Current State": "Active"}, ("current state",)) == "Active"


def test_find_patrol_read_text_skips_non_matching_ctrl_prop() -> None:
    # ``Ctrl_Prop`` entries that don't match the hints exercise the False
    # branch of the condition at line 456 (branch 458->454).
    payload = {
        "Controller Properties": [
            {"Ctrl_Prop": "Some Other Prop", "Value": "value"},
            {"Ctrl_Prop": "PR Mode", "Value": "Auto"},
        ]
    }

    assert _find_patrol_read_text(payload, ("mode",)) == "Auto"


def test_find_patrol_read_text_skips_blank_ctrl_prop_value() -> None:
    payload = {
        "Controller Properties": [
            {"Ctrl_Prop": "PR Mode", "Value": "   "},
        ]
    }

    assert _find_patrol_read_text(payload, ("mode",)) is None


def test_find_patrol_read_text_skips_blank_plain_value() -> None:
    payload = {"Current State": "   "}

    assert _find_patrol_read_text(payload, ("current state",)) is None


def test_find_patrol_read_text_no_match_returns_none() -> None:
    assert _find_patrol_read_text({}, ("mode",)) is None


# ---------------------------------------------------------------------------
# Branch 483->474, lines 485-496: _find_consistency_check_text fallback walks
# ---------------------------------------------------------------------------


def test_find_consistency_check_text_from_ctrl_prop() -> None:
    payload = {
        "Controller Properties": [
            {"Ctrl_Prop": "CC Mode", "Value": "Manual"},
        ]
    }

    assert _find_consistency_check_text(payload, ("mode",)) == "Manual"


def test_find_consistency_check_text_skips_non_matching_ctrl_prop() -> None:
    payload = {
        "Controller Properties": [
            {"Ctrl_Prop": "PR Mode", "Value": "Auto"},
            {"Ctrl_Prop": "CC Mode", "Value": "Manual"},
        ]
    }

    assert _find_consistency_check_text(payload, ("mode",)) == "Manual"


def test_find_consistency_check_text_skips_blank_ctrl_prop_value() -> None:
    payload = {
        "Controller Properties": [
            {"Ctrl_Prop": "CC Mode", "Value": "   "},
        ]
    }

    assert _find_consistency_check_text(payload, ("mode",)) is None


def test_find_consistency_check_text_falls_back_to_plain_keys() -> None:
    payload = {"CC Mode": "Manual"}

    assert _find_consistency_check_text(payload, ("mode",)) == "Manual"


def test_find_consistency_check_text_plain_keys_skip_non_matching() -> None:
    payload = {
        "PR Mode": "Auto",
        "CC Mode": "Manual",
    }

    assert _find_consistency_check_text(payload, ("mode",)) == "Manual"


def test_find_consistency_check_text_plain_key_blank_value() -> None:
    assert _find_consistency_check_text({"CC Mode": "   "}, ("mode",)) is None


def test_find_consistency_check_text_no_match_returns_none() -> None:
    assert _find_consistency_check_text({}, ("mode",)) is None


# ---------------------------------------------------------------------------
# Branch 503->511, branch 509->503: _find_consistency_check_operation_text edges
# ---------------------------------------------------------------------------


def test_find_consistency_check_operation_text_ignores_non_cc_operation() -> None:
    # Operation is a string but not CC/consistency_check → fall through to the
    # nested recursion at line 511 (branch 503->511).
    payload = {
        "Operation": "Rebuild",
        "State": "In progress",
    }

    assert _find_consistency_check_operation_text(payload, ("state",)) is None


def test_find_consistency_check_operation_text_skips_blank_direct_value() -> None:
    # Operation matches but the candidate text strips to empty → continues the
    # inner loop (branch 509->503) and falls through to the nested recursion
    # on line 511 (branch 503->511).
    payload = {
        "Operation": "CC",
        "State": "   ",
    }

    assert _find_consistency_check_operation_text(payload, ("state",)) is None


def test_find_consistency_check_operation_text_no_matching_key() -> None:
    # Operation matches but no key satisfies the hint → inner for loop on line
    # 503 completes naturally (branch 503->511).
    payload = {
        "Operation": "CC",
        "Other": "non matching",
    }

    assert _find_consistency_check_operation_text(payload, ("state",)) is None


def test_find_consistency_check_operation_text_skips_non_string_values() -> None:
    payload = {
        "Operation": "CC",
        "Status": 42,
        "State": "Active",
    }

    assert _find_consistency_check_operation_text(payload, ("state",)) == "Active"


def test_find_consistency_check_operation_text_returns_first_match() -> None:
    payload = {
        "Operation": "CC",
        "Status": "Active",
    }

    assert _find_consistency_check_operation_text(payload, ("status",)) == "Active"


def test_find_consistency_check_operation_text_handles_list_payload() -> None:
    payload = [{"Operation": "CC", "State": "Active"}]

    assert _find_consistency_check_operation_text(payload, ("state",)) == "Active"


def test_find_consistency_check_operation_text_returns_none_for_other_types() -> None:
    assert _find_consistency_check_operation_text("scalar", ("state",)) is None


# ---------------------------------------------------------------------------
# Line 536, 550->554, 553, 560:
# _find_consistency_check_inconsistency and _is_negative_*
# ---------------------------------------------------------------------------


def test_find_consistency_check_inconsistency_from_plain_keys() -> None:
    # No Ctrl_Prop entries → fallback ``_walk_key_values`` returns the match
    # on line 536.
    count, detail = _find_consistency_check_inconsistency({"Inconsistencies": "4"})

    assert count == 4
    assert detail == "4"


def test_find_consistency_check_inconsistency_non_string_non_int() -> None:
    # ``count`` is None (candidate isn't an int / parseable string) and
    # ``isinstance(candidate, str)`` is False → returns ``None`` (line 554).
    count, detail = _find_consistency_check_inconsistency({"Inconsistencies": None})

    assert count is None
    assert detail is None


def test_find_consistency_check_inconsistency_non_negative_text_detail() -> None:
    # Candidate is a non-negative descriptive string → return ``(None, detail)``
    # on line 553.
    count, detail = _find_consistency_check_inconsistency({"Inconsistencies": "Detected on stripe"})

    assert count is None
    assert detail == "Detected on stripe"


def test_find_consistency_check_inconsistency_negative_text_returns_none() -> None:
    count, detail = _find_consistency_check_inconsistency({"Inconsistencies": "no"})

    assert count is None
    assert detail is None


def test_find_consistency_check_inconsistency_no_match_returns_none() -> None:
    assert _find_consistency_check_inconsistency({"Unrelated": "value"}) == (None, None)


@pytest.mark.parametrize("text", ["no", "none", "n/a", "na", "0", "  No  "])
def test_is_negative_inconsistency_detail_set_membership(text: str) -> None:
    # Exercises line 560 (returns True from the membership branch).
    assert _is_negative_consistency_check_inconsistency_detail(text) is True


def test_is_negative_inconsistency_detail_regex_match() -> None:
    assert _is_negative_consistency_check_inconsistency_detail("zero inconsistencies") is True


def test_is_negative_inconsistency_detail_unrelated() -> None:
    assert _is_negative_consistency_check_inconsistency_detail("Detected on stripe") is False


# ---------------------------------------------------------------------------
# Lines 592, 594, 596: _parse_int type branches
# ---------------------------------------------------------------------------


def test_parse_int_rejects_bool() -> None:
    assert _parse_int(True) is None
    assert _parse_int(False) is None


def test_parse_int_returns_int_unchanged() -> None:
    assert _parse_int(42) == 42


def test_parse_int_truncates_float() -> None:
    assert _parse_int(3.9) == 3


def test_parse_int_extracts_from_string() -> None:
    assert _parse_int("42 percent") == 42


def test_parse_int_returns_none_for_unparseable_string() -> None:
    assert _parse_int("no digits") is None


def test_parse_int_returns_none_for_other_types() -> None:
    assert _parse_int(None) is None
    assert _parse_int([1, 2]) is None


# ---------------------------------------------------------------------------
# Lines 622, 624, 626, 628, 632, 642-644: _parse_minutes branches
# ---------------------------------------------------------------------------


def test_parse_minutes_rejects_bool() -> None:
    assert _parse_minutes(True) is None
    assert _parse_minutes(False) is None


def test_parse_minutes_accepts_int() -> None:
    assert _parse_minutes(15) == 15


def test_parse_minutes_truncates_float() -> None:
    assert _parse_minutes(15.9) == 15


def test_parse_minutes_rejects_other_types() -> None:
    assert _parse_minutes(None) is None
    assert _parse_minutes([]) is None


@pytest.mark.parametrize("text", ["", "-", "n/a", "na", "none", "  N/A  "])
def test_parse_minutes_returns_none_for_sentinel_strings(text: str) -> None:
    assert _parse_minutes(text) is None


def test_parse_minutes_returns_zero_for_explicit_zero_strings() -> None:
    assert _parse_minutes("0") == 0
    assert _parse_minutes("0 minutes") == 0


def test_parse_minutes_parses_combined_units() -> None:
    assert _parse_minutes("1 day 2 hours 30 minutes") == 24 * 60 + 2 * 60 + 30


def test_parse_minutes_rejects_unknown_alphabetic_units() -> None:
    # Alphabetic content with no recognised unit → returns None (line 643).
    assert _parse_minutes("abc") is None
    assert _parse_minutes("never") is None


def test_parse_minutes_parses_unitless_numeric_string() -> None:
    # No unit, no alphabetic noise → fall through to ``_parse_int`` (line 644).
    assert _parse_minutes("42") == 42


# ---------------------------------------------------------------------------
# Line 661: _normalize_rebuild_state "complete" branch
# ---------------------------------------------------------------------------


def test_normalize_rebuild_state_complete_text() -> None:
    assert _normalize_rebuild_state("Complete", None) == "Complete"


def test_normalize_rebuild_state_complete_text_with_not_is_idle() -> None:
    # ``not`` precedence: "not in progress" returns "Not in progress".
    assert _normalize_rebuild_state("Not in progress", None) == "Not in progress"


# ---------------------------------------------------------------------------
# Lines 671-673: _normalize_rebuild_state percent-based fallbacks
# ---------------------------------------------------------------------------


def test_normalize_rebuild_state_percent_only_in_progress() -> None:
    assert _normalize_rebuild_state(None, 42) == "In progress"


def test_normalize_rebuild_state_zero_percent_no_state() -> None:
    assert _normalize_rebuild_state(None, 0) == "Not in progress"


def test_normalize_rebuild_state_none_state_unrecognised_value_with_percent() -> None:
    # raw_state present but none of the known fragments match → falls past the
    # ``if raw_state is not None`` block. With percent > 0 we land on line 672.
    assert _normalize_rebuild_state("mystery firmware string", 50) == "In progress"


def test_normalize_rebuild_state_none_state_unrecognised_value_zero_percent() -> None:
    assert _normalize_rebuild_state("mystery firmware string", 0) == "Not in progress"


# ---------------------------------------------------------------------------
# Line 678: _normalize_patrol_read_state with None
# ---------------------------------------------------------------------------


def test_normalize_patrol_read_state_none_returns_unknown() -> None:
    assert _normalize_patrol_read_state(None) == "unknown"


def test_normalize_patrol_read_state_paused_branch() -> None:
    assert _normalize_patrol_read_state("Paused") == "paused"


def test_normalize_patrol_read_state_ready_branch() -> None:
    assert _normalize_patrol_read_state("Ready") == "ready"


def test_normalize_patrol_read_state_aborted_maps_to_stopped() -> None:
    assert _normalize_patrol_read_state("Aborted") == "stopped"


def test_normalize_patrol_read_state_unknown_passthrough() -> None:
    assert _normalize_patrol_read_state("weird state") == "weird state"


def test_normalize_patrol_read_state_empty_passes_unknown() -> None:
    assert _normalize_patrol_read_state("   ") == "unknown"


# ---------------------------------------------------------------------------
# Lines 706, 708: validate_virtual_drive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vd_id", ["abc", 1.5, True])
def test_build_consistency_check_start_rejects_non_int_vd(vd_id: Any) -> None:
    with pytest.raises(ValueError, match="vd_id must be int"):
        build_consistency_check_start_command(vd_id)


@pytest.mark.parametrize("vd_id", [-1, 256, 500])
def test_build_consistency_check_start_rejects_out_of_range_vd(vd_id: int) -> None:
    with pytest.raises(ValueError, match="vd_id must be int"):
        build_consistency_check_start_command(vd_id)


# ---------------------------------------------------------------------------
# Branch 354->353 (already covered above) and rebuild Command Status failure
# without an ErrMsg, exercising the fall-back string path through parse_*.
# ---------------------------------------------------------------------------


def test_parse_rebuild_status_failure_without_errmsg_uses_description() -> None:
    with pytest.raises(StorcliCommandFailed, match="boom") as exc_info:
        parse_rebuild_status(
            {
                "Controllers": [
                    {
                        "Command Status": {
                            "Status": "Failure",
                            "Description": "boom",
                            "Detailed Status": [{"NotErrMsg": "x"}],
                        },
                        "Response Data": {},
                    }
                ]
            }
        )

    assert exc_info.value.err_msg == "boom"


# ---------------------------------------------------------------------------
# Parse rebuild status surfaces patrol payload with progress in plain key
# (covers _find_patrol_read_progress_percent fallback via parse_patrol_read).
# ---------------------------------------------------------------------------


def test_parse_patrol_read_status_progress_from_plain_key() -> None:
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "Controller Properties": [
                        {"Ctrl_Prop": "PR Mode", "Value": "Auto"},
                        {"Ctrl_Prop": "PR Current State", "Value": "Active"},
                    ],
                    "Nested": {"Patrol Read Progress": "55%"},
                },
            }
        ]
    }

    status = parse_patrol_read_status(payload)

    assert isinstance(status, PatrolReadStatus)
    assert status.state == "active"
    assert status.progress_percent == 55


# ---------------------------------------------------------------------------
# Patrol-read can_stop helper (line 319 fallback).
# ---------------------------------------------------------------------------


def test_patrol_read_can_stop_when_running() -> None:
    status = PatrolReadStatus(
        mode="auto",
        state="active",
        progress_percent=10,
        completed_drive_count=None,
        last_run_timestamp=None,
    )

    assert drive_actions.patrol_read_can_stop(status) is True


def test_patrol_read_can_stop_when_stopped() -> None:
    status = PatrolReadStatus(
        mode="auto",
        state="stopped",
        progress_percent=None,
        completed_drive_count=None,
        last_run_timestamp=None,
    )

    assert drive_actions.patrol_read_can_stop(status) is False


# ---------------------------------------------------------------------------
# Branch 261->265: parse_patrol_read_status with state_raw is None
#
# This branch is otherwise unreachable from public input: the only way to make
# ``state in {"active", "paused"}`` is for ``state_raw`` to contain one of the
# recognised tokens, so the inner ``if state_raw is not None`` guard cannot be
# False from real storcli payloads. To exercise the defensive guard for branch
# coverage we monkeypatch ``_normalize_patrol_read_state`` so that ``None``
# state_raw still maps to ``"active"``.
# ---------------------------------------------------------------------------


def test_parse_patrol_read_status_handles_none_state_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        drive_actions,
        "_normalize_patrol_read_state",
        lambda _: "active",
    )

    status = parse_patrol_read_status(
        {
            "Controllers": [
                {
                    "Command Status": {"Status": "Success"},
                    "Response Data": {
                        "Controller Properties": [
                            {"Ctrl_Prop": "PR Mode", "Value": "Auto"},
                        ]
                    },
                }
            ]
        }
    )

    assert status.state == "active"
    assert status.progress_percent is None
    assert status.completed_drive_count is None


# ---------------------------------------------------------------------------
# Sanity check: the module exposes the names we imported (smoke check).
# ---------------------------------------------------------------------------


def test_drive_actions_module_has_internal_helpers() -> None:
    assert callable(drive_actions._command_error_message)
    assert callable(drive_actions._parse_int)
    assert callable(drive_actions._parse_minutes)
