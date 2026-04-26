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
        ("Optl", "Offln", "critical"),
        ("Optl", "Pdgd", "critical"),
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


def test_newly_discovered_healthy_virtual_drive_emits_no_event() -> None:
    previous = _previous()
    previous.virtual_drives = []

    assert _detector().detect(previous, _current()) == []


@pytest.mark.parametrize(
    ("state", "severity"),
    [
        ("Degraded", "warning"),
        ("Failed", "critical"),
    ],
)
def test_newly_discovered_faulty_virtual_drive_emits_event(
    state: str,
    severity: str,
) -> None:
    previous = _previous()
    current = _current()
    current = current.model_copy(
        update={"virtual_drives": [current.virtual_drives[0], _virtual_drive(1, state)]},
    )

    events = _detector().detect(previous, current)

    assert [(event.severity, event.category, event.subject, event.summary) for event in events] == [
        (severity, "vd_state", "VD 1", f"VD 1 state is {state}")
    ]
    assert events[0].before is None
    assert events[0].after == {"state": state}


@pytest.mark.parametrize(
    ("before", "after", "severity"),
    [
        ("Onln", "Rebld", "info"),
        ("Rebld", "Onln", "info"),
        ("Onln", "Failed", "critical"),
        ("Onln", "Offln", "critical"),
        ("Onln", "Msng", "critical"),
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


def test_baseline_healthy_snapshot_emits_no_state_events() -> None:
    assert _detector().detect(None, _current()) == []


def test_baseline_snapshot_emits_existing_fault_events() -> None:
    events = _detector().detect(
        None,
        _current(
            controller_alarm_state="On",
            vd_state="Degraded",
            pd_state="Failed",
            media_errors=5,
            other_errors=1,
            predictive_failures=1,
            smart_alert=True,
            cv_state="Degraded",
            cv_replacement_required=True,
            cv_capacitance_percent=65,
        ),
    )

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("info", "controller", "Alarm state is On"),
        ("warning", "vd_state", "VD 0 state is Degraded"),
        ("critical", "pd_state", "PD e252:s4 state is Failed"),
        ("critical", "media_errors", "Media error count is 5"),
        ("warning", "other_errors", "Other error count is 1"),
        ("critical", "predictive_failures", "Predictive failure count is 1"),
        ("critical", "smart_alert", "SMART alert flagged by drive"),
        ("critical", "cv_state", "CacheVault state is Degraded"),
        ("critical", "cv_state", "CacheVault replacement required"),
        ("warning", "cv_state", "CacheVault capacitance below 70%: 65%"),
    ]
    assert all(event.before is None for event in events)


@pytest.mark.parametrize(
    ("previous_temperature", "current_temperature", "expected_summaries", "expected_state"),
    [
        (60, 60, [], "critical"),
        (57, 57, [], "warning"),
        (
            54,
            60,
            [
                "Temperature reached warning threshold: 60 C",
                "Temperature reached critical threshold: 60 C",
            ],
            "critical",
        ),
    ],
)
def test_temperature_state_seeds_from_previous_snapshot_when_db_state_is_missing(
    previous_temperature: int,
    current_temperature: int,
    expected_summaries: list[str],
    expected_state: str,
) -> None:
    detector = _detector()
    detector.set_temperature_states({})

    events = detector.detect(
        _previous(temperature_celsius=previous_temperature),
        _current(temperature_celsius=current_temperature),
    )

    assert [event.summary for event in events] == expected_summaries
    assert detector.temperature_updates[0].state == expected_state


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


def test_replacement_drive_failed_state_still_emits_critical_event() -> None:
    events = _detector().detect(
        _previous(serial_number="OLD-SN", pd_state="Onln"),
        _current(serial_number="NEW-SN", pd_state="Failed"),
    )

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("info", "pd_state", "Drive replaced: OLD-SN -> NEW-SN"),
        ("critical", "pd_state", "PD e252:s4 state changed from Onln to Failed"),
    ]


def test_replacement_drive_state_change_emits_baseline_counter_events() -> None:
    events = _detector().detect(
        _previous(serial_number="OLD-SN", pd_state="Failed"),
        _current(
            serial_number="NEW-SN",
            pd_state="Onln",
            media_errors=5,
            predictive_failures=1,
        ),
    )

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("info", "pd_state", "Drive replaced: OLD-SN -> NEW-SN"),
        ("info", "pd_state", "PD e252:s4 state changed from Failed to Onln"),
        ("critical", "media_errors", "Media error count is 5"),
        ("critical", "predictive_failures", "Predictive failure count is 1"),
    ]
    assert events[2].before is None
    assert events[2].after == {"media_errors": 5}
    assert events[3].before is None
    assert events[3].after == {"predictive_failures": 1}


