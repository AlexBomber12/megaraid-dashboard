from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.drive_detail import (
    _load_backplane_layout,
    _load_error_sparkline,
    _neighbor_drive_url,
    _require_aware_utc,
    _speed_value,
    load_drive_detail_view_model,
)


@pytest.fixture(autouse=True)
def drive_detail_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-bcrypt-hash")
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("TEMP_WARNING_CELSIUS", "55")
    monkeypatch.setenv("TEMP_CRITICAL_CELSIUS", "60")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_drive_detail_view_model_smoke_healthy_drive(session: Session) -> None:
    _seed_snapshot(session)

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="1.2.3",
    )

    assert view_model.page_title == "Slot 252:4 (S4)"
    assert view_model.page_subtitle == "ST1000-4 / SN0004"
    assert view_model.health.state == "ONLN"
    assert view_model.health.state_severity == "optimal"
    assert view_model.health.summary_text == "Drive functioning normally."
    assert view_model.health.can_locate_start is True
    assert view_model.identity.serial_number == "SN0004"
    assert view_model.identity.raw_size_text == "1.0 TB"
    assert view_model.connection.sas_address == "5000c5000004"
    assert view_model.position.dg_span_row_text == "0 : 0 : 4"
    assert view_model.position.backplane_layout[4].is_this is True
    assert view_model.system_health.app_version == "1.2.3"
    assert view_model.auto_refresh_seconds == 30


def test_degraded_failed_drive_is_critical_and_replace_can_begin(session: Session) -> None:
    _seed_snapshot(session, states={4: "Failed"})

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )

    assert view_model.health.state_severity == "critical"
    assert view_model.replace.can_begin is True


def test_temperature_at_56c_is_warning(session: Session) -> None:
    _seed_snapshot(session, temperatures={4: 56})

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )

    assert view_model.health.temperature_celsius == 56
    assert view_model.health.temperature_severity == "warning"


def test_error_sparkline_shows_incremental_growth(session: Session) -> None:
    captured_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_snapshot(session, captured_at=captured_at - timedelta(days=2), media_errors={4: 1})
    _seed_snapshot(session, captured_at=captured_at - timedelta(days=1), media_errors={4: 1})
    _seed_snapshot(session, captured_at=captured_at, media_errors={4: 3})

    sparkline = _load_error_sparkline(session, serial_number="SN0004", days=30)

    assert sparkline.current_total == 3
    assert sparkline.media_errors == 3
    assert sparkline.points[-3].total_count == 1
    assert sparkline.points[-3].incremental_delta == 1
    assert sparkline.points[-2].incremental_delta == 0
    assert sparkline.points[-1].total_count == 3
    assert sparkline.points[-1].incremental_delta == 2


def test_error_sparkline_all_zero_still_has_flat_points(session: Session) -> None:
    _seed_snapshot(session)

    sparkline = _load_error_sparkline(session, serial_number="SN0004", days=30)

    assert len(sparkline.points) == 30
    assert {point.total_count for point in sparkline.points} == {0}
    assert sparkline.meta_text == "Media 0 / Other 0 / BBM 0 / Shield 0"


def test_position_diagram_has_eight_entries_and_one_current_slot(session: Session) -> None:
    _seed_snapshot(session)

    layout = _load_backplane_layout(
        session,
        this_enclosure=252,
        this_slot=4,
        settings=get_settings(),
    )

    assert len(layout) == 8
    assert sum(slot.is_this for slot in layout) == 1
    assert layout[4].detail_url == "/drives/252:4"


def test_position_diagram_severity_reflects_drive_tile_severity(session: Session) -> None:
    _seed_snapshot(session, states={1: "UBad", 2: "Failed"}, temperatures={3: 61})

    layout = _load_backplane_layout(
        session,
        this_enclosure=252,
        this_slot=4,
        settings=get_settings(),
    )

    assert layout[0].severity == "optimal"
    assert layout[1].severity == "warning"
    assert layout[2].severity == "critical"
    assert layout[3].severity == "critical"


