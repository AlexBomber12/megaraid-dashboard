from __future__ import annotations

from typing import Any

import pytest

from megaraid_dashboard.services.drive_actions import (
    build_insert_replacement_command,
    can_transition_step3,
)


def test_build_insert_replacement_command() -> None:
    assert build_insert_replacement_command(2, 0, 0, 0, 4) == [
        "/c0/e2/s0",
        "insert",
        "dg=0",
        "array=0",
        "row=4",
        "J",
    ]


def test_build_insert_replacement_command_uses_concrete_segments() -> None:
    assert build_insert_replacement_command(255, 7, 63, 63, 255) == [
        "/c0/e255/s7",
        "insert",
        "dg=63",
        "array=63",
        "row=255",
        "J",
    ]


@pytest.mark.parametrize("enclosure", [-1, 256, "abc", None, 1.5, True])
def test_build_insert_replacement_command_rejects_invalid_enclosure(enclosure: Any) -> None:
    with pytest.raises(ValueError, match="enclosure must be int"):
        build_insert_replacement_command(enclosure, 0, 0, 0, 0)


@pytest.mark.parametrize("slot", [-1, 256, "abc", None, 1.5, True])
def test_build_insert_replacement_command_rejects_invalid_slot(slot: Any) -> None:
    with pytest.raises(ValueError, match="slot must be int"):
        build_insert_replacement_command(2, slot, 0, 0, 0)


@pytest.mark.parametrize("dg", [-1, 64, "0", None, 1.5, True])
def test_build_insert_replacement_command_rejects_invalid_dg(dg: Any) -> None:
    with pytest.raises(ValueError, match="dg must be int"):
        build_insert_replacement_command(2, 0, dg, 0, 0)


@pytest.mark.parametrize("array", [-1, 64, "0", None, 1.5, True])
def test_build_insert_replacement_command_rejects_invalid_array(array: Any) -> None:
    with pytest.raises(ValueError, match="array must be int"):
        build_insert_replacement_command(2, 0, 0, array, 0)


@pytest.mark.parametrize("row", [-1, 256, "0", None, 1.5, True])
def test_build_insert_replacement_command_rejects_invalid_row(row: Any) -> None:
    with pytest.raises(ValueError, match="row must be int"):
        build_insert_replacement_command(2, 0, 0, 0, row)


@pytest.mark.parametrize(
    ("audit_message", "expected"),
    [
        ("replace step missing drive 2:0 serial WD-OLD succeeded", True),
        ("replace step offline drive 2:0 serial WD-OLD succeeded", False),
        ("replace step missing drive 2:0 serial WD-OLD failed: StorcliCommandFailed", False),
        ("locate start drive 2:0", False),
        ("", False),
        (None, False),
    ],
)
def test_can_transition_step3(audit_message: str | None, expected: bool) -> None:
    assert can_transition_step3(audit_message) is expected
