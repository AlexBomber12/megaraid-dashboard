from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    PhysicalDriveMetricsDaily,
    PhysicalDriveMetricsHourly,
    PhysicalDriveSnapshot,
)
from megaraid_dashboard.db.retention import (
    _HourlyMetricsAccumulator,
    _RawMetricsAccumulator,
    _require_aware_utc,
    _temperature_summary,
    downsample_to_daily,
    downsample_to_hourly,
    prune_hourly_metrics,
    prune_raw_snapshots,
)


def test_require_aware_utc_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive datetimes"):
        _require_aware_utc(datetime(2026, 5, 15, 12, 0))


def test_temperature_summary_returns_none_for_empty() -> None:
    assert _temperature_summary([]) == (None, None, None)


def test_temperature_summary_returns_min_max_avg() -> None:
    assert _temperature_summary([30, 40, 50]) == (30, 50, 40.0)


def test_raw_accumulator_keeps_older_serial_on_out_of_order_capture() -> None:
    bucket = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    accumulator = _RawMetricsAccumulator(
        bucket_start=bucket,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN-OLD",
    )
    drive_new = PhysicalDriveSnapshot(
        enclosure_id=252,
        slot_id=4,
        device_id=32,
        model="ST",
        serial_number="SN-NEW",
        firmware_version="FW",
        size_bytes=1,
        interface="SAS",
        media_type="HDD",
        state="Onln",
        temperature_celsius=30,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="addr",
    )
    accumulator.add(drive_new, captured_at=bucket + timedelta(minutes=10))
    assert accumulator.serial_number == "SN-NEW"

    drive_older = PhysicalDriveSnapshot(
        enclosure_id=252,
        slot_id=4,
        device_id=32,
        model="ST",
        serial_number="SN-OLDER",
        firmware_version="FW",
        size_bytes=1,
        interface="SAS",
        media_type="HDD",
        state="Onln",
        temperature_celsius=None,  # also exercises temperature is None branch
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="addr",
    )
    accumulator.add(drive_older, captured_at=bucket + timedelta(minutes=5))
    assert accumulator.serial_number == "SN-NEW"
    assert accumulator.temperatures == [30]


def test_hourly_accumulator_handles_none_and_out_of_order_buckets() -> None:
    bucket = datetime(2026, 5, 15, 0, 0, tzinfo=UTC)
    accumulator = _HourlyMetricsAccumulator(
        bucket_start=bucket,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN-OLD",
    )

    later = PhysicalDriveMetricsHourly(
        bucket_start=bucket + timedelta(hours=2),
        enclosure_id=252,
        slot_id=4,
        serial_number="SN-NEW",
        temperature_celsius_min=20,
        temperature_celsius_max=30,
        temperature_celsius_avg=25.0,
        temperature_sample_count=2,
        media_errors_max=1,
        other_errors_max=1,
        predictive_failures_max=1,
        sample_count=2,
    )
    accumulator.add(later)
    assert accumulator.serial_number == "SN-NEW"

    earlier_no_temp = PhysicalDriveMetricsHourly(
        bucket_start=bucket + timedelta(hours=1),
        enclosure_id=252,
        slot_id=4,
        serial_number="SN-OLDER",
        temperature_celsius_min=None,
        temperature_celsius_max=None,
        temperature_celsius_avg=None,
        temperature_sample_count=0,
        media_errors_max=0,
        other_errors_max=0,
        predictive_failures_max=0,
        sample_count=1,
    )
    accumulator.add(earlier_no_temp)
    assert accumulator.serial_number == "SN-NEW"
    assert accumulator.temperature_mins == [20]
    assert accumulator.temperature_maxes == [30]


