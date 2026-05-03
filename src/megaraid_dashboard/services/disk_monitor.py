from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import Event

LOGGER = structlog.get_logger(__name__)

DISK_SPACE_CATEGORY = "disk_space"
DISK_SPACE_SUBJECT = "Data partition"
RECOVERY_HYSTERESIS_MB = 50
SUPPRESS_WINDOW = timedelta(hours=6)


def _resolve_data_partition(database_url: str) -> Path:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite":
        raise NotImplementedError("only SQLite database URLs are supported")
    database_path = parsed.database
    if database_path is None or database_path in {"", ":memory:"}:
        return Path.cwd()
    path = Path(database_path)
    if path.is_absolute():
        return path.parent
    return path.resolve().parent


def _free_space_mb(path: Path) -> int:
    return shutil.disk_usage(path).free // (1024 * 1024)


def check_data_partition_free_space(
    session: Session,
    *,
    settings: Settings,
    now: datetime,
) -> list[Event]:
    now_utc = _require_aware_utc(now)
    try:
        data_partition = _resolve_data_partition(settings.database_url)
    except NotImplementedError:
        LOGGER.warning(
            "disk_space_monitor_unsupported_database",
            database_backend=make_url(settings.database_url).get_backend_name(),
        )
        return []

    free_mb = _free_space_mb(data_partition)
    latest_event = _latest_disk_space_event(session)
    severity: str | None = None
    summary: str | None = None
    threshold_mb: int | None = None

    if free_mb < settings.disk_critical_free_mb:
        severity = "critical"
        threshold_mb = settings.disk_critical_free_mb
    elif free_mb < settings.disk_warning_free_mb:
        severity = "warning"
        threshold_mb = settings.disk_warning_free_mb

    if severity is not None and threshold_mb is not None:
        if _should_suppress(latest_event, severity=severity, now=now_utc):
            return []
        summary = (
            f"Free space on data partition: {free_mb} MB "
            f"(below {severity} threshold {threshold_mb} MB)"
        )
    elif (
        latest_event is not None
        and latest_event.severity in {"warning", "critical"}
        and free_mb > settings.disk_warning_free_mb + RECOVERY_HYSTERESIS_MB
    ):
        severity = "info"
        summary = f"Free space recovered: {free_mb} MB"

    if severity is None or summary is None:
        return []

    return [
        Event(
            occurred_at=now_utc,
            severity=severity,
            category=DISK_SPACE_CATEGORY,
            subject=DISK_SPACE_SUBJECT,
            summary=summary,
            before_json=None,
            after_json={
                "free_mb": free_mb,
                "warning_free_mb": settings.disk_warning_free_mb,
                "critical_free_mb": settings.disk_critical_free_mb,
            },
        )
    ]


def _latest_disk_space_event(session: Session) -> Event | None:
    return session.scalars(
        select(Event)
        .where(Event.category == DISK_SPACE_CATEGORY)
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(1)
    ).one_or_none()


def _should_suppress(latest_event: Event | None, *, severity: str, now: datetime) -> bool:
    if latest_event is None or latest_event.severity != severity:
        return False
    return latest_event.occurred_at >= now - SUPPRESS_WINDOW


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "naive datetimes are not allowed; use timezone-aware UTC datetimes"
        raise ValueError(msg)
    return value.astimezone(UTC)
