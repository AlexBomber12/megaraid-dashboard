from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from megaraid_dashboard.db import (
    AlertSent,
    Base,
    CacheVaultSnapshot,
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
    get_sessionmaker,
    upsert_alert_sent,
)

EXPECTED_TABLES = {
    "alerts_sent",
    "audit_logs",
    "controller_snapshots",
    "cv_snapshots",
    "events",
    "pd_metrics_daily",
    "pd_metrics_hourly",
    "pd_snapshots",
    "vd_snapshots",
}


def test_schema_applies_cleanly_via_metadata(engine: Engine) -> None:
    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_controller_snapshot_with_children_round_trips(session: Session) -> None:
    snapshot = _controller_snapshot(datetime(2026, 4, 25, 12, 0, tzinfo=UTC))
    snapshot.virtual_drives = [
        VirtualDriveSnapshot(
            vd_id=0,
            name="raid5",
            raid_level="RAID5",
            size_bytes=1000,
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
            temperature_celsius=38,
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
        next_learn_cycle=datetime(2026, 5, 9, 20, 21, tzinfo=UTC),
    )
    session.add(snapshot)
    session.commit()

    stored = session.scalars(select(ControllerSnapshot)).one()

    assert stored.model_name == "LSI MegaRAID SAS 9270CV-8i"
    assert stored.virtual_drives[0].raid_level == "RAID5"
    assert stored.physical_drives[0].serial_number == "SN0001"
    assert stored.cachevault is not None
    assert stored.cachevault.capacitance_percent == 89
    assert stored.captured_at.tzinfo is not None


def test_cascade_delete_removes_children(session: Session) -> None:
    snapshot = _controller_snapshot(datetime(2026, 4, 25, 12, 0, tzinfo=UTC))
    snapshot.virtual_drives = [
        VirtualDriveSnapshot(
            vd_id=0,
            name="raid5",
            raid_level="RAID5",
            size_bytes=1000,
            state="Optl",
            access="RW",
            cache="RWBD",
        )
    ]
    session.add(snapshot)
    session.commit()

    session.delete(snapshot)
    session.commit()

    assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(VirtualDriveSnapshot)) == 0


def test_alert_upsert_reuses_unique_fingerprint(session: Session) -> None:
    first = upsert_alert_sent(
        session,
        severity="critical",
        category="smart_alert",
        subject="PD e252:s4",
        fingerprint="fingerprint-1",
        recipient="ops@example.test",
    )
    second = upsert_alert_sent(
        session,
        severity="critical",
        category="smart_alert",
        subject="PD e252:s4",
        fingerprint="fingerprint-1",
        recipient="ops@example.test",
        smtp_message_id="message-2",
    )
    session.commit()

    assert second.id == first.id
    assert second.smtp_message_id == "message-2"
    assert session.scalar(select(func.count()).select_from(AlertSent)) == 1


def test_alert_upsert_handles_conflict_across_sessions(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    session_factory = get_sessionmaker(engine)
    suppressed_until = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    try:
        with session_factory() as first_session:
            upsert_alert_sent(
                first_session,
                severity="warning",
                category="temperature",
                subject="PD e252:s4",
                fingerprint="fingerprint-2",
                recipient="ops@example.test",
            )
            first_session.commit()

        with session_factory() as second_session:
            alert = upsert_alert_sent(
                second_session,
                severity="critical",
                category="smart_alert",
                subject="PD e252:s4",
                fingerprint="fingerprint-2",
                recipient="oncall@example.test",
                smtp_message_id="message-2",
                suppressed_until=suppressed_until,
            )
            second_session.commit()

            assert alert.severity == "critical"
            assert alert.recipient == "oncall@example.test"
            assert alert.smtp_message_id == "message-2"
            assert alert.suppressed_until == suppressed_until

        with session_factory() as verification_session:
            assert verification_session.scalar(select(func.count()).select_from(AlertSent)) == 1
    finally:
        Base.metadata.drop_all(engine)


def test_naive_datetimes_are_rejected_at_insert_time(session: Session) -> None:
    session.add(_controller_snapshot(datetime(2026, 4, 25, 12, 0)))

    with pytest.raises(StatementError):
        session.flush()


def _controller_snapshot(captured_at: datetime) -> ControllerSnapshot:
    return ControllerSnapshot(
        captured_at=captured_at,
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number="SV00000001",
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state="Off",
        cv_present=True,
        bbu_present=True,
    )
