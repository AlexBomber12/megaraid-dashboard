from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from megaraid_dashboard.db import ControllerSnapshot, PhysicalDriveMetricsHourly
from megaraid_dashboard.db.models import PhysicalDriveMetricsDaily, PhysicalDriveSnapshot
from megaraid_dashboard.db.retention import (
    downsample_to_daily,
    downsample_to_hourly,
    prune_hourly_metrics,
    prune_raw_snapshots,
)


def test_downsample_to_hourly_writes_target_window_and_is_idempotent(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 34, tzinfo=UTC)
    target_bucket = (now - timedelta(days=30)).replace(
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(hours=2)
    _seed_pd_snapshot(session, target_bucket.replace(minute=5), temperature=30)
    _seed_pd_snapshot(session, target_bucket.replace(minute=25), temperature=40)
    _seed_pd_snapshot(session, now - timedelta(days=30), temperature=60)
    _seed_pd_snapshot(session, now - timedelta(days=29), temperature=50)
    _seed_pd_snapshot(session, now - timedelta(days=40), temperature=20)
    session.commit()

    written = downsample_to_hourly(session, now_utc=now)
    downsample_to_hourly(session, now_utc=now)
    session.commit()

    metrics = session.scalars(select(PhysicalDriveMetricsHourly)).all()
    assert written == 1
    assert len(metrics) == 1
    assert metrics[0].bucket_start == target_bucket.replace(minute=0, second=0, microsecond=0)
    assert metrics[0].temperature_celsius_min == 30
    assert metrics[0].temperature_celsius_max == 40
    assert metrics[0].temperature_celsius_avg == 35
    assert metrics[0].sample_count == 2


def test_downsample_to_daily_writes_days_older_than_one_year(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    target_day = (now - timedelta(days=365)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(days=1)
    session.add_all(
        [
            _hourly_metric(target_day.replace(hour=1), 30.0, sample_count=2),
            _hourly_metric(target_day.replace(hour=2), 40.0, sample_count=2),
            _hourly_metric(now - timedelta(days=365), 60.0, sample_count=1),
            _hourly_metric(now - timedelta(days=364), 50.0, sample_count=1),
            _hourly_metric(now - timedelta(days=390), 20.0, sample_count=1),
        ]
    )
    session.commit()

    written = downsample_to_daily(session, now_utc=now)
    session.commit()

    metrics = session.scalars(select(PhysicalDriveMetricsDaily)).all()
    assert written == 1
    assert len(metrics) == 1
    assert metrics[0].bucket_start == target_day.replace(hour=0, minute=0, second=0, microsecond=0)
    assert metrics[0].temperature_celsius_avg == 35
    assert metrics[0].sample_count == 4


def test_prune_raw_snapshots_deletes_only_old_rows_and_cascades(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 34, tzinfo=UTC)
    cutoff_hour = (now - timedelta(days=30)).replace(minute=0, second=0, microsecond=0)
    _seed_pd_snapshot(session, cutoff_hour - timedelta(minutes=1), temperature=30)
    _seed_pd_snapshot(session, cutoff_hour + timedelta(minutes=5), temperature=35)
    _seed_pd_snapshot(session, now - timedelta(days=29), temperature=40)
    session.commit()

    deleted = prune_raw_snapshots(session, now_utc=now, retention_days=30)
    session.commit()

    assert deleted == 1
    assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 2
    assert session.scalar(select(func.count()).select_from(PhysicalDriveSnapshot)) == 2


def test_prune_hourly_metrics_deletes_only_old_rows(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 34, tzinfo=UTC)
    cutoff_day = (now - timedelta(days=365)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    session.add_all(
        [
            _hourly_metric(cutoff_day - timedelta(hours=1), 30.0, sample_count=1),
            _hourly_metric(cutoff_day + timedelta(hours=1), 35.0, sample_count=1),
            _hourly_metric(now - timedelta(days=364), 40.0, sample_count=1),
        ]
    )
    session.commit()

    deleted = prune_hourly_metrics(session, now_utc=now, retention_days=365)
    session.commit()

    assert deleted == 1
    assert session.scalar(select(func.count()).select_from(PhysicalDriveMetricsHourly)) == 2


def _seed_pd_snapshot(session: Session, captured_at: datetime, *, temperature: int) -> None:
    snapshot = ControllerSnapshot(
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
            temperature_celsius=temperature,
            media_errors=1,
            other_errors=2,
            predictive_failures=3,
            smart_alert=False,
            sas_address="5000c50000000001",
        )
    ]
    session.add(snapshot)


def _hourly_metric(
    bucket_start: datetime,
    temperature_avg: float,
    *,
    sample_count: int,
) -> PhysicalDriveMetricsHourly:
    return PhysicalDriveMetricsHourly(
        bucket_start=bucket_start.replace(minute=0, second=0, microsecond=0),
        enclosure_id=252,
        slot_id=4,
        serial_number="SN0001",
        temperature_celsius_min=int(temperature_avg),
        temperature_celsius_max=int(temperature_avg),
        temperature_celsius_avg=temperature_avg,
        media_errors_max=1,
        other_errors_max=2,
        predictive_failures_max=3,
        sample_count=sample_count,
    )
