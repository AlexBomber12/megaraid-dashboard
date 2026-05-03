from __future__ import annotations

from typing import Literal

LocateAction = Literal["start", "stop"]

_LOCATE_VERB: dict[LocateAction, str] = {
    "start": "start",
    "stop": "stop",
}


def build_locate_command(enclosure: int, slot: int, action: LocateAction) -> list[str]:
    if not isinstance(enclosure, int) or enclosure < 0 or enclosure > 255:
        raise ValueError("enclosure must be int in [0, 255]")
    if not isinstance(slot, int) or slot < 0 or slot > 255:
        raise ValueError("slot must be int in [0, 255]")
    if action not in _LOCATE_VERB:
        raise ValueError(f"unknown locate action: {action!r}")
    verb = _LOCATE_VERB[action]
    return ["/c0/e" + str(enclosure) + "/s" + str(slot), verb, "locate", "J"]
