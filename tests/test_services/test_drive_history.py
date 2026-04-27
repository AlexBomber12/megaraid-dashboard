from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    PhysicalDriveMetricsDaily,
    PhysicalDriveMetricsHourly,
    PhysicalDriveSnapshot,
)
from megaraid_dashboard.services.drive_history import (
    load_drive_error_series,
    load_drive_temperature_series,
)


def test_load_drive_temperature_series_unions_layers_without_overlapping_duplicates(
    session: Session,
) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_raw(session, datetime(2026, 4, 25, 11, 10, tzinfo=UTC), temperature=50)
    _seed_raw(session, datetime(2025, 6, 1, 3, 15, tzinfo=UTC), temperature=60)
    session.add_all(
        [
            _hourly_metric(datetime(2026, 4, 25, 11, 0, tzinfo=UTC), temperature_avg=99),
            _hourly_metric(datetime(2025, 6, 1, 3, 0, tzinfo=UTC), temperature_avg=98),
            _hourly_metric(datetime(2025, 6, 1, 2, 0, tzinfo=UTC), temperature_avg=42),
            _daily_metric(datetime(2025, 6, 1, 0, 0, tzinfo=UTC), temperature_avg=30),
            _daily_metric(datetime(2025, 5, 1, 0, 0, tzinfo=UTC), temperature_avg=35),
        ]
    )
    session.commit()

    series = load_drive_temperature_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN0001",
        range_days=365,
        now_utc=now,
    )

    assert series.timestamps == (
        datetime(2025, 5, 1, 0, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 2, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 3, 15, tzinfo=UTC),
        datetime(2026, 4, 25, 11, 10, tzinfo=UTC),
    )
    assert series.average_celsius == (35.0, 42.0, 60.0, 50.0)
    assert series.raw_point_count == 2
    assert series.hourly_point_count == 1
    assert series.daily_point_count == 1


def test_load_drive_temperature_series_marks_replacement_and_falls_back_to_slot_match(
    session: Session,
) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_raw(
        session,
        datetime(2026, 4, 25, 10, 0, tzinfo=UTC),
        temperature=40,
        serial_number="SN-OLD",
    )
    _seed_raw(
        session,
        datetime(2026, 4, 25, 11, 0, tzinfo=UTC),
        temperature=45,
        serial_number="SN-NEW",
    )
    session.commit()

    series = load_drive_temperature_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN-NEW",
        range_days=7,
        now_utc=now,
    )

    assert series.average_celsius == (40.0, 45.0)
    assert len(series.replacement_markers) == 1
    assert series.replacement_markers[0].timestamp == datetime(2026, 4, 25, 11, 0, tzinfo=UTC)
    assert series.replacement_markers[0].previous_serial_number == "SN-OLD"
    assert series.replacement_markers[0].current_serial_number == "SN-NEW"


def test_load_drive_temperature_series_uses_now_as_upper_bound(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_raw(session, now, temperature=40)
    _seed_raw(session, datetime(2026, 4, 25, 13, 0, tzinfo=UTC), temperature=99)
    session.commit()

    series = load_drive_temperature_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN0001",
        range_days=7,
        now_utc=now,
    )

    assert series.timestamps == (now,)
    assert series.average_celsius == (40.0,)


def test_load_drive_temperature_series_uses_hourly_when_raw_temperature_is_missing(
    session: Session,
) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_raw(
        session,
        datetime(2026, 4, 25, 11, 10, tzinfo=UTC),
        temperature=None,
        media_errors=7,
    )
    session.add_all(
        [
            _hourly_metric(
                datetime(2026, 4, 25, 11, 0, tzinfo=UTC),
                temperature_avg=44,
                media_errors_max=99,
            ),
            _daily_metric(datetime(2026, 4, 25, 0, 0, tzinfo=UTC), temperature_avg=42),
        ]
    )
    session.commit()

    temperature_series = load_drive_temperature_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN0001",
        range_days=7,
        now_utc=now,
    )
    error_series = load_drive_error_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN0001",
        range_days=7,
        now_utc=now,
    )

    assert temperature_series.timestamps == (datetime(2026, 4, 25, 11, 0, tzinfo=UTC),)
    assert temperature_series.average_celsius == (44.0,)
    assert temperature_series.raw_point_count == 0
    assert temperature_series.hourly_point_count == 1
    assert temperature_series.daily_point_count == 0
    assert error_series.timestamps == (datetime(2026, 4, 25, 11, 10, tzinfo=UTC),)
    assert error_series.media_errors == (7,)
    assert error_series.raw_point_count == 1
    assert error_series.hourly_point_count == 0


