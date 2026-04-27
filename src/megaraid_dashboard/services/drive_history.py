from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    PhysicalDriveMetricsDaily,
    PhysicalDriveMetricsHourly,
    PhysicalDriveSnapshot,
)


@dataclass(frozen=True)
class DriveReplacementMarker:
    timestamp: datetime
    previous_serial_number: str
    current_serial_number: str
    label: str = "Drive replaced"


@dataclass(frozen=True)
class DriveTemperatureSeries:
    timestamps: tuple[datetime, ...]
    serial_numbers: tuple[str, ...]
    average_celsius: tuple[float, ...]
    minimum_celsius: tuple[float | None, ...]
    maximum_celsius: tuple[float | None, ...]
    replacement_markers: tuple[DriveReplacementMarker, ...]
    raw_point_count: int
    hourly_point_count: int
    daily_point_count: int


@dataclass(frozen=True)
class DriveErrorSeries:
    timestamps: tuple[datetime, ...]
    serial_numbers: tuple[str, ...]
    media_errors: tuple[int, ...]
    other_errors: tuple[int, ...]
    predictive_failures: tuple[int, ...]
    replacement_markers: tuple[DriveReplacementMarker, ...]
    raw_point_count: int
    hourly_point_count: int
    daily_point_count: int


@dataclass(frozen=True)
class _RawPoint:
    timestamp: datetime
    serial_number: str
    temperature_celsius: int | None
    media_errors: int
    other_errors: int
    predictive_failures: int


@dataclass(frozen=True)
class _AggregatePoint:
    timestamp: datetime
    serial_number: str
    temperature_celsius_min: int | None
    temperature_celsius_max: int | None
    temperature_celsius_avg: float | None
    media_errors_max: int
    other_errors_max: int
    predictive_failures_max: int


@dataclass(frozen=True)
class _SelectedHistoryRows:
    raw: tuple[_RawPoint, ...]
    hourly: tuple[_AggregatePoint, ...]
    daily: tuple[_AggregatePoint, ...]


@dataclass(frozen=True)
class _SerialPoint:
    timestamp: datetime
    serial_number: str


def load_drive_temperature_series(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    current_serial_number: str,
    range_days: int,
    now_utc: datetime | None = None,
) -> DriveTemperatureSeries:
    selected = _load_selected_history_rows(
        session,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        current_serial_number=current_serial_number,
        range_days=range_days,
        now_utc=now_utc,
    )
    raw_rows, hourly_rows, daily_rows = _temperature_rows(selected)
    points = tuple(
        sorted(
            (
                _temperature_point
                for _temperature_point in (
                    *(
                        _AggregatePoint(
                            timestamp=raw.timestamp,
                            serial_number=raw.serial_number,
                            temperature_celsius_min=raw.temperature_celsius,
                            temperature_celsius_max=raw.temperature_celsius,
                            temperature_celsius_avg=float(raw.temperature_celsius)
                            if raw.temperature_celsius is not None
                            else None,
                            media_errors_max=raw.media_errors,
                            other_errors_max=raw.other_errors,
                            predictive_failures_max=raw.predictive_failures,
                        )
                        for raw in raw_rows
                    ),
                    *hourly_rows,
                    *daily_rows,
                )
                if _temperature_point.temperature_celsius_avg is not None
            ),
            key=lambda point: _series_sort_key(
                point.timestamp,
                point.serial_number,
                current_serial_number,
            ),
        )
    )

    return DriveTemperatureSeries(
        timestamps=tuple(point.timestamp for point in points),
        serial_numbers=tuple(point.serial_number for point in points),
        average_celsius=tuple(_require_float(point.temperature_celsius_avg) for point in points),
        minimum_celsius=tuple(_optional_float(point.temperature_celsius_min) for point in points),
        maximum_celsius=tuple(_optional_float(point.temperature_celsius_max) for point in points),
        replacement_markers=_replacement_markers(
            tuple(_SerialPoint(point.timestamp, point.serial_number) for point in points),
            current_serial_number=current_serial_number,
        ),
        raw_point_count=len(raw_rows),
        hourly_point_count=len(hourly_rows),
        daily_point_count=len(daily_rows),
    )


