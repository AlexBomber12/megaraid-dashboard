from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from megaraid_dashboard.storcli import StorcliCommandFailed, StorcliParseError

LocateAction = Literal["start", "stop"]
PatrolReadMode = Literal["auto", "manual", "disable"]
ReplaceStep = Literal["offline", "missing"]

_LOCATE_VERB: dict[LocateAction, str] = {
    "start": "start",
    "stop": "stop",
}

_OFFLINE_ALLOWED_STATES: frozenset[str] = frozenset({"Onln", "Offln", "Failed", "UBad", "UGood"})
_MISSING_ALLOWED_STATES: frozenset[str] = frozenset({"Offln"})

# Matches the exact success-audit format written by the route layer for the
# missing step: ``replace step missing drive {e}:{s} serial {sn} succeeded``.
# The end-of-string anchor is critical: failed audits append free-form storcli
# error text after ``failed:`` that can contain substrings like
# ``not succeeded`` or ``succeeded operation aborted``.
_REPLACE_STEP_MISSING_SUCCESS_RE = re.compile(
    r"^replace step missing drive \d+:\d+ serial \S+ succeeded\Z"
)


def build_locate_command(enclosure: int, slot: int, action: LocateAction) -> list[str]:
    validate_enclosure_slot(enclosure, slot)
    if action not in _LOCATE_VERB:
        raise ValueError(f"unknown locate action: {action!r}")
    verb = _LOCATE_VERB[action]
    return [f"/c0/e{enclosure}/s{slot}", verb, "locate", "J"]


def build_set_offline_command(enclosure: int, slot: int) -> list[str]:
    validate_enclosure_slot(enclosure, slot)
    return [f"/c0/e{enclosure}/s{slot}", "set", "offline", "J"]


def build_set_missing_command(enclosure: int, slot: int) -> list[str]:
    validate_enclosure_slot(enclosure, slot)
    return [f"/c0/e{enclosure}/s{slot}", "set", "missing", "J"]


def build_show_drive_command(enclosure: int, slot: int) -> list[str]:
    validate_enclosure_slot(enclosure, slot)
    return [f"/c0/e{enclosure}/s{slot}", "show", "all", "J"]


def build_rebuild_status_command(enclosure: int, slot: int) -> list[str]:
    validate_enclosure_slot(enclosure, slot)
    return [f"/c0/e{enclosure}/s{slot}", "show", "rebuild", "J"]


def build_patrol_read_show_command() -> list[str]:
    """Show controller patrol-read state.

    Firmware syntax verified from StorCLI/PERCCLI-compatible docs:
    ``/c0 show patrolread J``.
    """
    return ["/c0", "show", "patrolread", "J"]


def build_patrol_read_start_command() -> list[str]:
    """Start an immediate whole-controller patrol-read pass: ``/c0 start patrolread J``."""
    return ["/c0", "start", "patrolread", "J"]


def build_patrol_read_stop_command() -> list[str]:
    """Stop the active patrol-read pass: ``/c0 stop patrolread J``."""
    return ["/c0", "stop", "patrolread", "J"]


def build_patrol_read_mode_command(mode: PatrolReadMode) -> list[str]:
    """Set patrol-read scheduling mode.

    StorCLI uses ``patrolread=on mode=auto`` or ``patrolread=on mode=manual``
    for enabled modes, and ``patrolread=off`` for disable.
    """
    if mode == "auto":
        return ["/c0", "set", "patrolread=on", "mode=auto", "J"]
    if mode == "manual":
        return ["/c0", "set", "patrolread=on", "mode=manual", "J"]
    if mode == "disable":
        return ["/c0", "set", "patrolread=off", "J"]
    raise ValueError(f"unknown patrol read mode: {mode!r}")


def build_insert_replacement_command(
    enclosure: int, slot: int, dg: int, array: int, row: int
) -> list[str]:
    validate_enclosure_slot(enclosure, slot)
    if not isinstance(dg, int) or isinstance(dg, bool) or dg < 0 or dg > 63:
        raise ValueError("dg must be int in [0, 63]")
    if not isinstance(array, int) or isinstance(array, bool) or array < 0 or array > 63:
        raise ValueError("array must be int in [0, 63]")
    if not isinstance(row, int) or isinstance(row, bool) or row < 0 or row > 255:
        raise ValueError("row must be int in [0, 255]")
    return [
        f"/c0/e{enclosure}/s{slot}",
        "insert",
        f"dg={dg}",
        f"array={array}",
        f"row={row}",
        "J",
    ]