def test_load_drive_history_preserves_hourly_rows_for_other_serial_when_raw_overlaps(
    session: Session,
) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_raw(
        session,
        datetime(2026, 4, 25, 11, 10, tzinfo=UTC),
        temperature=45,
        serial_number="SN-NEW",
        media_errors=7,
        other_errors=8,
        predictive_failures=9,
    )
    session.add_all(
        [
            _hourly_metric(
                datetime(2026, 4, 25, 11, 0, tzinfo=UTC),
                serial_number="SN-OLD",
                temperature_avg=40,
                media_errors_max=3,
                other_errors_max=4,
                predictive_failures_max=5,
            ),
            _hourly_metric(
                datetime(2026, 4, 25, 11, 0, tzinfo=UTC),
                serial_number="SN-NEW",
                temperature_avg=99,
                media_errors_max=99,
                other_errors_max=99,
                predictive_failures_max=99,
            ),
        ]
    )
    session.commit()

    temperature_series = load_drive_temperature_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN-NEW",
        range_days=7,
        now_utc=now,
    )
    error_series = load_drive_error_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN-NEW",
        range_days=7,
        now_utc=now,
    )

    assert temperature_series.timestamps == (
        datetime(2026, 4, 25, 11, 0, tzinfo=UTC),
        datetime(2026, 4, 25, 11, 10, tzinfo=UTC),
    )
    assert temperature_series.serial_numbers == ("SN-OLD", "SN-NEW")
    assert temperature_series.average_celsius == (40.0, 45.0)
    assert temperature_series.raw_point_count == 1
    assert temperature_series.hourly_point_count == 1
    assert temperature_series.daily_point_count == 0
    assert len(temperature_series.replacement_markers) == 1
    assert temperature_series.replacement_markers[0].timestamp == datetime(
        2026, 4, 25, 11, 10, tzinfo=UTC
    )
    assert temperature_series.replacement_markers[0].previous_serial_number == "SN-OLD"
    assert temperature_series.replacement_markers[0].current_serial_number == "SN-NEW"
    assert error_series.timestamps == temperature_series.timestamps
    assert error_series.serial_numbers == ("SN-OLD", "SN-NEW")
    assert error_series.media_errors == (3, 7)
    assert error_series.other_errors == (4, 8)
    assert error_series.predictive_failures == (5, 9)
    assert error_series.raw_point_count == 1
    assert error_series.hourly_point_count == 1
    assert error_series.daily_point_count == 0
    assert error_series.replacement_markers == temperature_series.replacement_markers


def test_load_drive_history_preserves_same_bucket_acquisition_order(
    session: Session,
) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            _hourly_metric(
                datetime(2026, 4, 25, 11, 0, tzinfo=UTC),
                serial_number="ZZZ-OLD",
                temperature_avg=39,
                media_errors_max=1,
            ),
            _hourly_metric(
                datetime(2026, 4, 25, 11, 0, tzinfo=UTC),
                serial_number="AAA-MID",
                temperature_avg=41,
                media_errors_max=2,
            ),
            _hourly_metric(
                datetime(2026, 4, 25, 11, 0, tzinfo=UTC),
                serial_number="SN-NEW",
                temperature_avg=45,
                media_errors_max=3,
            ),
        ]
    )
    session.commit()

    temperature_series = load_drive_temperature_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN-NEW",
        range_days=7,
        now_utc=now,
    )
    error_series = load_drive_error_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN-NEW",
        range_days=7,
        now_utc=now,
    )

    assert temperature_series.serial_numbers == ("ZZZ-OLD", "AAA-MID", "SN-NEW")
    assert temperature_series.average_celsius == (39.0, 41.0, 45.0)
    assert error_series.serial_numbers == temperature_series.serial_numbers
    assert error_series.media_errors == (1, 2, 3)
    assert [
        (
            marker.previous_serial_number,
            marker.current_serial_number,
        )
        for marker in temperature_series.replacement_markers
    ] == [("ZZZ-OLD", "AAA-MID"), ("AAA-MID", "SN-NEW")]
    assert error_series.replacement_markers == temperature_series.replacement_markers


