from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.db import (
    CacheVaultSnapshot,
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
    clear_temp_state_for_slot,
    get_temp_state,
    upsert_temp_state,
)
from megaraid_dashboard.services.event_detector import DetectedEvent, EventDetector
from megaraid_dashboard.storcli import (
    CacheVault,
    ControllerInfo,
    PhysicalDrive,
    StorcliSnapshot,
    VirtualDrive,
)


def test_alarm_state_change_emits_controller_event() -> None:
    events = _detector().detect(
        _previous(alarm_state="Off"),
        _current(controller_alarm_state="On"),
    )

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("info", "controller", "Alarm state changed from Off to On")
    ]


@pytest.mark.parametrize(
    ("before", "after", "severity"),
    [
        ("Optl", "Degraded", "warning"),
        ("Degraded", "Failed", "critical"),
        ("Failed", "Optl", "info"),
    ],
)
def test_virtual_drive_state_changes_emit_expected_severity(
    before: str,
    after: str,
    severity: str,
) -> None:
    events = _detector().detect(
        _previous(vd_state=before),
        _current(vd_state=after),
    )

    assert [(event.severity, event.category, event.subject) for event in events] == [
        (severity, "vd_state", "VD 0")
    ]


@pytest.mark.parametrize(
    ("before", "after", "severity"),
    [
        ("Onln", "Rebld", "info"),
        ("Rebld", "Onln", "info"),
        ("Onln", "Failed", "critical"),
    ],
)
def test_physical_drive_state_changes_emit_expected_severity(
    before: str,
    after: str,
    severity: str,
) -> None:
    events = _detector().detect(
        _previous(pd_state=before),
        _current(pd_state=after),
    )

    assert [(event.severity, event.category, event.subject) for event in events] == [
        (severity, "pd_state", "PD e252:s4")
    ]


def test_physical_drive_error_counter_increases_emit_events() -> None:
    events = _detector().detect(
        _previous(media_errors=0, other_errors=0, predictive_failures=0),
        _current(media_errors=5, other_errors=1, predictive_failures=1),
    )

    assert [(event.severity, event.category) for event in events] == [
        ("critical", "media_errors"),
        ("warning", "other_errors"),
        ("critical", "predictive_failures"),
    ]
    assert "0 to 5" in events[0].summary


def test_smart_alert_transition_to_true_emits_critical_event() -> None:
    events = _detector().detect(
        _previous(smart_alert=False),
        _current(smart_alert=True),
    )

    assert [(event.severity, event.category, event.subject) for event in events] == [
        ("critical", "smart_alert", "PD e252:s4")
    ]


def test_temperature_state_machine_uses_hysteresis(session: Session) -> None:
    detector = _detector()

    events = _detect_and_persist_temperature(session, detector, temperature_celsius=60)
    assert [(event.severity, event.summary) for event in events] == [
        ("warning", "Temperature reached warning threshold: 60 C"),
        ("critical", "Temperature reached critical threshold: 60 C"),
    ]
    assert _stored_temp_state(session) == "critical"

    assert _detect_and_persist_temperature(session, detector, temperature_celsius=55) == []
    assert _stored_temp_state(session) == "critical"

    events = _detect_and_persist_temperature(session, detector, temperature_celsius=54)
    assert [(event.severity, event.summary) for event in events] == [
        ("info", "Temperature back below critical: 54 C")
    ]
    assert _stored_temp_state(session) == "warning"

    assert _detect_and_persist_temperature(session, detector, temperature_celsius=50) == []
    assert _stored_temp_state(session) == "warning"

    events = _detect_and_persist_temperature(session, detector, temperature_celsius=49)
    assert [(event.severity, event.summary) for event in events] == [
        ("info", "Temperature back to normal: 49 C")
    ]
    assert _stored_temp_state(session) == "ok"


