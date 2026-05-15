from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from megaraid_dashboard.services.event_detector import (
    EventDetector,
    physical_drive_state_severity,
)
from megaraid_dashboard.storcli import (
    ControllerInfo,
    PhysicalDrive,
    StorcliSnapshot,
)


def _make_detector() -> EventDetector:
    return EventDetector(
        temp_warning=60,
        temp_critical=70,
        temp_hysteresis=2,
        roc_temp_warning=80,
        roc_temp_critical=90,
        roc_temp_hysteresis=2,
    )


@pytest.fixture
def detector() -> EventDetector:
    return _make_detector()


def _make_drive(
    *,
    enclosure_id: int = 252,
    slot_id: int = 0,
    serial: str = "SN-A",
    temperature: int | None = None,
    state: str = "Onln",
    smart_alert: bool = False,
) -> PhysicalDrive:
    return PhysicalDrive.model_validate(
        {
            "EID:Slt": f"{enclosure_id}:{slot_id}",
            "DID": 32,
            "Model": "ST",
            "SN": serial,
            "Firmware Revision": "FW",
            "Size": 1,
            "Intf": "SAS",
            "Med": "HDD",
            "State": state,
            "Drive Temperature": "" if temperature is None else f"{temperature}C",
            "Media Error Count": 0,
            "Other Error Count": 0,
            "Predictive Failure Count": 0,
            "S.M.A.R.T alert flagged by drive": smart_alert,
            "SAS address": "0x5000c50000000001",
        }
    )


def _make_snapshot(drive: PhysicalDrive) -> StorcliSnapshot:
    controller = ControllerInfo.model_validate(
        {
            "Model": "LSI MegaRAID SAS 9270CV-8i",
            "Serial Number": "SV0001",
            "Firmware Version": "23.0",
            "Bios Version": "BIOS",
            "Driver Name": "megaraid_sas",
            "Driver Version": "07.0",
            "PCI Address": "0:1:0:0",
            "Current System Date/time": "01/01/2026, 12:00:00",
            "Alarm": "Off",
            "Cachevault_Info": [],
            "BBU": "Yes",
        }
    )
    return StorcliSnapshot(
        controller=controller,
        virtual_drives=[],
        physical_drives=[drive],
        cachevault=None,
        bbu=None,
        captured_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )


def test_temperature_state_from_reading_returns_ok_for_none(detector: EventDetector) -> None:
    assert detector._temperature_state_from_reading(None) == "ok"


def test_detect_roc_temperature_previous_none_below_warning(detector: EventDetector) -> None:
    from datetime import datetime as _dt

    from megaraid_dashboard.db.models import ControllerSnapshot as _ControllerSnapshot

    previous = _ControllerSnapshot(
        captured_at=_dt(2026, 5, 15, 11, 0, tzinfo=UTC),
        model_name="LSI",
        serial_number="SV0001",
        firmware_version="FW",
        bios_version="BIOS",
        driver_version="DRV",
        alarm_state="Off",
        cv_present=True,
        bbu_present=True,
        roc_temperature_celsius=None,
    )

    controller = ControllerInfo.model_validate(
        {
            "Model": "LSI MegaRAID SAS 9270CV-8i",
            "Serial Number": "SV0001",
            "Firmware Version": "23.0",
            "Bios Version": "BIOS",
            "Driver Name": "megaraid_sas",
            "Driver Version": "07.0",
            "PCI Address": "0:1:0:0",
            "Current System Date/time": "01/01/2026, 12:00:00",
            "Alarm": "Off",
            "Cachevault_Info": [],
            "BBU": "Yes",
            "ROC temperature(Degree Celsius)": 50,
        }
    )
    current = StorcliSnapshot(
        controller=controller,
        virtual_drives=[],
        physical_drives=[],
        cachevault=None,
        bbu=None,
        captured_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )

    assert detector._detect_roc_temperature(previous=previous, current=current) == []


def test_temperature_transitions_returns_empty_for_no_temperature(
    detector: EventDetector,
) -> None:
    drive = _make_drive()
    drive = replace(drive, temperature_celsius=None) if hasattr(drive, "_asdict") else drive
    drive_copy = drive.model_copy(update={"temperature_celsius": None})
    events, state = detector._temperature_transitions(drive_copy, initial_state="ok")
    assert events == []
    assert state == "ok"