def test_advanced_actions_mark_ubad_enabled_only_for_ugood(session: Session) -> None:
    _seed_snapshot(session, states={4: "UGood", 5: "Failed"})
    ugood_view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )

    assert _action(ugood_view_model.advanced_actions, "Mark as UBad").is_enabled is True

    failed_view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=5,
        settings=get_settings(),
        app_version="test",
    )

    failed_mark_ubad = _action(failed_view_model.advanced_actions, "Mark as UBad")
    assert failed_mark_ubad.is_enabled is False
    assert failed_mark_ubad.disabled_reason is not None


def test_replace_wizard_step_is_loaded_from_latest_mid_flow_event(session: Session) -> None:
    captured_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_snapshot(session, captured_at=captured_at)

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )
    assert view_model.replace.current_step is None

    session.add(
        Event(
            occurred_at=captured_at + timedelta(minutes=1),
            severity="info",
            category="drive_replace_insert_pending",
            subject="drive 252:4",
            summary="replacement insert pending",
        )
    )
    session.commit()

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )
    assert view_model.replace.can_begin is False
    assert view_model.replace.current_step == 3


def test_prev_next_drive_urls_do_not_wrap(session: Session) -> None:
    _seed_snapshot(session)

    middle = _load_slot(session, 4)
    first = _load_slot(session, 0)
    last = _load_slot(session, 7)

    assert middle.prev_drive_url == "/drives/252:3"
    assert middle.next_drive_url == "/drives/252:5"
    assert first.prev_drive_url is None
    assert first.next_drive_url == "/drives/252:1"
    assert last.prev_drive_url == "/drives/252:6"
    assert last.next_drive_url is None


def test_link_speed_is_degraded_when_lower_than_device_speed(session: Session) -> None:
    snapshot = _seed_snapshot(session)
    drive = snapshot.physical_drives[4]
    drive.device_speed_text = "6.0Gb/s"  # type: ignore[attr-defined]
    drive.link_speed_text = "3.0Gb/s"  # type: ignore[attr-defined]

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )

    assert view_model.connection.device_speed_text == "6.0Gb/s"
    assert view_model.connection.link_speed_text == "3.0Gb/s"
    assert view_model.connection.link_speed_is_degraded is True


def test_loader_validates_range_and_missing_data(session: Session) -> None:
    with pytest.raises(ValueError, match="range_days must be positive"):
        load_drive_detail_view_model(
            session,
            enclosure_id=252,
            slot_id=4,
            settings=get_settings(),
            app_version="test",
            range_days=0,
        )

    with pytest.raises(LookupError, match="no controller snapshot"):
        load_drive_detail_view_model(
            session,
            enclosure_id=252,
            slot_id=4,
            settings=get_settings(),
            app_version="test",
        )

    _seed_snapshot(session)
    with pytest.raises(LookupError, match="not found"):
        load_drive_detail_view_model(
            session,
            enclosure_id=252,
            slot_id=99,
            settings=get_settings(),
            app_version="test",
        )


def test_error_sparkline_validates_days_and_handles_unknown_serial(session: Session) -> None:
    with pytest.raises(ValueError, match="days must be positive"):
        _load_error_sparkline(session, serial_number="SN0004", days=0)

    sparkline = _load_error_sparkline(session, serial_number="UNKNOWN", days=2)

    assert len(sparkline.points) == 2
    assert sparkline.current_total == 0


def test_backplane_layout_defaults_without_snapshot_or_settings(session: Session) -> None:
    layout = _load_backplane_layout(session, this_enclosure=252, this_slot=7)

    assert len(layout) == 8
    assert layout[7].is_this is True
    assert {slot.severity for slot in layout} == {"neutral"}


