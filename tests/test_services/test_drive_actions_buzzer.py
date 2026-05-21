from __future__ import annotations

from megaraid_dashboard.services.drive_actions import (
    build_alarm_disable_command,
    build_alarm_enable_command,
    build_alarm_silence_command,
)


def test_build_alarm_silence_command() -> None:
    assert build_alarm_silence_command() == ["/c0", "set", "alarm=silence"]


def test_build_alarm_disable_command() -> None:
    assert build_alarm_disable_command() == ["/c0", "set", "alarm=off"]


def test_build_alarm_enable_command() -> None:
    assert build_alarm_enable_command() == ["/c0", "set", "alarm=on"]