def build_foreign_config_show_command() -> list[str]:
    """Show the foreign configuration on the controller (read-only)."""
    return ["/c0/fall", "show", "all", "J"]


def build_foreign_config_import_command() -> list[str]:
    """Import the foreign configuration the controller currently sees.

    The storcli command syntax is firmware-specific; this builder emits the
    most widely documented form (``/c0/fall import``). Hand-verification on
    real hardware is mandatory before merge — see PR-064 task notes.
    """
    return ["/c0/fall", "import", "J"]


def build_foreign_config_clear_command() -> list[str]:
    """Clear/delete the foreign configuration the controller currently sees.

    The storcli command syntax is firmware-specific; this builder emits the
    most widely documented form (``/c0/fall delete``). Hand-verification on
    real hardware is mandatory before merge — see PR-064 task notes.
    """
    return ["/c0/fall", "delete", "J"]


def can_transition(current_state: str, requested_step: ReplaceStep) -> bool:
    if requested_step == "offline":
        return current_state in _OFFLINE_ALLOWED_STATES
    if requested_step == "missing":
        return current_state in _MISSING_ALLOWED_STATES
    return False


def can_transition_step3(latest_audit_message: str | None) -> bool:
    """Insert is allowed only if the latest operator-action audit for this slot
    records that ``replace step missing`` succeeded. Failed missing attempts and
    intervening operator actions (e.g. locate) reset the gate.

    The match is anchored on the full audit-message format produced by the
    route layer so free-form storcli error text appended after ``failed:``
    cannot bypass the gate by including ``succeeded`` as a substring.
    """
    if latest_audit_message is None:
        return False
    return _REPLACE_STEP_MISSING_SUCCESS_RE.match(latest_audit_message) is not None


@dataclass(frozen=True)
class RebuildStatus:
    percent_complete: int
    state: str
    time_remaining_minutes: int | None


@dataclass(frozen=True)
class PatrolReadStatus:
    mode: str
    state: str
    progress_percent: int | None
    completed_drive_count: int | None
    last_run_timestamp: str | None

    @property
    def is_running(self) -> bool:
        return self.state.lower() in {"active", "paused", "running", "in progress"}


def parse_rebuild_status(payload: dict[str, Any]) -> RebuildStatus:
    response_data = _single_controller_response_data(payload)
    percent = _find_percent_complete(response_data)
    explicit_state = _find_text_value(
        response_data,
        key_fragments=("state", "status"),
        value_hints=("rebuild", "rbld", "progress", "complete", "not in progress", "none"),
    )
    time_remaining = _find_time_remaining_minutes(response_data)

    if percent is None and explicit_state is None:
        raise StorcliParseError("storcli rebuild status missing progress data")

    state = _normalize_rebuild_state(explicit_state, percent)
    resolved_percent = percent
    if resolved_percent is None:
        resolved_percent = 100 if state == "Complete" else 0
    return RebuildStatus(
        percent_complete=max(0, min(100, resolved_percent)),
        state=state,
        time_remaining_minutes=time_remaining,
    )


def parse_patrol_read_status(payload: dict[str, Any]) -> PatrolReadStatus:
    response_data = _single_controller_response_data(payload)
    mode = _find_patrol_read_text(response_data, ("mode",)) or "unknown"
    state_raw = _find_patrol_read_text(response_data, ("current state", "state", "status"))
    state = _normalize_patrol_read_state(state_raw)
    progress_percent = None
    completed_drive_count = None
    if state in {"active", "paused"}:
        progress_percent = _find_patrol_read_progress_percent(response_data)
        if state_raw is not None:
            if progress_percent is None:
                progress_percent = _parse_trailing_percent(state_raw)
            completed_drive_count = _parse_trailing_drive_count(state_raw)
    last_run_timestamp = _find_patrol_read_text(
        response_data,
        ("last run", "last completed", "last start", "last"),
    )
    return PatrolReadStatus(
        mode=mode.lower(),
        state=state,
        progress_percent=(
            max(0, min(100, progress_percent)) if progress_percent is not None else None
        ),
        completed_drive_count=completed_drive_count,
        last_run_timestamp=last_run_timestamp,
    )