def test_detect_temperatures_skips_drives_without_temperature(detector: EventDetector) -> None:
    drive = _make_drive(temperature=None)
    snapshot = _make_snapshot(drive)
    result = detector._detect_temperatures(previous=None, current=snapshot, replaced_slots=set())
    assert result == []


def test_state_severity_falls_through_to_info_for_unknown_states() -> None:
    assert physical_drive_state_severity("Rbld", "Cpybck") == "info"


@pytest.mark.parametrize("current_state", ["JBOD", "UGood", "UBad"])
def test_state_severity_warning_for_jbod_ugood_ubad(current_state: str) -> None:
    assert physical_drive_state_severity("Rbld", current_state) == "warning"


def test_state_severity_critical_for_offline() -> None:
    assert physical_drive_state_severity("Onln", "Offln") == "critical"


def test_state_severity_info_when_onln_is_current() -> None:
    assert physical_drive_state_severity("Rbld", "Onln") == "info"


def test_detector_replaces_slot_with_smart_alert_emits_smart_alert_event() -> None:
    from datetime import datetime as _dt

    from megaraid_dashboard.db.models import (
        ControllerSnapshot as _ControllerSnapshot,
    )
    from megaraid_dashboard.db.models import (
        PhysicalDriveSnapshot as _PhysicalDriveSnapshot,
    )

    detector = _make_detector()
    previous_drive_snap = _PhysicalDriveSnapshot(
        enclosure_id=252,
        slot_id=0,
        device_id=32,
        model="ST",
        serial_number="OLD",
        firmware_version="FW",
        size_bytes=1,
        interface="SAS",
        media_type="HDD",
        state="Offln",
        temperature_celsius=None,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="addr",
    )
    previous = _ControllerSnapshot(
        captured_at=_dt(2026, 5, 15, 11, 0, tzinfo=UTC),
        model_name="LSI",
        serial_number="SV0001",
        firmware_version="FW",
        bios_version="BIOS",
        driver_version="DRV",
        alarm_state="Off",
        cv_present=True,
        bbu_present=True,
        physical_drives=[previous_drive_snap],
    )
    current_drive = _make_drive(serial="NEW", state="Rbld", smart_alert=True)
    current_snapshot = _make_snapshot(current_drive)
    replaced = {(current_drive.enclosure_id, current_drive.slot_id)}

    events = detector._detect_physical_drives(
        previous=previous, current=current_snapshot, replaced_slots=replaced
    )
    categories = {event.category for event in events}
    assert "smart_alert" in categories


def test_detector_replaces_slot_state_unchanged_branch() -> None:
    from datetime import datetime as _dt

    from megaraid_dashboard.db.models import (
        ControllerSnapshot as _ControllerSnapshot,
    )
    from megaraid_dashboard.db.models import (
        PhysicalDriveSnapshot as _PhysicalDriveSnapshot,
    )

    detector = _make_detector()
    previous_drive_snap = _PhysicalDriveSnapshot(
        enclosure_id=252,
        slot_id=0,
        device_id=32,
        model="ST",
        serial_number="OLD",
        firmware_version="FW",
        size_bytes=1,
        interface="SAS",
        media_type="HDD",
        state="Onln",
        temperature_celsius=None,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="addr",
    )
    previous = _ControllerSnapshot(
        captured_at=_dt(2026, 5, 15, 11, 0, tzinfo=UTC),
        model_name="LSI",
        serial_number="SV0001",
        firmware_version="FW",
        bios_version="BIOS",
        driver_version="DRV",
        alarm_state="Off",
        cv_present=True,
        bbu_present=True,
        physical_drives=[previous_drive_snap],
    )
    current_drive = _make_drive(serial="NEW", state="Onln")
    current_snapshot = _make_snapshot(current_drive)
    replaced = {(current_drive.enclosure_id, current_drive.slot_id)}

    detector._detect_physical_drives(
        previous=previous, current=current_snapshot, replaced_slots=replaced
    )