def test_drive_replacement_emits_event_and_clears_old_temperature_state(
    session: Session,
) -> None:
    upsert_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="OLD-SN",
        state="critical",
    )
    session.commit()
    detector = _detector()
    current = _current(serial_number="NEW-SN", temperature_celsius=35)
    detector.set_temperature_states({})

    events = detector.detect(_previous(serial_number="OLD-SN"), current)
    _persist_temperature_transitions(session, detector)

    assert [(event.category, event.summary) for event in events] == [
        ("pd_state", "Drive replaced: OLD-SN -> NEW-SN")
    ]
    assert (
        get_temp_state(
            session,
            enclosure_id=252,
            slot_id=4,
            serial_number="OLD-SN",
        )
        is None
    )
    assert _stored_temp_state(session, serial_number="NEW-SN") == "ok"


def test_cachevault_state_change_emits_critical_event() -> None:
    events = _detector().detect(
        _previous(cv_state="Optimal"),
        _current(cv_state="Degraded"),
    )

    assert [(event.severity, event.category, event.subject) for event in events] == [
        ("critical", "cv_state", "CacheVault")
    ]


def test_cachevault_replacement_required_emits_critical_event() -> None:
    events = _detector().detect(
        _previous(cv_replacement_required=False),
        _current(cv_replacement_required=True),
    )

    assert [(event.severity, event.summary) for event in events] == [
        ("critical", "CacheVault replacement required")
    ]


def test_cachevault_capacitance_crossing_threshold_warns_once() -> None:
    detector = _detector()

    events = detector.detect(
        _previous(cv_capacitance_percent=75),
        _current(cv_capacitance_percent=65),
    )
    duplicate_events = detector.detect(
        _previous(cv_capacitance_percent=65),
        _current(cv_capacitance_percent=55),
    )

    assert [(event.severity, event.category) for event in events] == [("warning", "cv_state")]
    assert duplicate_events == []


def _detector() -> EventDetector:
    return EventDetector(
        temp_warning=55,
        temp_critical=60,
        temp_hysteresis=5,
        cv_capacitance_warning_percent=70,
    )


def _detect_and_persist_temperature(
    session: Session,
    detector: EventDetector,
    *,
    temperature_celsius: int,
) -> list[DetectedEvent]:
    current = _current(temperature_celsius=temperature_celsius)
    states = _load_temperature_states(session, current)
    detector.set_temperature_states(states)
    events = detector.detect(None, current)
    _persist_temperature_transitions(session, detector)
    return events


def _load_temperature_states(
    session: Session,
    current: StorcliSnapshot,
) -> dict[tuple[int, int, str], str]:
    states: dict[tuple[int, int, str], str] = {}
    for drive in current.physical_drives:
        temp_state = get_temp_state(
            session,
            enclosure_id=drive.enclosure_id,
            slot_id=drive.slot_id,
            serial_number=drive.serial_number,
        )
        if temp_state is not None:
            states[(drive.enclosure_id, drive.slot_id, drive.serial_number)] = temp_state.state
    return states


def _persist_temperature_transitions(session: Session, detector: EventDetector) -> None:
    for temp_clear in detector.temperature_clears:
        clear_temp_state_for_slot(
            session,
            enclosure_id=temp_clear.enclosure_id,
            slot_id=temp_clear.slot_id,
        )
    for temp_update in detector.temperature_updates:
        upsert_temp_state(
            session,
            enclosure_id=temp_update.enclosure_id,
            slot_id=temp_update.slot_id,
            serial_number=temp_update.serial_number,
            state=temp_update.state,
        )
    session.commit()


def _stored_temp_state(session: Session, *, serial_number: str = "SN0001") -> str | None:
    temp_state = get_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number=serial_number,
    )
    return temp_state.state if temp_state is not None else None