def patrol_read_can_start(status: PatrolReadStatus) -> bool:
    return status.state.lower() in {"stopped", "ready"}


def patrol_read_can_stop(status: PatrolReadStatus) -> bool:
    return status.is_running


def _single_controller_response_data(payload: dict[str, Any]) -> dict[str, Any]:
    controllers = payload.get("Controllers")
    if not isinstance(controllers, list) or not controllers:
        raise StorcliParseError("storcli rebuild status missing Controllers")
    controller = controllers[0]
    if not isinstance(controller, dict):
        raise StorcliParseError("storcli rebuild status controller is not an object")
    command_status = controller.get("Command Status")
    if (
        isinstance(command_status, dict)
        and str(command_status.get("Status", "")).lower() == "failure"
    ):
        err_msg = _command_error_message(command_status)
        raise StorcliCommandFailed(f"storcli command failed: {err_msg}", err_msg=err_msg)
    response_data = controller.get("Response Data")
    if not isinstance(response_data, dict):
        raise StorcliParseError("storcli rebuild status missing Response Data")
    return response_data


def _command_error_message(command_status: dict[str, Any]) -> str:
    detailed_status = command_status.get("Detailed Status", [])
    if isinstance(detailed_status, list):
        for item in detailed_status:
            if isinstance(item, dict) and item.get("ErrMsg"):
                return str(item["ErrMsg"])
    return str(command_status.get("Description") or command_status.get("Status") or "unknown error")


def _find_percent_complete(value: Any) -> int | None:
    for key, candidate in _walk_key_values(value):
        normalized_key = key.lower().replace(" ", "").replace("_", "")
        if "%" in key or "percent" in normalized_key or "progress" in normalized_key:
            percent = _parse_int(candidate)
            if percent is not None:
                return percent
    return None


def _find_patrol_read_progress_percent(value: Any) -> int | None:
    for key, candidate in _walk_storcli_properties(value):
        percent = _parse_patrol_read_progress_candidate(key, candidate)
        if percent is not None:
            return percent
    for key, candidate in _walk_key_values(value):
        percent = _parse_patrol_read_progress_candidate(key, candidate)
        if percent is not None:
            return percent
    return None


def _parse_patrol_read_progress_candidate(key: str, candidate: Any) -> int | None:
    normalized_key = key.lower().replace(" ", "").replace("_", "")
    if "patrol" not in normalized_key and "pr" not in normalized_key:
        return None
    if "rate" in normalized_key:
        return None
    if "progress" not in normalized_key and "percentcomplete" not in normalized_key:
        return None
    return _parse_int(candidate)


def _find_time_remaining_minutes(value: Any) -> int | None:
    for key, candidate in _walk_key_values(value):
        normalized_key = key.lower().replace(" ", "").replace("_", "")
        if "time" in normalized_key and (
            "remain" in normalized_key or "left" in normalized_key or "eta" in normalized_key
        ):
            return _parse_minutes(candidate)
    return None


def _find_text_value(
    value: Any,
    *,
    key_fragments: tuple[str, ...],
    value_hints: tuple[str, ...],
) -> str | None:
    for key, candidate in _walk_key_values(value):
        if not isinstance(candidate, str):
            continue
        lowered_key = key.lower()
        lowered_value = candidate.lower()
        if any(fragment in lowered_key for fragment in key_fragments) and any(
            hint in lowered_value for hint in value_hints
        ):
            return candidate
    return None


def _find_patrol_read_text(value: Any, key_hints: tuple[str, ...]) -> str | None:
    for key, candidate in _walk_storcli_properties(value):
        lowered_key = key.lower()
        if any(hint in lowered_key for hint in key_hints) and isinstance(candidate, str):
            text = candidate.strip()
            if text:
                return text
    for key, candidate in _walk_key_values(value):
        lowered_key = key.lower()
        if any(hint in lowered_key for hint in key_hints) and isinstance(candidate, str):
            text = candidate.strip()
            if text:
                return text
    return None