def test_slot_disappearance_clears_temperature_state(session: Session) -> None:
    upsert_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN0001",
        state="critical",
    )
    session.commit()
    detector = _detector()
    current = _current().model_copy(update={"physical_drives": []})
    detector.set_temperature_states({(252, 4, "SN0001"): "critical"})

    events = detector.detect(_previous(), current)
    _persist_temperature_transitions(session, detector)

    assert events == []
    assert _stored_temp_state(session) is None


def test_new_drive_after_empty_slot_clears_stale_temperature_state(session: Session) -> None:
    upsert_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="OLD-SN",
        state="critical",
    )
    session.commit()
    previous = _previous()
    previous.physical_drives = []
    detector = _detector()
    detector.set_temperature_states({})

    events = detector.detect(previous, _current(serial_number="NEW-SN"))
    _persist_temperature_transitions(session, detector)

    assert events == []
    assert _stored_temp_state(session, serial_number="OLD-SN") is None
    assert _stored_temp_state(session, serial_number="NEW-SN") == "ok"


def test_new_drive_after_empty_slot_failed_state_emits_critical_event() -> None:
    previous = _previous()
    previous.physical_drives = []

    events = _detector().detect(previous, _current(serial_number="NEW-SN", pd_state="Failed"))

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("critical", "pd_state", "PD e252:s4 state is Failed")
    ]


def test_cachevault_state_change_emits_critical_event() -> None:
    events = _detector().detect(
        _previous(cv_state="Optimal"),
        _current(cv_state="Degraded"),
    )

    assert [(event.severity, event.category, event.subject) for event in events] == [
        ("critical", "cv_state", "CacheVault")
    ]


def test_cachevault_recovery_emits_info_event() -> None:
    events = _detector().detect(
        _previous(cv_state="Degraded"),
        _current(cv_state="Optimal"),
    )

    assert [(event.severity, event.category, event.subject) for event in events] == [
        ("info", "cv_state", "CacheVault")
    ]


def test_cachevault_replacement_required_emits_critical_event() -> None:
    events = _detector().detect(
        _previous(cv_replacement_required=False),
        _current(cv_replacement_required=True),
    )

    assert [(event.severity, event.summary) for event in events] == [
        ("critical", "CacheVault replacement required")
    ]


def test_cachevault_disappearance_emits_critical_event() -> None:
    current = _current().model_copy(update={"cachevault": None})

    events = _detector().detect(_previous(cv_state="Optimal"), current)

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("critical", "cv_state", "CacheVault no longer detected")
    ]
    assert events[0].before == {"present": True, "state": "Optimal"}
    assert events[0].after == {"present": False}


def test_cachevault_appearance_emits_presence_and_baseline_health_events() -> None:
    previous = _previous()
    previous.cachevault = None

    events = _detector().detect(
        previous,
        _current(
            cv_state="Degraded",
            cv_replacement_required=True,
            cv_capacitance_percent=65,
        ),
    )

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("info", "cv_state", "CacheVault detected"),
        ("critical", "cv_state", "CacheVault state is Degraded"),
        ("critical", "cv_state", "CacheVault replacement required"),
        ("warning", "cv_state", "CacheVault capacitance below 70%: 65%"),
    ]
    assert events[0].before == {"present": False}
    assert events[0].after == {"present": True, "state": "Degraded"}


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


def test_cachevault_capacitance_unknown_to_low_warns_once() -> None:
    detector = _detector()

    events = detector.detect(
        _previous(cv_capacitance_percent=None),
        _current(cv_capacitance_percent=65),
    )
    duplicate_events = detector.detect(
        _previous(cv_capacitance_percent=65),
        _current(cv_capacitance_percent=55),
    )

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("warning", "cv_state", "CacheVault capacitance dropped below 70%: unknown -> 65%")
    ]
    assert events[0].before == {"capacitance_percent": None}
    assert events[0].after == {"capacitance_percent": 65}
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
        virtual_drives=[_virtual_drive(0, vd_state)],
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


def _virtual_drive(vd_id: int, state: str) -> VirtualDrive:
    return VirtualDrive(
        vd_id=vd_id,
        name=f"raid{vd_id}",
        raid_level="RAID5",
        size_bytes=1_000_000_000,
        state=state,
        access="RW",
        cache="RWBD",
    )
