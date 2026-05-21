"""RoC temperature history aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import ControllerSnapshot

_BucketGranularity = Literal["hour", "day"]


@dataclass(frozen=True)
class RocTemperaturePoint:
    captured_at: datetime
    temperature_celsius: int


@dataclass(frozen=True)
class RocTemperatureSeries:
    points: list[RocTemperaturePoint]
    range_label: str
    min_celsius: int | None
    avg_celsius: int | None
    max_celsius: int | None
    current_celsius: int | None
    warning_threshold: int
    critical_threshold: int


def load_roc_temperature_series(
    session: Session,
    *,
    range_hours: int,
    settings: Settings,
) -> RocTemperatureSeries:
    latest_captured_at = _latest_captured_at(session)
    current_celsius = _current_celsius(session)
    if latest_captured_at is None:
        return _series(
            points=[],
            range_hours=range_hours,
            current_celsius=current_celsius,
            settings=settings,
        )

    until = _now_utc()
    cutoff = until - timedelta(hours=range_hours)
    if range_hours <= 24:
        points = _load_raw_points(session, cutoff=cutoff, until=until)
    elif range_hours <= 168:
        points = _load_bucketed_points(
            session,
            cutoff=cutoff,
            until=until,
            granularity="hour",
            sqlite_format="%Y-%m-%d %H",
            sqlite_parse_format="%Y-%m-%d %H",
        )
    else:
        points = _load_bucketed_points(
            session,
            cutoff=cutoff,
            until=until,
            granularity="day",
            sqlite_format="%Y-%m-%d",
            sqlite_parse_format="%Y-%m-%d",
        )

    return _series(
        points=points,
        range_hours=range_hours,
        current_celsius=current_celsius,
        settings=settings,
    )


def _latest_captured_at(session: Session) -> datetime | None:
    captured_at = session.scalar(
        select(ControllerSnapshot.captured_at)
        .order_by(ControllerSnapshot.captured_at.desc())
        .limit(1)
    )
    return None if captured_at is None else _require_aware_utc(captured_at)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _current_celsius(session: Session) -> int | None:
    return session.scalar(
        select(ControllerSnapshot.roc_temperature_celsius)
        .order_by(ControllerSnapshot.captured_at.desc())
        .limit(1)
    )


def _load_raw_points(
    session: Session,
    *,
    cutoff: datetime,
    until: datetime,
) -> list[RocTemperaturePoint]:
    rows = session.execute(
        select(ControllerSnapshot.captured_at, ControllerSnapshot.roc_temperature_celsius)
        .where(ControllerSnapshot.captured_at > cutoff)
        .where(ControllerSnapshot.captured_at <= until)
        .where(ControllerSnapshot.roc_temperature_celsius.is_not(None))
        .order_by(ControllerSnapshot.captured_at)
    )
    return [
        RocTemperaturePoint(
            captured_at=_require_aware_utc(captured_at),
            temperature_celsius=temperature_celsius,
        )
        for captured_at, temperature_celsius in rows
        if temperature_celsius is not None
    ]


def _load_bucketed_points(
    session: Session,
    *,
    cutoff: datetime,
    until: datetime,
    granularity: _BucketGranularity,
    sqlite_format: str,
    sqlite_parse_format: str,
) -> list[RocTemperaturePoint]:
    bucket = _bucket_expression(
        dialect_name=session.get_bind().dialect.name,
        granularity=granularity,
        sqlite_format=sqlite_format,
    )
    rows = session.execute(
        select(bucket.label("bucket"), func.avg(ControllerSnapshot.roc_temperature_celsius))
        .where(ControllerSnapshot.captured_at > cutoff)
        .where(ControllerSnapshot.captured_at <= until)
        .where(ControllerSnapshot.roc_temperature_celsius.is_not(None))
        .group_by(bucket)
        .order_by(bucket)
    )
    return [
        RocTemperaturePoint(
            captured_at=_bucket_captured_at(bucket_value, sqlite_parse_format=sqlite_parse_format),
            temperature_celsius=round(temperature_avg),
        )
        for bucket_value, temperature_avg in rows
        if bucket_value is not None and temperature_avg is not None
    ]


def _bucket_expression(
    *,
    dialect_name: str,
    granularity: _BucketGranularity,
    sqlite_format: str,
) -> ColumnElement[Any]:
    if dialect_name == "sqlite":
        return func.strftime(sqlite_format, ControllerSnapshot.captured_at)
    if dialect_name == "postgresql":
        return func.date_trunc(granularity, func.timezone("UTC", ControllerSnapshot.captured_at))
    msg = f"unsupported database dialect for RoC temperature history aggregation: {dialect_name}"
    raise RuntimeError(msg)


def _bucket_captured_at(bucket_value: Any, *, sqlite_parse_format: str) -> datetime:
    if isinstance(bucket_value, datetime):
        return _require_aware_utc(bucket_value)
    if isinstance(bucket_value, str):
        return datetime.strptime(bucket_value, sqlite_parse_format).replace(tzinfo=UTC)
    msg = f"unsupported RoC temperature history bucket value: {bucket_value!r}"
    raise TypeError(msg)


def _series(
    *,
    points: list[RocTemperaturePoint],
    range_hours: int,
    current_celsius: int | None,
    settings: Settings,
) -> RocTemperatureSeries:
    temperatures = [point.temperature_celsius for point in points]
    return RocTemperatureSeries(
        points=points,
        range_label=_range_label(range_hours),
        min_celsius=None if not temperatures else min(temperatures),
        avg_celsius=None if not temperatures else round(sum(temperatures) / len(temperatures)),
        max_celsius=None if not temperatures else max(temperatures),
        current_celsius=current_celsius,
        warning_threshold=settings.roc_temp_warning_celsius,
        critical_threshold=settings.roc_temp_critical_celsius,
    )


def _range_label(range_hours: int) -> str:
    if range_hours < 24:
        return f"last {range_hours} hour"
    if range_hours == 24:
        return "last 24 hours"
    days = range_hours // 24
    return f"last {days} days"


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
