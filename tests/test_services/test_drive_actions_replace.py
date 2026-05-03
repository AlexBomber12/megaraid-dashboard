from __future__ import annotations

from typing import Any

import pytest

from megaraid_dashboard.services.drive_actions import (
    build_set_missing_command,
    build_set_offline_command,
    build_show_drive_command,
    can_transition,
)


def test_build_set_offline_command() -> None:
    assert build_set_offline_command(2, 0) == ["/c0/e2/s0", "set", "offline", "J"]


def test_build_set_offline_command_uses_concrete_es_segments() -> None:
    assert build_set_offline_command(255, 7) == ["/c0/e255/s7", "set", "offline", "J"]


def test_build_set_missing_command() -> None:
    assert build_set_missing_command(2, 0) == ["/c0/e2/s0", "set", "missing", "J"]


def test_build_set_missing_command_uses_concrete_es_segments() -> None:
    assert build_set_missing_command(255, 7) == ["/c0/e255/s7", "set", "missing", "J"]


def test_build_show_drive_command() -> None:
    assert build_show_drive_command(2, 0) == ["/c0/e2/s0", "show", "all", "J"]


def test_build_show_drive_command_uses_concrete_es_segments() -> None:
    assert build_show_drive_command(255, 7) == ["/c0/e255/s7", "show", "all", "J"]


@pytest.mark.parametrize("enclosure", [-1, 256, "abc", None, 1.5, True])
def test_build_show_drive_command_rejects_invalid_enclosure(enclosure: Any) -> None:
    with pytest.raises(ValueError, match="enclosure must be int"):
        build_show_drive_command(enclosure, 0)


@pytest.mark.parametrize("slot", [-1, 256, "abc", None, 1.5, True])
def test_build_show_drive_command_rejects_invalid_slot(slot: Any) -> None:
    with pytest.raises(ValueError, match="slot must be int"):
        build_show_drive_command(2, slot)


@pytest.mark.parametrize("enclosure", [-1, 256, "abc", None, 1.5, True])
def test_build_set_offline_command_rejects_invalid_enclosure(enclosure: Any) -> None:
    with pytest.raises(ValueError, match="enclosure must be int"):
        build_set_offline_command(enclosure, 0)


@pytest.mark.parametrize("slot", [-1, 256, "abc", None, 1.5, True])
def test_build_set_offline_command_rejects_invalid_slot(slot: Any) -> None:
    with pytest.raises(ValueError, match="slot must be int"):
        build_set_offline_command(2, slot)


@pytest.mark.parametrize("enclosure", [-1, 256, "abc", None, 1.5, True])
def test_build_set_missing_command_rejects_invalid_enclosure(enclosure: Any) -> None:
    with pytest.raises(ValueError, match="enclosure must be int"):
        build_set_missing_command(enclosure, 0)


@pytest.mark.parametrize("slot", [-1, 256, "abc", None, 1.5, True])
def test_build_set_missing_command_rejects_invalid_slot(slot: Any) -> None:
    with pytest.raises(ValueError, match="slot must be int"):
        build_set_missing_command(2, slot)


@pytest.mark.parametrize(
    ("current_state", "expected"),
    [
        ("Onln", True),
        ("Offln", True),
        ("Failed", True),
        ("UBad", True),
        ("UGood", True),
        ("Rbld", False),
        ("DHS", False),
        ("GHS", False),
        ("JBOD", False),
        ("Missing", False),
        ("", False),
    ],
)
def test_can_transition_offline(current_state: str, expected: bool) -> None:
    assert can_transition(current_state, "offline") is expected


@pytest.mark.parametrize(
    ("current_state", "expected"),
    [
        ("Onln", False),
        ("Offln", True),
        ("Failed", False),
        ("UBad", False),
        ("UGood", False),
        ("Rbld", False),
        ("DHS", False),
        ("GHS", False),
        ("JBOD", False),
        ("Missing", False),
        ("", False),
    ],
)
def test_can_transition_missing(current_state: str, expected: bool) -> None:
    assert can_transition(current_state, "missing") is expected
