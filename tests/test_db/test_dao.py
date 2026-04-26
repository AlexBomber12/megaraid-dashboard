from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from megaraid_dashboard.db import (
    AuditLog,
    CacheVaultSnapshot,
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    PhysicalDriveTempState,
    VirtualDriveSnapshot,
    clear_temp_state_for_slot,
    get_latest_snapshot,
    get_temp_state,
    insert_snapshot,
    list_recent_snapshots,
    record_audit,
    record_event,
    upsert_temp_state,
)
from megaraid_dashboard.storcli import StorcliSnapshot


def test_insert_snapshot_creates_expected_child_rows(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    insert_snapshot(session, sample_snapshot, store_raw=True, raw_payload={"Controllers": []})
    session.commit()

    assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(VirtualDriveSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(PhysicalDriveSnapshot)) == 8
    assert session.scalar(select(func.count()).select_from(CacheVaultSnapshot)) == 1
    assert session.scalars(select(ControllerSnapshot)).one().raw_json == {"Controllers": []}
    assert session.scalars(select(CacheVaultSnapshot)).one().capacitance_percent == 89


def test_get_latest_snapshot_returns_most_recent(session: Session) -> None:
    older = _controller_snapshot(datetime(2026, 4, 24, 12, 0, tzinfo=UTC), serial="old")
    newer = _controller_snapshot(datetime(2026, 4, 25, 12, 0, tzinfo=UTC), serial="new")
    session.add_all([older, newer])
    session.commit()

    latest = get_latest_snapshot(session)

    assert latest is not None
    assert latest.serial_number == "new"


def test_list_recent_snapshots_orders_descending(session: Session) -> None:
    snapshots = [
        _controller_snapshot(datetime(2026, 4, 23, 12, 0, tzinfo=UTC), serial="oldest"),
        _controller_snapshot(datetime(2026, 4, 25, 12, 0, tzinfo=UTC), serial="newest"),
        _controller_snapshot(datetime(2026, 4, 24, 12, 0, tzinfo=UTC), serial="middle"),
    ]
    session.add_all(snapshots)
    session.commit()

    recent = list_recent_snapshots(session, limit=2)

    assert [snapshot.serial_number for snapshot in recent] == ["newest", "middle"]


def test_record_event_writes_severity_and_category(session: Session) -> None:
    record_event(
        session,
        severity="warning",
        category="temperature",
        subject="PD e252:s4",
        summary="Drive temperature is elevated",
    )
    session.commit()

    event = session.scalars(select(Event)).one()
    assert event.severity == "warning"
    assert event.category == "temperature"


def test_temperature_state_upsert_and_clear(session: Session) -> None:
    first = upsert_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN0001",
        state="warning",
    )
    second = upsert_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN0001",
        state="critical",
    )
    session.commit()

    stored = get_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN0001",
    )

    assert second.id == first.id
    assert stored is not None
    assert stored.state == "critical"
    assert session.scalar(select(func.count()).select_from(PhysicalDriveTempState)) == 1

    deleted = clear_temp_state_for_slot(session, enclosure_id=252, slot_id=4)
    session.commit()

    assert deleted == 1
    assert (
        get_temp_state(
            session,
            enclosure_id=252,
            slot_id=4,
            serial_number="SN0001",
        )
        is None
    )


def test_record_audit_writes_command_argv_list(session: Session) -> None:
    record_audit(
        session,
        actor="admin",
        action="start_locate",
        target="PD e252:s4",
        command_argv=["storcli64", "/c0/e252/s4", "start", "locate", "J"],
        exit_code=0,
        stdout_tail="ok",
        stderr_tail="",
        duration_seconds=0.25,
        success=True,
    )
    session.commit()

    audit_log = session.scalars(select(AuditLog)).one()
    assert audit_log.command_argv == ["storcli64", "/c0/e252/s4", "start", "locate", "J"]
    assert audit_log.success is True


def _controller_snapshot(captured_at: datetime, *, serial: str) -> ControllerSnapshot:
    return ControllerSnapshot(
        captured_at=captured_at,
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number=serial,
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state="Off",
        cv_present=True,
        bbu_present=True,
    )
