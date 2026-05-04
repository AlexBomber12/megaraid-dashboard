from __future__ import annotations

from datetime import UTC, datetime

from megaraid_dashboard.db import (
    CacheVaultSnapshot,
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.storcli import (
    CacheVault,
    ControllerInfo,
    ForeignConfig,
    ForeignConfigDiskGroup,
    PhysicalDrive,
    StorcliSnapshot,
    VirtualDrive,
)


def _detector() -> EventDetector:
    return EventDetector(
        temp_warning=55,
        temp_critical=60,
        temp_hysteresis=5,
        roc_temp_warning=95,
        roc_temp_critical=105,
        roc_temp_hysteresis=5,
        cv_capacitance_warning_percent=70,
    )


def test_foreign_config_appearing_emits_warning_baseline() -> None:
    detector = _detector()

    events = detector.detect(None, _current(foreign_config=_foreign_config_present()))

    foreign_events = [event for event in events if event.category == "foreign_config_detected"]
    assert len(foreign_events) == 1
    event = foreign_events[0]
    assert event.severity == "warning"
    assert event.subject == "Controller foreign config"
    assert "Foreign configuration detected" in event.summary
    assert event.after == {
        "present": True,
        "dg_count": 1,
        "drive_count": 4,
        "digest": "FC-DG1-PD4-9000GB-[dg0:4]",
    }


def test_foreign_config_absent_emits_no_event() -> None:
    detector = _detector()

    events = detector.detect(None, _current(foreign_config=ForeignConfig(present=False)))

    assert all(event.category != "foreign_config_detected" for event in events)


def test_foreign_config_no_repeat_within_same_detector() -> None:
    detector = _detector()
    foreign_config = _foreign_config_present()

    detector.detect(None, _current(foreign_config=foreign_config))
    second_events = detector.detect(_previous(), _current(foreign_config=foreign_config))

    assert all(event.category != "foreign_config_detected" for event in second_events)


def test_foreign_config_clearing_emits_info_event() -> None:
    detector = _detector()
    detector.detect(None, _current(foreign_config=_foreign_config_present()))

    clear_events = detector.detect(
        _previous(),
        _current(foreign_config=ForeignConfig(present=False)),
    )

    foreign_events = [
        event for event in clear_events if event.category == "foreign_config_detected"
    ]
    assert len(foreign_events) == 1
    event = foreign_events[0]
    assert event.severity == "info"
    assert event.summary == "Foreign configuration cleared"


def test_foreign_config_none_field_treated_as_unknown() -> None:
    detector = _detector()

    events = detector.detect(None, _current(foreign_config=None))

    assert all(event.category != "foreign_config_detected" for event in events)


def test_foreign_config_probe_failure_does_not_emit_clear_or_redetect() -> None:
    detector = _detector()
    foreign_config = _foreign_config_present()

    detector.detect(None, _current(foreign_config=foreign_config))

    failure_events = detector.detect(_previous(), _current(foreign_config=None))
    assert all(event.category != "foreign_config_detected" for event in failure_events)

    recovery_events = detector.detect(_previous(), _current(foreign_config=foreign_config))
    assert all(event.category != "foreign_config_detected" for event in recovery_events)


def _foreign_config_present() -> ForeignConfig:
    return ForeignConfig(
        present=True,
        dg_count=1,
        drive_count=4,
        total_size_bytes=9_000_000_000_000,
        disk_groups=[ForeignConfigDiskGroup(dg_id=0, drive_count=4, size_bytes=9_000_000_000_000)],
        digest="FC-DG1-PD4-9000GB-[dg0:4]",
    )


def _previous() -> ControllerSnapshot:
    snapshot = ControllerSnapshot(
        captured_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number="SV00000001",
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state="Off",
        cv_present=True,
        bbu_present=False,
        roc_temperature_celsius=78,
    )
    snapshot.virtual_drives = [
        VirtualDriveSnapshot(
            vd_id=0,
            name="raid5",
            raid_level="RAID5",
            size_bytes=1_000_000_000,
            state="Optl",
            access="RW",
            cache="RWBD",
        )
    ]
    snapshot.physical_drives = [
        PhysicalDriveSnapshot(
            enclosure_id=252,
            slot_id=4,
            device_id=32,
            model="ST4000NM000",
            serial_number="SN0001",
            firmware_version="SN04",
            size_bytes=4_000_000_000_000,
            interface="SAS",
            media_type="HDD",
            state="Onln",
            temperature_celsius=35,
            media_errors=0,
            other_errors=0,
            predictive_failures=0,
            smart_alert=False,
            sas_address="5000c50000000001",
        )
    ]
    snapshot.cachevault = CacheVaultSnapshot(
        type="CVPM02",
        state="Optimal",
        temperature_celsius=40,
        pack_energy="332 J",
        capacitance_percent=89,
        replacement_required=False,
        next_learn_cycle=None,
    )
    return snapshot


def _current(*, foreign_config: ForeignConfig | None) -> StorcliSnapshot:
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
            alarm_state="Off",
            cv_present=True,
            bbu_present=False,
            roc_temperature_celsius=78,
        ),
        virtual_drives=[
            VirtualDrive(
                vd_id=0,
                name="raid5",
                raid_level="RAID5",
                size_bytes=1_000_000_000,
                state="Optl",
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
                serial_number="SN0001",
                firmware_version="SN04",
                size_bytes=4_000_000_000_000,
                interface="SAS",
                media_type="HDD",
                state="Onln",
                temperature_celsius=35,
                media_errors=0,
                other_errors=0,
                predictive_failures=0,
                smart_alert=False,
                sas_address="5000c50000000001",
            )
        ],
        cachevault=CacheVault(
            type="CVPM02",
            state="Optimal",
            temperature_celsius=40,
            pack_energy="332 J",
            capacitance_percent=89,
            replacement_required=False,
            next_learn_cycle=None,
        ),
        bbu=None,
        foreign_config=foreign_config,
        captured_at=datetime(2026, 4, 25, 12, 5, tzinfo=UTC),
    )
