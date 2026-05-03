from __future__ import annotations

from typing import Literal

LocateAction = Literal["start", "stop"]
ReplaceStep = Literal["offline", "missing"]

_LOCATE_VERB: dict[LocateAction, str] = {
    "start": "start",
    "stop": "stop",
}

_OFFLINE_ALLOWED_STATES: frozenset[str] = frozenset({"Onln", "Offln", "Failed", "UBad", "UGood"})
_MISSING_ALLOWED_STATES: frozenset[str] = frozenset({"Offln"})

_REPLACE_STEP_MISSING_TOKEN = "replace step missing"


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


def can_transition(current_state: str, requested_step: ReplaceStep) -> bool:
    if requested_step == "offline":
        return current_state in _OFFLINE_ALLOWED_STATES
    if requested_step == "missing":
        return current_state in _MISSING_ALLOWED_STATES
    return False


def can_transition_step3(latest_audit_message: str | None) -> bool:
    """Insert is allowed only if the latest operator-action audit for this slot
    records that ``replace step missing`` succeeded. Failed missing attempts and
    intervening operator actions (e.g. locate) reset the gate."""
    if latest_audit_message is None:
        return False
    if _REPLACE_STEP_MISSING_TOKEN not in latest_audit_message:
        return False
    return "succeeded" in latest_audit_message


def validate_enclosure_slot(enclosure: int, slot: int) -> None:
    if not isinstance(enclosure, int) or isinstance(enclosure, bool):
        raise ValueError("enclosure must be int in [0, 255]")
    if enclosure < 0 or enclosure > 255:
        raise ValueError("enclosure must be int in [0, 255]")
    if not isinstance(slot, int) or isinstance(slot, bool):
        raise ValueError("slot must be int in [0, 255]")
    if slot < 0 or slot > 255:
        raise ValueError("slot must be int in [0, 255]")
