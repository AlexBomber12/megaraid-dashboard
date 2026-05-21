from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import ControllerSnapshot
from megaraid_dashboard.services import controller_history
from megaraid_dashboard.services.controller_history import (
    _require_aware_utc,
    load_roc_temperature_series,
)


@pytest.fixture(autouse=True)
def _freeze_history_request_time(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        controller_history,
        "_now_utc",
        lambda: datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )


def test_24h_range_with_five_minute_snapshots_yields_288_points(session: Session) -> None:
    latest = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    _seed_regular_snapshots(session, latest=latest, count=288, step=timedelta(minutes=5))

    series = load_roc_temperature_series(session, range_hours=24, settings=_settings())

    assert len(series.points) == 288
    assert series.points[0].captured_at == latest - timedelta(minutes=5 * 287)
    assert series.points[-1].captured_at == latest


def test_168h_range_yields_hourly_buckets(session: Session) -> None:
    latest = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    _seed_regular_snapshots(session, latest=latest, count=168, step=timedelta(hours=1))

    series = load_roc_temperature_series(session, range_hours=168, settings=_settings())

    assert len(series.points) == 168
    assert series.points[0].captured_at == latest - timedelta(hours=167)
    assert series.points[-1].captured_at == latest


def test_720h_range_yields_daily_buckets(session: Session) -> None:
    latest = datetime(2026, 5, 20, 0, 0, tzinfo=UTC)
    _seed_regular_snapshots(session, latest=latest, count=30, step=timedelta(days=1))

    series = load_roc_temperature_series(session, range_hours=720, settings=_settings())

    assert len(series.points) == 30
    assert series.points[0].captured_at == latest - timedelta(days=29)
    assert series.points[-1].captured_at == latest


def test_empty_db_yields_empty_series(session: Session) -> None:
    series = load_roc_temperature_series(session, range_hours=24, settings=_settings())

    assert series.points == []
    assert series.min_celsius is None
    assert series.avg_celsius is None
    assert series.max_celsius is None
    assert series.current_celsius is None


def test_thresholds_are_populated_from_settings(session: Session) -> None:
    _insert_snapshot(session, datetime(2026, 5, 20, 12, 0, tzinfo=UTC), temperature=72)

    series = load_roc_temperature_series(session, range_hours=24, settings=_settings(91, 101))

    assert series.warning_threshold == 91
    assert series.critical_threshold == 101


def test_range_label_is_formatted(session: Session) -> None:
    settings = _settings()

    assert load_roc_temperature_series(session, range_hours=24, settings=settings).range_label == (
        "last 24 hours"
    )
    assert load_roc_temperature_series(session, range_hours=168, settings=settings).range_label == (
        "last 7 days"
    )
    assert load_roc_temperature_series(session, range_hours=720, settings=settings).range_label == (
        "last 30 days"
    )


def test_hourly_aggregation_averages_samples_in_bucket(
    session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    start = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        controller_history,
        "_now_utc",
        lambda: start + timedelta(hours=1),
    )
    for offset in range(12):
        _insert_snapshot(
            session,
            start + timedelta(minutes=offset * 5),
            temperature=60 + offset,
        )
    session.commit()

    series = load_roc_temperature_series(session, range_hours=168, settings=_settings())

    assert len(series.points) == 1
    assert series.points[0].captured_at == start
    assert series.points[0].temperature_celsius == 66


def test_current_celsius_is_latest_sample_value(session: Session) -> None:
    _insert_snapshot(session, datetime(2026, 5, 20, 11, 55, tzinfo=UTC), temperature=71)
    _insert_snapshot(session, datetime(2026, 5, 20, 12, 0, tzinfo=UTC), temperature=73)
    session.commit()

    series = load_roc_temperature_series(session, range_hours=1, settings=_settings())

    assert series.current_celsius == 73


def test_recent_window_is_anchored_to_current_time(
    session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(controller_history, "_now_utc", lambda: now)
    _insert_snapshot(session, now - timedelta(hours=48), temperature=74)
    session.commit()

    series = load_roc_temperature_series(session, range_hours=24, settings=_settings())

    assert series.points == []
    assert series.current_celsius == 74


def test_bucket_expression_uses_dialect_specific_sql() -> None:
    sqlite_sql = str(
        select(
            controller_history._bucket_expression(
                dialect_name="sqlite",
                granularity="hour",
                sqlite_format="%Y-%m-%d %H",
            )
        ).compile(dialect=sqlite.dialect())
    )
    postgresql_sql = str(
        select(
            controller_history._bucket_expression(
                dialect_name="postgresql",
                granularity="hour",
                sqlite_format="%Y-%m-%d %H",
            )
        ).compile(dialect=postgresql.dialect())
    )

    assert "strftime" in sqlite_sql
    assert "date_trunc" in postgresql_sql
    assert "timezone" in postgresql_sql
    assert "strftime" not in postgresql_sql


def test_bucket_expression_rejects_unsupported_dialect() -> None:
    with pytest.raises(RuntimeError, match="unsupported database dialect"):
        controller_history._bucket_expression(
            dialect_name="mysql",
            granularity="hour",
            sqlite_format="%Y-%m-%d %H",
        )


def test_bucket_captured_at_accepts_postgresql_datetime_bucket() -> None:
    bucket = datetime(2026, 5, 20, 12, 0)

    captured_at = controller_history._bucket_captured_at(
        bucket,
        sqlite_parse_format="%Y-%m-%d %H",
    )

    assert captured_at == datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def test_bucket_captured_at_rejects_unexpected_bucket_type() -> None:
    with pytest.raises(TypeError, match="unsupported RoC temperature history bucket value"):
        controller_history._bucket_captured_at(1, sqlite_parse_format="%Y-%m-%d %H")


def test_now_utc_returns_aware_utc_datetime(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.undo()

    value = controller_history._now_utc()

    assert value.tzinfo is UTC
    assert value.utcoffset() == timedelta(0)


def test_require_aware_utc_treats_naive_datetime_as_utc() -> None:
    assert _require_aware_utc(datetime(2026, 5, 20, 12, 0)) == datetime(
        2026,
        5,
        20,
        12,
        0,
        tzinfo=UTC,
    )


def _seed_regular_snapshots(
    session: Session,
    *,
    latest: datetime,
    count: int,
    step: timedelta,
) -> None:
    first = latest - (step * (count - 1))
    for index in range(count):
        _insert_snapshot(session, first + (step * index), temperature=70 + (index % 5))
    session.commit()


def _insert_snapshot(session: Session, captured_at: datetime, *, temperature: int) -> None:
    session.add(
        ControllerSnapshot(
            captured_at=captured_at,
            model_name="MegaRAID SAS 9270CV-8i",
            serial_number="SV00000001",
            firmware_version="23.34.0-0019",
            bios_version="6.36.00.3_4.19.08.00_0x06180200",
            driver_version="07.727.03.00",
            alarm_state="Off",
            cv_present=True,
            bbu_present=False,
            roc_temperature_celsius=temperature,
            raw_json=None,
        )
    )


def _settings(warning: int = 95, critical: int = 105) -> Settings:
    return Settings.model_construct(
        roc_temp_warning_celsius=warning,
        roc_temp_critical_celsius=critical,
    )