def _walk_storcli_properties(value: Any) -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("Ctrl_Prop"), str) and "Value" in value:
            pairs.append((value["Ctrl_Prop"], value["Value"]))
        for candidate in value.values():
            pairs.extend(_walk_storcli_properties(candidate))
    elif isinstance(value, list):
        for item in value:
            pairs.extend(_walk_storcli_properties(item))
    return pairs


def _walk_key_values(value: Any) -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, candidate in value.items():
            key_text = str(key)
            pairs.append((key_text, candidate))
            pairs.extend(_walk_key_values(candidate))
    elif isinstance(value, list):
        for item in value:
            pairs.extend(_walk_key_values(item))
    return pairs


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match is not None:
            return int(float(match.group(0)))
    return None


def _parse_trailing_percent(value: str) -> int | None:
    match = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)\s*%\s*$", value)
    if match is None:
        return None
    return int(float(match.group(1)))


def _parse_trailing_drive_count(value: str) -> int | None:
    if re.search(r"%\s*$", value) is not None:
        return None
    match = re.search(r"(?:^|\s)(\d+)\s*$", value)
    if match is None:
        return None
    return int(match.group(1))


def _parse_minutes(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None

    lowered = value.strip().lower()
    if lowered in {"", "-", "n/a", "na", "none"}:
        return None
    if lowered in {"0", "0 minutes", "0 minute"}:
        return 0

    days = _unit_value(lowered, ("day", "days", "d"))
    hours = _unit_value(lowered, ("hour", "hours", "hr", "hrs", "h"))
    minutes = _unit_value(lowered, ("minute", "minutes", "min", "mins", "m"))
    seconds = _unit_value(lowered, ("second", "seconds", "sec", "secs", "s"))
    if days is not None or hours is not None or minutes is not None or seconds is not None:
        return int((days or 0) * 24 * 60 + (hours or 0) * 60 + (minutes or 0) + (seconds or 0) / 60)
    if any(character.isalpha() for character in lowered):
        return None
    return _parse_int(lowered)


def _unit_value(text: str, units: tuple[str, ...]) -> float | None:
    unit_pattern = "|".join(re.escape(unit) for unit in units)
    match = re.search(rf"(\d+(?:\.\d+)?)\s*(?:{unit_pattern})\b", text)
    if match is None:
        return None
    return float(match.group(1))


def _normalize_rebuild_state(raw_state: str | None, percent: int | None) -> str:
    if percent is not None and percent >= 100:
        return "Complete"
    if raw_state is not None:
        lowered = raw_state.lower()
        if "complete" in lowered and "not" not in lowered:
            return "Complete"
        if "not" in lowered or "none" in lowered or "idle" in lowered:
            return "Not in progress"
        if (
            "progress" in lowered
            or "rebuild" in lowered
            or "rbld" in lowered
            or "active" in lowered
        ):
            return "In progress"
    if percent is not None and percent > 0:
        return "In progress"
    return "Not in progress"


def _normalize_patrol_read_state(raw_state: str | None) -> str:
    if raw_state is None:
        return "unknown"
    lowered = raw_state.strip().lower()
    if "not in progress" in lowered:
        return "stopped"
    if "active" in lowered or "running" in lowered or "progress" in lowered:
        return "active"
    if "paused" in lowered:
        return "paused"
    if "ready" in lowered:
        return "ready"
    if "stopped" in lowered or "aborted" in lowered:
        return "stopped"
    return lowered or "unknown"


def validate_enclosure_slot(enclosure: int, slot: int) -> None:
    if not isinstance(enclosure, int) or isinstance(enclosure, bool):
        raise ValueError("enclosure must be int in [0, 255]")
    if enclosure < 0 or enclosure > 255:
        raise ValueError("enclosure must be int in [0, 255]")
    if not isinstance(slot, int) or isinstance(slot, bool):
        raise ValueError("slot must be int in [0, 255]")
    if slot < 0 or slot > 255:
        raise ValueError("slot must be int in [0, 255]")