def test_load_drive_history_preserves_daily_rows_for_other_serial_when_raw_overlaps(
    session: Session,
) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _seed_raw(
        session,
        datetime(2026, 4, 25, 11, 10, tzinfo=UTC),
        temperature=45,
        serial_number="SN-NEW",
        media_errors=7,
        other_errors=8,
        predictive_failures=9,
    )
    session.add_all(
        [
            _daily_metric(
                datetime(2026, 4, 25, 0, 0, tzinfo=UTC),
                serial_number="SN-OLD",
                temperature_avg=40,
                media_errors_max=3,
                other_errors_max=4,
                predictive_failures_max=5,
            ),
            _daily_metric(
                datetime(2026, 4, 25, 0, 0, tzinfo=UTC),
                serial_number="SN-NEW",
                temperature_avg=99,
                media_errors_max=99,
                other_errors_max=99,
                predictive_failures_max=99,
            ),
        ]
    )
    session.commit()

    temperature_series = load_drive_temperature_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN-NEW",
        range_days=7,
        now_utc=now,
    )
    error_series = load_drive_error_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN-NEW",
        range_days=7,
        now_utc=now,
    )

    assert temperature_series.timestamps == (
        datetime(2026, 4, 25, 0, 0, tzinfo=UTC),
        datetime(2026, 4, 25, 11, 10, tzinfo=UTC),
    )
    assert temperature_series.serial_numbers == ("SN-OLD", "SN-NEW")
    assert temperature_series.average_celsius == (40.0, 45.0)
    assert temperature_series.raw_point_count == 1
    assert temperature_series.hourly_point_count == 0
    assert temperature_series.daily_point_count == 1
    assert len(temperature_series.replacement_markers) == 1
    assert temperature_series.replacement_markers[0].timestamp == datetime(
        2026, 4, 25, 11, 10, tzinfo=UTC
    )
    assert temperature_series.replacement_markers[0].previous_serial_number == "SN-OLD"
    assert temperature_series.replacement_markers[0].current_serial_number == "SN-NEW"
    assert error_series.timestamps == temperature_series.timestamps
    assert error_series.serial_numbers == ("SN-OLD", "SN-NEW")
    assert error_series.media_errors == (3, 7)
    assert error_series.other_errors == (4, 8)
    assert error_series.predictive_failures == (5, 9)
    assert error_series.raw_point_count == 1
    assert error_series.hourly_point_count == 0
    assert error_series.daily_point_count == 1
    assert error_series.replacement_markers == temperature_series.replacement_markers


def test_load_drive_error_series_uses_aggregate_max_columns(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            _daily_metric(
                datetime(2025, 5, 1, 0, 0, tzinfo=UTC),
                temperature_avg=35,
                media_errors_max=2,
                other_errors_max=3,
                predictive_failures_max=4,
            ),
            _hourly_metric(
                datetime(2025, 6, 1, 2, 0, tzinfo=UTC),
                temperature_avg=42,
                media_errors_max=5,
                other_errors_max=6,
                predictive_failures_max=7,
            ),
        ]
    )
    session.commit()

    series = load_drive_error_series(
        session,
        enclosure_id=252,
        slot_id=4,
        current_serial_number="SN0001",
        range_days=365,
        now_utc=now,
    )

    assert series.media_errors == (2, 5)
    assert series.other_errors == (3, 6)
    assert series.predictive_failures == (4, 7)
    assert series.raw_point_count == 0
    assert series.hourly_point_count == 1
    assert series.daily_point_count == 1


def _seed_raw(
    session: Session,
    captured_at: datetime,
    *,
    temperature: int | None,
    serial_number: str = "SN0001",
    media_errors: int = 1,
    other_errors: int = 2,
    predictive_failures: int = 3,
) -> None:
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
            serial_number=serial_number,
            firmware_version="SN04",
            size_bytes=4_000_000_000_000,
            interface="SAS",
            media_type="HDD",
            state="Onln",
            temperature_celsius=temperature,
            media_errors=media_errors,
            other_errors=other_errors,
            predictive_failures=predictive_failures,
            smart_alert=False,
            sas_address="5000c50000000001",
        )
    ]
    session.add(snapshot)


def _hourly_metric(
    bucket_start: datetime,
    *,
    temperature_avg: float,
    serial_number: str = "SN0001",
    media_errors_max: int = 1,
    other_errors_max: int = 2,
    predictive_failures_max: int = 3,
) -> PhysicalDriveMetricsHourly:
    return PhysicalDriveMetricsHourly(
        bucket_start=bucket_start.replace(minute=0, second=0, microsecond=0),
        enclosure_id=252,
        slot_id=4,
        serial_number=serial_number,
        temperature_celsius_min=int(temperature_avg),
        temperature_celsius_max=int(temperature_avg),
        temperature_celsius_avg=temperature_avg,
        temperature_sample_count=1,
        media_errors_max=media_errors_max,
        other_errors_max=other_errors_max,
        predictive_failures_max=predictive_failures_max,
        sample_count=5,
    )


def _daily_metric(
    bucket_start: datetime,
    *,
    temperature_avg: float,
    serial_number: str = "SN0001",
    media_errors_max: int = 1,
    other_errors_max: int = 2,
    predictive_failures_max: int = 3,
) -> PhysicalDriveMetricsDaily:
    return PhysicalDriveMetricsDaily(
        bucket_start=bucket_start.replace(hour=0, minute=0, second=0, microsecond=0),
        enclosure_id=252,
        slot_id=4,
        serial_number=serial_number,
        temperature_celsius_min=int(temperature_avg),
        temperature_celsius_max=int(temperature_avg),
        temperature_celsius_avg=temperature_avg,
        temperature_sample_count=1,
        media_errors_max=media_errors_max,
        other_errors_max=other_errors_max,
        predictive_failures_max=predictive_failures_max,
        sample_count=24,
    )
