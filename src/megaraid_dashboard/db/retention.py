from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    PhysicalDriveMetricsDaily,
    PhysicalDriveMetricsHourly,
    PhysicalDriveSnapshot,
)


@dataclass
class _RawMetricsAccumulator:
    bucket_start: datetime
    enclosure_id: int
    slot_id: int
    serial_number: str
    media_errors_max: int = 0
    other_errors_max: int = 0
    predictive_failures_max: int = 0
    sample_count: int = 0
    latest_seen_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))
    temperatures: list[int] = field(default_factory=list)

    def add(self, drive: PhysicalDriveSnapshot, captured_at: datetime) -> None:
        self.sample_count += 1
        self.media_errors_max = max(self.media_errors_max, drive.media_errors)
        self.other_errors_max = max(self.other_errors_max, drive.other_errors)
        self.predictive_failures_max = max(
            self.predictive_failures_max,
            drive.predictive_failures,
        )
        if drive.temperature_celsius is not None:
            self.temperatures.append(drive.temperature_celsius)
        if captured_at >= self.latest_seen_at:
            self.latest_seen_at = captured_at
            self.serial_number = drive.serial_number


@dataclass
class _HourlyMetricsAccumulator:
    bucket_start: datetime
    enclosure_id: int
    slot_id: int
    serial_number: str
    media_errors_max: int = 0
    other_errors_max: int = 0
    predictive_failures_max: int = 0
    sample_count: int = 0
    temperature_weighted_sum: float = 0.0
    temperature_sample_count: int = 0
    latest_seen_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))
    temperature_mins: list[int] = field(default_factory=list)
    temperature_maxes: list[int] = field(default_factory=list)

    def add(self, metrics: PhysicalDriveMetricsHourly) -> None:
        self.sample_count += metrics.sample_count
        self.media_errors_max = max(self.media_errors_max, metrics.media_errors_max)
        self.other_errors_max = max(self.other_errors_max, metrics.other_errors_max)
        self.predictive_failures_max = max(
            self.predictive_failures_max,
            metrics.predictive_failures_max,
        )
        if metrics.temperature_celsius_min is not None:
            self.temperature_mins.append(metrics.temperature_celsius_min)
        if metrics.temperature_celsius_max is not None:
            self.temperature_maxes.append(metrics.temperature_celsius_max)
        if metrics.temperature_celsius_avg is not None and metrics.sample_count > 0:
            self.temperature_weighted_sum += metrics.temperature_celsius_avg * metrics.sample_count
            self.temperature_sample_count += metrics.sample_count
        if metrics.bucket_start >= self.latest_seen_at:
            self.latest_seen_at = metrics.bucket_start
            self.serial_number = metrics.serial_number


def downsample_to_hourly(session: Session, *, now_utc: datetime) -> int:
    now = _require_aware_utc(now_utc)
    window_end = _hour_bucket(now - timedelta(days=30))
    window_start = window_end - timedelta(days=1)
    buckets: dict[tuple[datetime, int, int], _RawMetricsAccumulator] = {}

    rows = session.execute(
        select(PhysicalDriveSnapshot, ControllerSnapshot.captured_at)
        .join(ControllerSnapshot, PhysicalDriveSnapshot.snapshot_id == ControllerSnapshot.id)
        .where(ControllerSnapshot.captured_at < window_end)
        .where(ControllerSnapshot.captured_at >= window_start)
    )
    for drive, captured_at in rows:
        bucket_start = _hour_bucket(captured_at)
        key = (bucket_start, drive.enclosure_id, drive.slot_id)
        accumulator = buckets.setdefault(
            key,
            _RawMetricsAccumulator(
                bucket_start=bucket_start,
                enclosure_id=drive.enclosure_id,
                slot_id=drive.slot_id,
                serial_number=drive.serial_number,
            ),
        )
        accumulator.add(drive, captured_at)

    for accumulator in buckets.values():
        _upsert_hourly(session, accumulator)

    session.flush()
    return len(buckets)


def downsample_to_daily(session: Session, *, now_utc: datetime) -> int:
    now = _require_aware_utc(now_utc)
    window_end = _day_bucket(now - timedelta(days=365))
    window_start = window_end - timedelta(days=1)
    buckets: dict[tuple[datetime, int, int], _HourlyMetricsAccumulator] = {}

    rows = session.scalars(
        select(PhysicalDriveMetricsHourly)
        .where(PhysicalDriveMetricsHourly.bucket_start < window_end)
        .where(PhysicalDriveMetricsHourly.bucket_start >= window_start)
    )
    for metrics in rows:
        bucket_start = _day_bucket(metrics.bucket_start)
        key = (bucket_start, metrics.enclosure_id, metrics.slot_id)
        accumulator = buckets.setdefault(
            key,
            _HourlyMetricsAccumulator(
                bucket_start=bucket_start,
                enclosure_id=metrics.enclosure_id,
                slot_id=metrics.slot_id,
                serial_number=metrics.serial_number,
            ),
        )
        accumulator.add(metrics)

    for accumulator in buckets.values():
        _upsert_daily(session, accumulator)

    session.flush()
    return len(buckets)