def _previous(
    *,
    alarm_state: str = "Off",
    vd_state: str = "Optl",
    pd_state: str = "Onln",
    serial_number: str = "SN0001",
    temperature_celsius: int | None = 35,
    media_errors: int = 0,
    other_errors: int = 0,
    predictive_failures: int = 0,
    smart_alert: bool = False,
    cv_state: str = "Optimal",
    cv_replacement_required: bool = False,
    cv_capacitance_percent: int | None = 89,
) -> ControllerSnapshot:
    snapshot = ControllerSnapshot(
        captured_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number="SV00000001",
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state=alarm_state,
        cv_present=True,
        bbu_present=False,
    )
    snapshot.virtual_drives = [_previous_virtual_drive(vd_state)]
    snapshot.physical_drives = [
        _previous_physical_drive(
            state=pd_state,
            serial_number=serial_number,
            temperature_celsius=temperature_celsius,
            media_errors=media_errors,
            other_errors=other_errors,
            predictive_failures=predictive_failures,
            smart_alert=smart_alert,
        )
    ]
    snapshot.cachevault = CacheVaultSnapshot(
        type="CVPM02",
        state=cv_state,
        temperature_celsius=40,
        pack_energy="332 J",
        capacitance_percent=cv_capacitance_percent,
        replacement_required=cv_replacement_required,
        next_learn_cycle=None,
    )
    return snapshot


def _previous_virtual_drive(state: str) -> VirtualDriveSnapshot:
    return VirtualDriveSnapshot(
        vd_id=0,
        name="raid5",
        raid_level="RAID5",
        size_bytes=1_000_000_000,
        state=state,
        access="RW",
        cache="RWBD",
    )


def _previous_physical_drive(
    *,
    state: str,
    serial_number: str,
    temperature_celsius: int | None,
    media_errors: int,
    other_errors: int,
    predictive_failures: int,
    smart_alert: bool,
) -> PhysicalDriveSnapshot:
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
        temperature_celsius=temperature_celsius,
        media_errors=media_errors,
        other_errors=other_errors,
        predictive_failures=predictive_failures,
        smart_alert=smart_alert,
        sas_address="5000c50000000001",
    )


def _current(
    *,
    controller_alarm_state: str = "Off",
    vd_state: str = "Optl",
    pd_state: str = "Onln",
    serial_number: str = "SN0001",
    temperature_celsius: int | None = 35,
    media_errors: int = 0,
    other_errors: int = 0,
    predictive_failures: int = 0,
    smart_alert: bool = False,
    cv_state: str = "Optimal",
    cv_replacement_required: bool = False,
    cv_capacitance_percent: int | None = 89,
) -> StorcliSnapshot:
    return StorcliSnapshot(
        controller=ControllerInfo(
            model_name="LSI MegaRAID SAS 9270CV-8i",
            serial_number="SV00000001",
            firmware_version="23.34.0-0019",
            bios_version="6.36.00.3_4.19.08.00_0x06180203",
            driver_name="megaraid_sas",
            driver_version="07.727.03.00",
            pci_address="00:01:00:00",
            system_time=datetime(2026, 4, 25, 12, 5, tzinfo=UTC),
            alarm_state=controller_alarm_state,
            cv_present=True,
            bbu_present=False,
        ),
        virtual_drives=[
            VirtualDrive(
                vd_id=0,
                name="raid5",
                raid_level="RAID5",
                size_bytes=1_000_000_000,
                state=vd_state,
                access="RW",
                cache="RWBD",
            )
        ],
        physical_drives=[
            PhysicalDrive(
                enclosure_id=252,
                slot_id=4,
                device_id=32,
                model="ST4000NM000",
                serial_number=serial_number,
                firmware_version="SN04",
                size_bytes=4_000_000_000_000,
                interface="SAS",
                media_type="HDD",
                state=pd_state,
                temperature_celsius=temperature_celsius,
                media_errors=media_errors,
                other_errors=other_errors,
                predictive_failures=predictive_failures,
                smart_alert=smart_alert,
                sas_address="5000c50000000001",
            )
        ],
        cachevault=CacheVault(
            type="CVPM02",
            state=cv_state,
            temperature_celsius=40,
            pack_energy="332 J",
            capacitance_percent=cv_capacitance_percent,
            replacement_required=cv_replacement_required,
            next_learn_cycle=None,
        ),
        bbu=None,
        captured_at=datetime(2026, 4, 25, 12, 5, tzinfo=UTC),
    )
