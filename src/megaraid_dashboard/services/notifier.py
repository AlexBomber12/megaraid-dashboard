from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts import AlertMessage, AlertTransport
from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import (
    count_events_notified_since,
    iter_pending_events,
    mark_event_notified,
)
from megaraid_dashboard.db.models import Event

_LOG = structlog.get_logger(__name__)

_LOCK_PATH_DEFAULT = "/tmp/megaraid-dashboard-notifier.lock"
_SUBJECT_MAX_LENGTH = 200
_CONTROLLER_LABEL = "LSI MegaRAID SAS9270CV-8i"
# RoC overheating is slow-moving; one reminder per day avoids noisy hourly repeats.
_PER_CATEGORY_SUPPRESS_MINUTES: dict[str, int] = {
    "controller_temperature": 1440,
}


@dataclass(frozen=True)
class NotifierCycleResult:
    attempted: int
    sent: int
    deduplicated: int
    failed: int
    throttle_warning: bool


def run_notifier_cycle(
    session: Session,
    transport: AlertTransport,
    *,
    settings: Settings,
    now: datetime,
) -> NotifierCycleResult:
    if now.tzinfo is None or now.utcoffset() is None:
        msg = "now must be a timezone-aware UTC datetime"
        raise ValueError(msg)
    now_utc = now.astimezone(UTC)
    earliest_since = now_utc - timedelta(minutes=_max_suppress_window_minutes(settings))

    pending = list(
        iter_pending_events(
            session,
            severity_threshold=settings.alert_severity_threshold,
            since=earliest_since,
        )
    )

    trailing_hour_start = now_utc - timedelta(hours=1)
    notified_count = count_events_notified_since(session, since=trailing_hour_start)
    throttle_warning = notified_count > settings.alert_throttle_per_hour
    if throttle_warning:
        _LOG.warning(
            "notifier_throttle_warning",
            notified_count=notified_count,
            limit=settings.alert_throttle_per_hour,
        )

    attempted = 0
    sent = 0
    deduplicated = 0
    failed = 0

    for event in pending:
        event_since = now_utc - timedelta(minutes=_suppress_window_minutes(event, settings))
        if _to_aware_utc(event.occurred_at) < event_since:
            continue
        attempted += 1
        if _event_was_notified_recently(session, event, since=event_since):
            mark_event_notified(session, event.id, now_utc)
            deduplicated += 1
            _LOG.info(
                "notifier_event_deduplicated",
                event_id=event.id,
                severity=event.severity,
                category=event.category,
                subject=event.subject,
            )
            continue
        message = _build_alert_message(event)
        try:
            transport.send(message, to=settings.alert_to)
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            failed += 1
            _LOG.error(
                "notifier_event_failed",
                event_id=event.id,
                error=str(exc),
                exc_info=True,
            )
            continue
        mark_event_notified(session, event.id, now_utc)
        sent += 1
        _LOG.info(
            "notifier_event_sent",
            event_id=event.id,
            severity=event.severity,
            category=event.category,
            subject=event.subject,
        )

    session.commit()
    return NotifierCycleResult(
        attempted=attempted,
        sent=sent,
        deduplicated=deduplicated,
        failed=failed,
        throttle_warning=throttle_warning,
    )


def _suppress_window_minutes(event: Event, settings: Settings) -> int:
    return _PER_CATEGORY_SUPPRESS_MINUTES.get(
        event.category,
        settings.alert_suppress_window_minutes,
    )


def _max_suppress_window_minutes(settings: Settings) -> int:
    configured_windows = [
        settings.alert_suppress_window_minutes,
        *_PER_CATEGORY_SUPPRESS_MINUTES.values(),
    ]
    return max(configured_windows)


def _event_was_notified_recently(
    session: Session,
    event: Event,
    *,
    since: datetime,
) -> bool:
    statement = (
        select(Event.id)
        .where(
            Event.severity == event.severity,
            Event.category == event.category,
            Event.subject == event.subject,
            Event.notified_at.is_not(None),
            Event.notified_at >= since,
            Event.id != event.id,
        )
        .limit(1)
    )
    return session.execute(statement).first() is not None


def _build_alert_message(event: Event) -> AlertMessage:
    subject = f"[MegaRAID {event.severity}] {event.category}: {event.subject}"[:_SUBJECT_MAX_LENGTH]
    occurred_utc = _to_aware_utc(event.occurred_at)
    occurred_utc_str = occurred_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    occurred_local_str = _format_europe_rome(occurred_utc)
    body_text = (
        f"A {event.severity} event was recorded on the MegaRAID controller.\n"
        "\n"
        f"Severity:    {event.severity}\n"
        f"Category:    {event.category}\n"
        f"Subject:     {event.subject}\n"
        f"Summary:     {event.summary}\n"
        f"Occurred at: {occurred_utc_str} ({occurred_local_str})\n"
        f"Controller:  {_CONTROLLER_LABEL}\n"
        "\n"
        "Do not reply. This mailbox is unmonitored.\n"
    )
    return AlertMessage(subject=subject, body_text=body_text)


def _to_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_europe_rome(value: datetime) -> str:
    try:
        localized = value.astimezone(ZoneInfo("Europe/Rome"))
    except ZoneInfoNotFoundError:
        localized = value
    return localized.strftime("%Y-%m-%d %H:%M:%S %Z")