def prune_raw_snapshots(
    session: Session,
    *,
    now_utc: datetime,
    retention_days: int = 30,
) -> int:
    now = _require_aware_utc(now_utc)
    cutoff = _hour_bucket(now - timedelta(days=retention_days))
    snapshot_ids = list(
        session.scalars(
            select(ControllerSnapshot.id).where(ControllerSnapshot.captured_at < cutoff)
        )
    )
    if not snapshot_ids:
        return 0
    session.execute(delete(ControllerSnapshot).where(ControllerSnapshot.id.in_(snapshot_ids)))
    session.flush()
    return len(snapshot_ids)


def prune_hourly_metrics(
    session: Session,
    *,
    now_utc: datetime,
    retention_days: int = 365,
) -> int:
    now = _require_aware_utc(now_utc)
    cutoff = _day_bucket(now - timedelta(days=retention_days))
    metric_ids = list(
        session.scalars(
            select(PhysicalDriveMetricsHourly.id).where(
                PhysicalDriveMetricsHourly.bucket_start < cutoff
            )
        )
    )
    if not metric_ids:
        return 0
    session.execute(
        delete(PhysicalDriveMetricsHourly).where(PhysicalDriveMetricsHourly.id.in_(metric_ids))
    )
    session.flush()
    return len(metric_ids)


def _upsert_hourly(session: Session, accumulator: _RawMetricsAccumulator) -> None:
    metrics = session.scalars(
        select(PhysicalDriveMetricsHourly)
        .where(PhysicalDriveMetricsHourly.bucket_start == accumulator.bucket_start)
        .where(PhysicalDriveMetricsHourly.enclosure_id == accumulator.enclosure_id)
        .where(PhysicalDriveMetricsHourly.slot_id == accumulator.slot_id)
    ).one_or_none()
    temperature_min, temperature_max, temperature_avg = _temperature_summary(
        accumulator.temperatures
    )

    if metrics is None:
        metrics = PhysicalDriveMetricsHourly(
            bucket_start=accumulator.bucket_start,
            enclosure_id=accumulator.enclosure_id,
            slot_id=accumulator.slot_id,
            serial_number=accumulator.serial_number,
            temperature_celsius_min=temperature_min,
            temperature_celsius_max=temperature_max,
            temperature_celsius_avg=temperature_avg,
            media_errors_max=accumulator.media_errors_max,
            other_errors_max=accumulator.other_errors_max,
            predictive_failures_max=accumulator.predictive_failures_max,
            sample_count=accumulator.sample_count,
        )
        session.add(metrics)
        return

    metrics.serial_number = accumulator.serial_number
    metrics.temperature_celsius_min = temperature_min
    metrics.temperature_celsius_max = temperature_max
    metrics.temperature_celsius_avg = temperature_avg
    metrics.media_errors_max = accumulator.media_errors_max
    metrics.other_errors_max = accumulator.other_errors_max
    metrics.predictive_failures_max = accumulator.predictive_failures_max
    metrics.sample_count = accumulator.sample_count


def _upsert_daily(session: Session, accumulator: _HourlyMetricsAccumulator) -> None:
    metrics = session.scalars(
        select(PhysicalDriveMetricsDaily)
        .where(PhysicalDriveMetricsDaily.bucket_start == accumulator.bucket_start)
        .where(PhysicalDriveMetricsDaily.enclosure_id == accumulator.enclosure_id)
        .where(PhysicalDriveMetricsDaily.slot_id == accumulator.slot_id)
    ).one_or_none()
    temperature_avg = (
        accumulator.temperature_weighted_sum / accumulator.temperature_sample_count
        if accumulator.temperature_sample_count
        else None
    )
    temperature_min = min(accumulator.temperature_mins) if accumulator.temperature_mins else None
    temperature_max = max(accumulator.temperature_maxes) if accumulator.temperature_maxes else None

    if metrics is None:
        metrics = PhysicalDriveMetricsDaily(
            bucket_start=accumulator.bucket_start,
            enclosure_id=accumulator.enclosure_id,
            slot_id=accumulator.slot_id,
            serial_number=accumulator.serial_number,
            temperature_celsius_min=temperature_min,
            temperature_celsius_max=temperature_max,
            temperature_celsius_avg=temperature_avg,
            media_errors_max=accumulator.media_errors_max,
            other_errors_max=accumulator.other_errors_max,
            predictive_failures_max=accumulator.predictive_failures_max,
            sample_count=accumulator.sample_count,
        )
        session.add(metrics)
        return

    metrics.serial_number = accumulator.serial_number
    metrics.temperature_celsius_min = temperature_min
    metrics.temperature_celsius_max = temperature_max
    metrics.temperature_celsius_avg = temperature_avg
    metrics.media_errors_max = accumulator.media_errors_max
    metrics.other_errors_max = accumulator.other_errors_max
    metrics.predictive_failures_max = accumulator.predictive_failures_max
    metrics.sample_count = accumulator.sample_count


def _temperature_summary(values: list[int]) -> tuple[int | None, int | None, float | None]:
    if not values:
        return None, None, None
    return min(values), max(values), sum(values) / len(values)


def _hour_bucket(value: datetime) -> datetime:
    return _require_aware_utc(value).replace(minute=0, second=0, microsecond=0)


def _day_bucket(value: datetime) -> datetime:
    return _require_aware_utc(value).replace(hour=0, minute=0, second=0, microsecond=0)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "naive datetimes are not allowed; use timezone-aware UTC datetimes"
        raise ValueError(msg)
    return value.astimezone(UTC)
