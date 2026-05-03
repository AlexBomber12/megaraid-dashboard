from __future__ import annotations

from typing import Any

import pytest

from megaraid_dashboard.services.drive_actions import build_locate_command


def test_build_locate_command_start() -> None:
    assert build_locate_command(2, 0, "start") == ["/c0/e2/s0", "start", "locate", "J"]


def test_build_locate_command_stop() -> None:
    assert build_locate_command(2, 0, "stop") == ["/c0/e2/s0", "stop", "locate", "J"]


@pytest.mark.parametrize("enclosure", [-1, 256, "abc", None])
def test_build_locate_command_rejects_invalid_enclosure(enclosure: Any) -> None:
    with pytest.raises(ValueError, match="enclosure must be int"):
        build_locate_command(enclosure, 0, "start")


@pytest.mark.parametrize("slot", [-1, 256, "abc", None])
def test_build_locate_command_rejects_invalid_slot(slot: Any) -> None:
    with pytest.raises(ValueError, match="slot must be int"):
        build_locate_command(2, slot, "start")


@pytest.mark.parametrize("action", ["foo", "start; rm -rf /"])
def test_build_locate_command_rejects_unknown_action(action: Any) -> None:
    with pytest.raises(ValueError, match="unknown locate action"):
        build_locate_command(2, 0, action)
