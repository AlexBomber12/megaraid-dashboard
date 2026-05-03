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


def build_locate_command(enclosure: int, slot: int, action: LocateAction) -> list[str]:
    _validate_es(enclosure, slot)
    if action not in _LOCATE_VERB:
        raise ValueError(f"unknown locate action: {action!r}")
    verb = _LOCATE_VERB[action]
    return [f"/c0/e{enclosure}/s{slot}", verb, "locate", "J"]


def build_set_offline_command(enclosure: int, slot: int) -> list[str]:
    _validate_es(enclosure, slot)
    return [f"/c0/e{enclosure}/s{slot}", "set", "offline", "J"]


def build_set_missing_command(enclosure: int, slot: int) -> list[str]:
    _validate_es(enclosure, slot)
    return [f"/c0/e{enclosure}/s{slot}", "set", "missing", "J"]


def can_transition(current_state: str, requested_step: ReplaceStep) -> bool:
    if requested_step == "offline":
        return current_state in _OFFLINE_ALLOWED_STATES
    if requested_step == "missing":
        return current_state in _MISSING_ALLOWED_STATES
    return False


def _validate_es(enclosure: int, slot: int) -> None:
    if not isinstance(enclosure, int) or isinstance(enclosure, bool):
        raise ValueError("enclosure must be int in [0, 255]")
    if enclosure < 0 or enclosure > 255:
        raise ValueError("enclosure must be int in [0, 255]")
    if not isinstance(slot, int) or isinstance(slot, bool):
        raise ValueError("slot must be int in [0, 255]")
    if slot < 0 or slot > 255:
        raise ValueError("slot must be int in [0, 255]")