def test_prune_raw_snapshots_returns_zero_when_nothing_to_prune(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    assert prune_raw_snapshots(session, now_utc=now, retention_days=30) == 0


def test_prune_hourly_metrics_returns_zero_when_nothing_to_prune(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    assert prune_hourly_metrics(session, now_utc=now, retention_days=365) == 0


def test_downsample_to_hourly_updates_existing_metric(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    bucket = (now - timedelta(days=31)).replace(minute=0, second=0, microsecond=0)
    session.add(
        ControllerSnapshot(
            captured_at=bucket.replace(minute=5),
            model_name="LSI",
            serial_number="SV001",
            firmware_version="FW",
            bios_version="BIOS",
            driver_version="DRV",
            alarm_state="Off",
            cv_present=True,
            bbu_present=True,
            physical_drives=[
                PhysicalDriveSnapshot(
                    enclosure_id=252,
                    slot_id=4,
                    device_id=32,
                    model="ST",
                    serial_number="SN0001",
                    firmware_version="FW",
                    size_bytes=1,
                    interface="SAS",
                    media_type="HDD",
                    state="Onln",
                    temperature_celsius=30,
                    media_errors=0,
                    other_errors=0,
                    predictive_failures=0,
                    smart_alert=False,
                    sas_address="addr",
                )
            ],
        )
    )
    session.commit()

    downsample_to_hourly(session, now_utc=now)
    session.commit()

    session.add(
        ControllerSnapshot(
            captured_at=bucket.replace(minute=35),
            model_name="LSI",
            serial_number="SV002",
            firmware_version="FW",
            bios_version="BIOS",
            driver_version="DRV",
            alarm_state="Off",
            cv_present=True,
            bbu_present=True,
            physical_drives=[
                PhysicalDriveSnapshot(
                    enclosure_id=252,
                    slot_id=4,
                    device_id=32,
                    model="ST",
                    serial_number="SN0001",
                    firmware_version="FW",
                    size_bytes=1,
                    interface="SAS",
                    media_type="HDD",
                    state="Onln",
                    temperature_celsius=45,
                    media_errors=5,
                    other_errors=2,
                    predictive_failures=1,
                    smart_alert=False,
                    sas_address="addr",
                )
            ],
        )
    )
    session.commit()

    downsample_to_hourly(session, now_utc=now)
    session.commit()

    metrics = session.scalars(select(PhysicalDriveMetricsHourly)).all()
    assert len(metrics) == 1
    assert metrics[0].temperature_celsius_max == 45
    assert metrics[0].media_errors_max == 5


def test_downsample_to_daily_updates_existing_metric(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    day = (now - timedelta(days=366)).replace(hour=0, minute=0, second=0, microsecond=0)
    session.add_all(
        [
            PhysicalDriveMetricsHourly(
                bucket_start=day.replace(hour=1),
                enclosure_id=252,
                slot_id=4,
                serial_number="SN0001",
                temperature_celsius_min=30,
                temperature_celsius_max=30,
                temperature_celsius_avg=30.0,
                temperature_sample_count=1,
                media_errors_max=1,
                other_errors_max=1,
                predictive_failures_max=1,
                sample_count=1,
            ),
        ]
    )
    session.commit()

    downsample_to_daily(session, now_utc=now)
    session.commit()

    # Now add another hourly metric in the same day window and re-run to hit the
    # "update existing daily" branch.
    session.add(
        PhysicalDriveMetricsHourly(
            bucket_start=day.replace(hour=5),
            enclosure_id=252,
            slot_id=4,
            serial_number="SN0001",
            temperature_celsius_min=40,
            temperature_celsius_max=50,
            temperature_celsius_avg=45.0,
            temperature_sample_count=2,
            media_errors_max=3,
            other_errors_max=4,
            predictive_failures_max=5,
            sample_count=4,
        )
    )
    session.commit()

    downsample_to_daily(session, now_utc=now)
    session.commit()

    metrics = session.scalars(select(PhysicalDriveMetricsDaily)).all()
    assert len(metrics) == 1
    assert metrics[0].temperature_celsius_max == 50
    assert metrics[0].temperature_celsius_min == 30
    assert metrics[0].sample_count == 5
