from __future__ import annotations

from datetime import UTC, datetime

import pytest

from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.overview import derive_controller_health


def _snapshot(*, alarm_state: str = "On") -> ControllerSnapshot:
    return ControllerSnapshot(
        captured_at=datetime(2026, 5, 14, 12, 0, tzinfo=UTC),
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number="SV00000001",
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state=alarm_state,
        cv_present=True,
        bbu_present=False,
        roc_temperature_celsius=70,
    )


def _physical_drive(*, state: str = "Onln", serial_number: str = "SN0001") -> PhysicalDriveSnapshot:
    return PhysicalDriveSnapshot(
        enclosure_id=252,
        slot_id=4,
        device_id=32,
        model="ST4000NM000",
        serial_number=serial_number,
        firmware_version="SN04",
        size_bytes=4_000_000_000_000,
        interface="SAS",
        media_type="HDD",
        state=state,
        temperature_celsius=40,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="5000c50000000001",
    )


def _virtual_drive(*, state: str = "Optl") -> VirtualDriveSnapshot:
    return VirtualDriveSnapshot(
        vd_id=0,
        name="raid6",
        raid_level="RAID6",
        size_bytes=1_000_000_000,
        state=state,
        access="RW",
        cache="RWBD",
    )


def test_buzzer_enabled_with_healthy_drives_is_optimal() -> None:
    health = derive_controller_health(
        _snapshot(alarm_state="On"),
        [_physical_drive(state="Onln") for _ in range(8)],
        [_virtual_drive(state="Optl")],
    )

    assert health == "optimal"


def test_buzzer_enabled_with_failed_drive_is_critical() -> None:
    drives = [_physical_drive(state="Onln", serial_number=f"SN000{idx}") for idx in range(7)]
    drives.append(_physical_drive(state="Failed", serial_number="SN0007"))

    health = derive_controller_health(
        _snapshot(alarm_state="On"),
        drives,
        [_virtual_drive(state="Optl")],
    )

    assert health == "critical"


def test_buzzer_disabled_with_healthy_drives_is_optimal() -> None:
    health = derive_controller_health(
        _snapshot(alarm_state="Off"),
        [_physical_drive(state="Onln") for _ in range(8)],
        [_virtual_drive(state="Optl")],
    )

    assert health == "optimal"


@pytest.mark.parametrize("alarm_state", ["On", "Off", "Sounding", "Unknown"])
def test_alarm_field_does_not_escalate_health_regardless_of_value(alarm_state: str) -> None:
    health = derive_controller_health(
        _snapshot(alarm_state=alarm_state),
        [_physical_drive(state="Onln") for _ in range(8)],
        [_virtual_drive(state="Optl")],
    )

    assert health == "optimal"