def load_drive_error_series(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    current_serial_number: str,
    range_days: int,
    now_utc: datetime | None = None,
) -> DriveErrorSeries:
    selected = _load_selected_history_rows(
        session,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        current_serial_number=current_serial_number,
        range_days=range_days,
        now_utc=now_utc,
    )
    raw_rows, hourly_rows, daily_rows = _error_rows(selected)
    points = tuple(
        sorted(
            (
                *(
                    _AggregatePoint(
                        timestamp=raw.timestamp,
                        serial_number=raw.serial_number,
                        temperature_celsius_min=raw.temperature_celsius,
                        temperature_celsius_max=raw.temperature_celsius,
                        temperature_celsius_avg=float(raw.temperature_celsius)
                        if raw.temperature_celsius is not None
                        else None,
                        media_errors_max=raw.media_errors,
                        other_errors_max=raw.other_errors,
                        predictive_failures_max=raw.predictive_failures,
                    )
                    for raw in raw_rows
                ),
                *hourly_rows,
                *daily_rows,
            ),
            key=lambda point: _series_sort_key(
                point.timestamp,
                point.serial_number,
                current_serial_number,
            ),
        )
    )
    return DriveErrorSeries(
        timestamps=tuple(point.timestamp for point in points),
        serial_numbers=tuple(point.serial_number for point in points),
        media_errors=tuple(point.media_errors_max for point in points),
        other_errors=tuple(point.other_errors_max for point in points),
        predictive_failures=tuple(point.predictive_failures_max for point in points),
        replacement_markers=_replacement_markers(
            tuple(_SerialPoint(point.timestamp, point.serial_number) for point in points),
            current_serial_number=current_serial_number,
        ),
        raw_point_count=len(raw_rows),
        hourly_point_count=len(hourly_rows),
        daily_point_count=len(daily_rows),
    )


def _load_selected_history_rows(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    current_serial_number: str,
    range_days: int,
    now_utc: datetime | None,
) -> _SelectedHistoryRows:
    if range_days <= 0:
        msg = "range_days must be positive"
        raise ValueError(msg)

    now = _require_aware_utc(now_utc or datetime.now(UTC))
    cutoff = now - timedelta(days=range_days)
    raw_rows = _load_raw_points(
        session,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        cutoff=cutoff,
    )
    hourly_rows = _load_hourly_points(
        session,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        cutoff=cutoff,
    )
    daily_rows = _load_daily_points(
        session,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        cutoff=cutoff,
    )

    selected_raw, selected_hourly, selected_daily = _select_rows_for_serial_window(
        raw_rows=raw_rows,
        hourly_rows=hourly_rows,
        daily_rows=daily_rows,
        current_serial_number=current_serial_number,
    )
    selected_raw = tuple(
        sorted(
            selected_raw,
            key=lambda row: _series_sort_key(
                row.timestamp,
                row.serial_number,
                current_serial_number,
            ),
        )
    )
    selected_hourly = tuple(
        sorted(
            selected_hourly,
            key=lambda row: _series_sort_key(
                row.timestamp,
                row.serial_number,
                current_serial_number,
            ),
        )
    )
    selected_daily = tuple(
        sorted(
            selected_daily,
            key=lambda row: _series_sort_key(
                row.timestamp,
                row.serial_number,
                current_serial_number,
            ),
        )
    )

    return _SelectedHistoryRows(
        raw=selected_raw,
        hourly=selected_hourly,
        daily=selected_daily,
    )


