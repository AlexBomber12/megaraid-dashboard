"""SERVER's front-facing cage labels; controller addresses remain unchanged."""

_PHYSICAL_SLOT_BY_CONTROLLER_SLOT = {4: 0, 5: 1, 6: 2, 7: 3, 0: 4, 1: 5, 2: 6, 3: 7}
CONTROLLER_SLOTS_IN_PHYSICAL_ORDER = tuple(_PHYSICAL_SLOT_BY_CONTROLLER_SLOT)


def physical_slot_number(controller_slot: int) -> int:
    return _PHYSICAL_SLOT_BY_CONTROLLER_SLOT.get(controller_slot, controller_slot)


def physical_slot_label(controller_slot: int) -> str:
    return f"S{physical_slot_number(controller_slot)}"