def test_locate_state_tracks_latest_operator_action(session: Session) -> None:
    captured_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_snapshot(session, captured_at=captured_at)
    session.add_all(
        [
            Event(
                occurred_at=captured_at + timedelta(minutes=1),
                severity="info",
                category="operator_action",
                subject="Operator action",
                summary="locate start drive 252:4",
            ),
            Event(
                occurred_at=captured_at + timedelta(minutes=2),
                severity="info",
                category="operator_action",
                subject="Operator action",
                summary="locate stop drive 252:4",
            ),
        ]
    )
    session.commit()

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )

    assert view_model.health.locate_active is False
    assert view_model.health.can_locate_start is True
    assert view_model.health.can_locate_stop is False


def test_smart_errors_and_unconfigured_sas_drive_populate_detail_text(session: Session) -> None:
    _seed_snapshot(
        session,
        states={4: "Mystery", 5: "Rebuild"},
        media_errors={4: 1, 6: 1},
        other_errors={4: 2},
        predictive_failures={4: 3},
        smart_alerts={4: True},
        disk_groups={4: None},
        interfaces={4: "SAS", 5: "FC"},
    )

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )
    rebuild_view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=5,
        settings=get_settings(),
        app_version="test",
    )
    optimal_with_errors_view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=6,
        settings=get_settings(),
        app_version="test",
    )

    assert view_model.health.state_severity == "warning"
    assert view_model.health.smart_status == "ALERT"
    assert view_model.health.smart_severity == "critical"
    assert "1 historical media error" in view_model.health.summary_text
    assert "2 other errors" in view_model.health.summary_text
    assert "3 predictive failures" in view_model.health.summary_text
    assert "S.M.A.R.T. alert is active." in view_model.health.summary_text
    assert view_model.health.state_subtitle == "Unconfigured drive."
    assert view_model.identity.manufacturer_text == "SAS"
    assert rebuild_view_model.health.state_severity == "warning"
    assert rebuild_view_model.identity.manufacturer_text == "FC"
    assert optimal_with_errors_view_model.health.summary_text == "1 historical media error."


def test_replace_wizard_operator_events_and_completion(session: Session) -> None:
    captured_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_snapshot(session, captured_at=captured_at)
    session.add(
        Event(
            occurred_at=captured_at + timedelta(minutes=1),
            severity="info",
            category="operator_action",
            subject="Operator action",
            summary="replace step offline drive 252:4 serial SN0004 succeeded",
        )
    )
    session.commit()

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )
    assert view_model.replace.current_step == 2

    session.add(
        Event(
            occurred_at=captured_at + timedelta(minutes=2),
            severity="info",
            category="operator_action",
            subject="Operator action",
            summary="replace step insert drive 252:4 serial SN0004 succeeded",
        )
    )
    session.commit()

    completed_view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )
    assert completed_view_model.replace.current_step is None

    session.add(
        Event(
            occurred_at=captured_at + timedelta(minutes=3),
            severity="info",
            category="operator_action",
            subject="Operator action",
            summary="replace step inspect drive 252:4 serial SN0004 succeeded",
        )
    )
    session.commit()

    unknown_step_view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )
    assert unknown_step_view_model.replace.current_step is None


def test_virtual_drive_subtitle_and_custom_identity_extras(session: Session) -> None:
    snapshot = _seed_snapshot(session, with_virtual_drive=True)
    drive = snapshot.physical_drives[4]
    drive.wwn = "wwn-4"  # type: ignore[attr-defined]
    drive.raw_size_text = "931.5 GB"  # type: ignore[attr-defined]
    drive.coerced_size_text = ""  # type: ignore[attr-defined]
    drive.logical_sector_size_text = "512 B"  # type: ignore[attr-defined]
    drive.connector_text = "Port 0-3 x1 (port 7)"  # type: ignore[attr-defined]

    view_model = load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=4,
        settings=get_settings(),
        app_version="test",
    )

    assert view_model.health.state_subtitle == "Member of VD 0. RAID5. DG 0."
    assert view_model.identity.wwn == "wwn-4"
    assert view_model.identity.raw_size_text == "931.5 GB"
    assert view_model.identity.coerced_size_text == "1.0 TB"
    assert view_model.identity.logical_sector_size_text == "512 B"
    assert view_model.position.port_text == "Port 0-3 x1 (port 7) (device 14)"