def _temperature_rows(
    selected: _SelectedHistoryRows,
) -> tuple[tuple[_RawPoint, ...], tuple[_AggregatePoint, ...], tuple[_AggregatePoint, ...]]:
    raw_rows = tuple(raw for raw in selected.raw if raw.temperature_celsius is not None)
    raw_covered_hours = {(_hour_bucket(raw.timestamp), raw.serial_number) for raw in raw_rows}
    hourly_rows = tuple(
        hourly
        for hourly in selected.hourly
        if hourly.temperature_celsius_avg is not None
        and (hourly.timestamp, hourly.serial_number) not in raw_covered_hours
    )
    raw_covered_days = {(_day_bucket(raw.timestamp), raw.serial_number) for raw in raw_rows}
    hourly_covered_days = {
        (_day_bucket(hourly.timestamp), hourly.serial_number) for hourly in hourly_rows
    }
    daily_rows = tuple(
        daily
        for daily in selected.daily
        if daily.temperature_celsius_avg is not None
        and (daily.timestamp, daily.serial_number) not in raw_covered_days
        and (daily.timestamp, daily.serial_number) not in hourly_covered_days
    )
    return raw_rows, hourly_rows, daily_rows


def _error_rows(
    selected: _SelectedHistoryRows,
) -> tuple[tuple[_RawPoint, ...], tuple[_AggregatePoint, ...], tuple[_AggregatePoint, ...]]:
    raw_covered_hours = {(_hour_bucket(raw.timestamp), raw.serial_number) for raw in selected.raw}
    hourly_rows = tuple(
        hourly
        for hourly in selected.hourly
        if (hourly.timestamp, hourly.serial_number) not in raw_covered_hours
    )
    raw_covered_days = {(_day_bucket(raw.timestamp), raw.serial_number) for raw in selected.raw}
    hourly_covered_days = {
        (_day_bucket(hourly.timestamp), hourly.serial_number) for hourly in hourly_rows
    }
    daily_rows = tuple(
        daily
        for daily in selected.daily
        if (daily.timestamp, daily.serial_number) not in raw_covered_days
        and (daily.timestamp, daily.serial_number) not in hourly_covered_days
    )
    return selected.raw, hourly_rows, daily_rows


def _load_raw_points(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    cutoff: datetime,
) -> tuple[_RawPoint, ...]:
    rows = session.execute(
        select(PhysicalDriveSnapshot, ControllerSnapshot.captured_at)
        .join(ControllerSnapshot, PhysicalDriveSnapshot.snapshot_id == ControllerSnapshot.id)
        .where(PhysicalDriveSnapshot.enclosure_id == enclosure_id)
        .where(PhysicalDriveSnapshot.slot_id == slot_id)
        .where(ControllerSnapshot.captured_at >= cutoff)
        .order_by(ControllerSnapshot.captured_at)
    )
    return tuple(
        _RawPoint(
            timestamp=_require_aware_utc(captured_at),
            serial_number=drive.serial_number,
            temperature_celsius=drive.temperature_celsius,
            media_errors=drive.media_errors,
            other_errors=drive.other_errors,
            predictive_failures=drive.predictive_failures,
        )
        for drive, captured_at in rows
    )


def _load_hourly_points(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    cutoff: datetime,
) -> tuple[_AggregatePoint, ...]:
    rows = session.scalars(
        select(PhysicalDriveMetricsHourly)
        .where(PhysicalDriveMetricsHourly.enclosure_id == enclosure_id)
        .where(PhysicalDriveMetricsHourly.slot_id == slot_id)
        .where(PhysicalDriveMetricsHourly.bucket_start >= cutoff)
        .order_by(PhysicalDriveMetricsHourly.bucket_start)
    )
    return tuple(
        _AggregatePoint(
            timestamp=_require_aware_utc(row.bucket_start),
            serial_number=row.serial_number,
            temperature_celsius_min=row.temperature_celsius_min,
            temperature_celsius_max=row.temperature_celsius_max,
            temperature_celsius_avg=row.temperature_celsius_avg,
            media_errors_max=row.media_errors_max,
            other_errors_max=row.other_errors_max,
            predictive_failures_max=row.predictive_failures_max,
        )
        for row in rows
    )