def test_small_helper_edge_cases() -> None:
    drive = PhysicalDriveSnapshot(
        snapshot_id=1,
        enclosure_id=252,
        slot_id=4,
        device_id=14,
        model="ST1000",
        serial_number="SN0004",
        firmware_version="1.0",
        size_bytes=1_000_000_000_000,
        interface="SATA",
        media_type="HDD",
        state="Onln",
        disk_group_id=0,
        temperature_celsius=40,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="5000c5000004",
    )

    assert _neighbor_drive_url([], drive, offset=1) is None
    assert _speed_value("not negotiated") == 0.0
    assert _speed_value("1.2.3Gb/s") == 0.0
    with pytest.raises(ValueError, match="timezone"):
        _require_aware_utc(datetime(2026, 4, 25, 12, 0))


def _load_slot(session: Session, slot_id: int):
    return load_drive_detail_view_model(
        session,
        enclosure_id=252,
        slot_id=slot_id,
        settings=get_settings(),
        app_version="test",
    )


def _action(buttons: list, label: str):
    for button in buttons:
        if button.label == label:
            return button
    raise AssertionError(f"missing action: {label}")


def _seed_snapshot(
    session: Session,
    *,
    captured_at: datetime = datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
    states: dict[int, str] | None = None,
    temperatures: dict[int, int | None] | None = None,
    media_errors: dict[int, int] | None = None,
    other_errors: dict[int, int] | None = None,
    predictive_failures: dict[int, int] | None = None,
    smart_alerts: dict[int, bool] | None = None,
    disk_groups: dict[int, int | None] | None = None,
    interfaces: dict[int, str] | None = None,
    with_virtual_drive: bool = False,
) -> ControllerSnapshot:
    resolved_states = states or {}
    resolved_temperatures = temperatures or {}
    resolved_media_errors = media_errors or {}
    resolved_other_errors = other_errors or {}
    resolved_predictive_failures = predictive_failures or {}
    resolved_smart_alerts = smart_alerts or {}
    resolved_disk_groups = disk_groups or {}
    resolved_interfaces = interfaces or {}
    snapshot = ControllerSnapshot(
        captured_at=captured_at,
        model_name="MegaRAID SAS9270CV-8i",
        serial_number="CTRL-SN",
        firmware_version="23.34.0-0019",
        bios_version="1.0",
        driver_version="07.727",
        alarm_state="Off",
        cv_present=True,
        bbu_present=False,
        roc_temperature_celsius=78,
    )
    if with_virtual_drive:
        snapshot.virtual_drives = [
            VirtualDriveSnapshot(
                vd_id=0,
                name="VD0",
                raid_level="RAID5",
                size_bytes=8_000_000_000_000,
                state="Optl",
                access="RW",
                cache="RWBD",
            )
        ]
    snapshot.physical_drives = [
        PhysicalDriveSnapshot(
            enclosure_id=252,
            slot_id=slot_id,
            device_id=10 + slot_id,
            model=f"ST1000-{slot_id}",
            serial_number=f"SN000{slot_id}",
            firmware_version="1.0",
            size_bytes=1_000_000_000_000,
            interface=resolved_interfaces.get(slot_id, "SATA"),
            media_type="HDD",
            state=resolved_states.get(slot_id, "Onln"),
            disk_group_id=resolved_disk_groups.get(slot_id, 0),
            temperature_celsius=resolved_temperatures.get(slot_id, 40),
            media_errors=resolved_media_errors.get(slot_id, 0),
            other_errors=resolved_other_errors.get(slot_id, 0),
            predictive_failures=resolved_predictive_failures.get(slot_id, 0),
            smart_alert=resolved_smart_alerts.get(slot_id, False),
            sas_address=f"5000c500000{slot_id}",
        )
        for slot_id in range(8)
    ]
    session.add(snapshot)
    session.commit()
    return snapshot