def _load_daily_points(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    cutoff: datetime,
) -> tuple[_AggregatePoint, ...]:
    rows = session.scalars(
        select(PhysicalDriveMetricsDaily)
        .where(PhysicalDriveMetricsDaily.enclosure_id == enclosure_id)
        .where(PhysicalDriveMetricsDaily.slot_id == slot_id)
        .where(PhysicalDriveMetricsDaily.bucket_start >= cutoff)
        .order_by(PhysicalDriveMetricsDaily.bucket_start)
    )
    return tuple(
        _AggregatePoint(
            timestamp=_require_aware_utc(row.bucket_start),
            serial_number=row.serial_number,
            temperature_celsius_min=row.temperature_celsius_min,
            temperature_celsius_max=row.temperature_celsius_max,
            temperature_celsius_avg=row.temperature_celsius_avg,
            media_errors_max=row.media_errors_max,
            other_errors_max=row.other_errors_max,
            predictive_failures_max=row.predictive_failures_max,
        )
        for row in rows
    )


def _select_rows_for_serial_window(
    *,
    raw_rows: tuple[_RawPoint, ...],
    hourly_rows: tuple[_AggregatePoint, ...],
    daily_rows: tuple[_AggregatePoint, ...],
    current_serial_number: str,
) -> tuple[tuple[_RawPoint, ...], tuple[_AggregatePoint, ...], tuple[_AggregatePoint, ...]]:
    serial_numbers = {
        *[row.serial_number for row in raw_rows],
        *[row.serial_number for row in hourly_rows],
        *[row.serial_number for row in daily_rows],
    }
    include_all_slot_serials = len(serial_numbers) > 1
    if include_all_slot_serials:
        return raw_rows, hourly_rows, daily_rows
    return (
        tuple(row for row in raw_rows if row.serial_number == current_serial_number),
        tuple(row for row in hourly_rows if row.serial_number == current_serial_number),
        tuple(row for row in daily_rows if row.serial_number == current_serial_number),
    )


def _replacement_markers(
    points: tuple[_SerialPoint, ...],
    *,
    current_serial_number: str,
) -> tuple[DriveReplacementMarker, ...]:
    markers: list[DriveReplacementMarker] = []
    previous_serial_number: str | None = None
    for point in sorted(
        points,
        key=lambda item: _series_sort_key(
            item.timestamp,
            item.serial_number,
            current_serial_number,
        ),
    ):
        if previous_serial_number is None:
            previous_serial_number = point.serial_number
            continue
        if previous_serial_number == point.serial_number:
            continue
        markers.append(
            DriveReplacementMarker(
                timestamp=point.timestamp,
                previous_serial_number=previous_serial_number,
                current_serial_number=point.serial_number,
            )
        )
        previous_serial_number = point.serial_number
    return tuple(markers)


def _series_sort_key(
    timestamp: datetime,
    serial_number: str,
    current_serial_number: str,
) -> tuple[datetime, int, str]:
    return timestamp, 1 if serial_number == current_serial_number else 0, serial_number


def _optional_float(value: int | float | None) -> float | None:
    return float(value) if value is not None else None


def _require_float(value: float | None) -> float:
    if value is None:
        msg = "temperature average must be present before serialization"
        raise ValueError(msg)
    return value


def _hour_bucket(value: datetime) -> datetime:
    return _require_aware_utc(value).replace(minute=0, second=0, microsecond=0)


def _day_bucket(value: datetime) -> datetime:
    return _require_aware_utc(value).replace(hour=0, minute=0, second=0, microsecond=0)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "naive datetimes are not allowed; use timezone-aware UTC datetimes"
        raise ValueError(msg)
    return value.astimezone(UTC)
