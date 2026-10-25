"""Alert-only Telegram delivery.

Telegram is an additional, independent channel next to SMTP. Delivery state lives in
``event_deliveries`` (channel ``telegram``); ``events.notified_at`` stays SMTP-only, so a
failure in one channel never marks, blocks or duplicates the other.

Only actionable risks are sent: critical array, drive, SMART, CacheVault, temperature and
prolonged-monitoring-outage events immediately, and sustained temperature warnings after a
confirmation delay. Info events (recoveries, routine changes) and every other warning are
never sent. One incident produces one message: repeats of the same incident within 24 hours
are skipped, while a worse state (e.g. ``Dgrd`` -> ``OfLn``) or a different failure of the
same object (CacheVault replacement required, then missing; a replacement disk in the
same slot) is a new incident.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Protocol

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts.telegram import (
    TelegramConfigError,
    TelegramSendError,
    TelegramTransport,
    load_telegram_config,
)
from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import TELEGRAM_CHANNEL, get_maintenance_state, get_state, set_state
from megaraid_dashboard.db.models import Event, EventDelivery
from megaraid_dashboard.services.notifier import _record_telegram_health, _to_aware_utc
from megaraid_dashboard.services.telegram_messages import (
    build_telegram_text,
    incident_key,
    is_monitoring_outage,
)
from megaraid_dashboard.services.telegram_relevance import (
    delivery_relevance,
    drive_serial_at,
    incident_superseded,
)
from megaraid_dashboard.web.metrics import TELEGRAM_ALERTS_TOTAL

_LOG = structlog.get_logger(__name__)

STATUS_SENDING = "sending"
STATUS_RETRY = "retry"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

_REPEAT_SUPPRESS_WINDOW = timedelta(hours=24)
_PROLONGED_FAILURE_ATTEMPTS = 8
_MAX_CANDIDATES_PER_CYCLE = 100
_COOLDOWN_KEY = "telegram_not_before"
_RETRY_BASE_SECONDS = 60
_RETRY_MAX_SECONDS = 3600
_MAX_SENDS_PER_CYCLE = 5

_IMMEDIATE_CRITICAL_CATEGORIES = frozenset(
    {
        "vd_state",
        "pd_state",
        "media_errors",
        "predictive_failures",
        "smart_alert",
        "cv_state",
        "temperature",
        "controller_temperature",
        # Single collection failures are warnings; only the "Monitoring stale" outage is critical.
        "system",
    }
)
_CONFIRMED_WARNING_CATEGORIES = frozenset({"temperature", "controller_temperature"})


class TelegramSender(Protocol):
    def send(self, text: str) -> None: ...


@dataclass(frozen=True)
class TelegramCycleResult:
    sent: int
    failed: int
    skipped: int
    deferred: int
    config_error: bool = False


def build_telegram_transport(settings: Settings) -> TelegramSender:
    return TelegramTransport(load_telegram_config(settings))


def run_telegram_cycle(
    session: Session,
    *,
    settings: Settings,
    now: datetime,
    transport_factory: Callable[[Settings], TelegramSender] = build_telegram_transport,
) -> TelegramCycleResult:
    if now.tzinfo is None or now.utcoffset() is None:
        msg = "now must be a timezone-aware UTC datetime"
        raise ValueError(msg)
    started = monotonic()
    now_utc = _to_aware_utc(now)
    try:
        transport = transport_factory(settings)
    except TelegramConfigError as exc:
        # Fail closed: nothing is sent and events stay pending until the config is fixed.
        _record_telegram_health(False)
        _LOG.error("telegram_config_invalid", error=str(exc))
        return TelegramCycleResult(sent=0, failed=0, skipped=0, deferred=0, config_error=True)

    maintenance_active = get_maintenance_state(session, now=now_utc).active
    confirm_delay = timedelta(minutes=settings.telegram_warning_confirm_minutes)
    hostname = socket.gethostname()
    sent = failed = skipped = deferred = 0
    sends_left = _MAX_SENDS_PER_CYCLE
    cooldown_raw = get_state(session, _COOLDOWN_KEY)
    cooldown = _to_aware_utc(datetime.fromisoformat(cooldown_raw)) if cooldown_raw else now_utc
    channel_blocked = cooldown > now_utc

    for event, delivery in _load_candidates(session, now=now_utc):
        if not is_telegram_alert(event):
            continue
        if delivery is not None and delivery.status in (STATUS_SENDING, STATUS_FAILED):
            # A claim can survive a crash before HTTP, or after Telegram accepted the
            # request but before the sent commit. Prefer eventual delivery of an active
            # critical incident over silently losing it; the latter case may duplicate.
            _set_status(
                delivery, STATUS_RETRY, detail="outcome unknown: interrupted or failed send"
            )
            session.commit()
            _LOG.warning("telegram_delivery_requeued", event_id=event.id)
        if maintenance_active:
            _skip(session, event, delivery, detail="maintenance")
            skipped += 1
            continue
        if event.severity == "warning":
            if _to_aware_utc(event.occurred_at) > now_utc - confirm_delay:
                _defer(
                    session,
                    event,
                    delivery,
                    now=now_utc,
                    detail="awaiting_confirmation",
                    next_attempt_at=_to_aware_utc(event.occurred_at) + confirm_delay,
                )
                deferred += 1
                continue
            if _has_newer_event(session, event):
                _skip(session, event, delivery, detail="not_confirmed")
                skipped += 1
                continue
        relevance = delivery_relevance(session, event, settings=settings, now=now_utc)
        if relevance == "obsolete":
            _skip(session, event, delivery, detail="obsolete")
            skipped += 1
            continue
        if relevance != "active":
            _defer(session, event, delivery, now=now_utc, detail=relevance)
            deferred += 1
            continue
        if _same_incident_sent_since(session, event, since=now_utc - _REPEAT_SUPPRESS_WINDOW):
            _skip(session, event, delivery, detail="duplicate")
            skipped += 1
            _LOG.info("telegram_event_deduplicated", event_id=event.id, category=event.category)
            continue
        if channel_blocked or sends_left <= 0:
            _defer(session, event, delivery, now=now_utc, detail="channel_deferred")
            deferred += 1
            continue

        text = build_telegram_text(event, hostname=hostname)
        delivery = _claim(session, event, delivery, now=now_utc)
        session.commit()
        sends_left -= 1
        try:
            transport.send(text)
        except TelegramSendError as exc:
            # One failure stops this cycle's sends so an outage costs at most one timeout.
            channel_blocked = True
            failed_at = now_utc + timedelta(seconds=monotonic() - started)
            _record_send_failure(delivery, exc, now=failed_at)
            if exc.retry_after_seconds is not None:
                # Persist the channel embargo in the same transaction as the retry.
                cooldown = failed_at + timedelta(seconds=exc.retry_after_seconds)
                set_state(session, _COOLDOWN_KEY, cooldown.isoformat())
            session.commit()
            failed += 1
            TELEGRAM_ALERTS_TOTAL.labels(result="failed").inc()
            _LOG.error(
                "telegram_event_failed",
                event_id=event.id,
                attempts=delivery.attempts,
                status=delivery.status,
                error=exc.reason,
            )
            continue
        _set_status(delivery, STATUS_SENT, detail=None)
        delivery.delivered_at = now_utc
        session.commit()
        sent += 1
        TELEGRAM_ALERTS_TOTAL.labels(result="sent").inc()
        _LOG.info(
            "telegram_event_sent",
            event_id=event.id,
            severity=event.severity,
            category=event.category,
            subject=event.subject,
        )

    pending_failure = session.scalar(
        select(EventDelivery.id)
        .where(
            EventDelivery.channel == TELEGRAM_CHANNEL,
            EventDelivery.status.in_((STATUS_RETRY, STATUS_SENDING, STATUS_FAILED)),
            EventDelivery.attempts > 0,
        )
        .limit(1)
    )
    _record_telegram_health(failed == 0 and pending_failure is None and cooldown <= now_utc)
    return TelegramCycleResult(sent=sent, failed=failed, skipped=skipped, deferred=deferred)


def is_telegram_alert(event: Event) -> bool:
    if event.severity == "critical":
        if event.category == "system":
            return is_monitoring_outage(event)
        return event.category in _IMMEDIATE_CRITICAL_CATEGORIES
    if event.severity == "warning":
        return event.category in _CONFIRMED_WARNING_CATEGORIES
    return False


def _load_candidates(
    session: Session,
    *,
    now: datetime,
) -> list[tuple[Event, EventDelivery | None]]:
    statement = (
        select(Event, EventDelivery)
        .outerjoin(
            EventDelivery,
            and_(
                EventDelivery.event_id == Event.id,
                EventDelivery.channel == TELEGRAM_CHANNEL,
            ),
        )
        .where(
            Event.occurred_at <= now,
            or_(
                Event.category != "system",
                and_(
                    Event.subject == "Controller",
                    Event.after_json["monitoring_outage"].as_boolean().is_(True),
                ),
            ),
            Event.severity.in_(("warning", "critical")),
            Event.category.in_(_IMMEDIATE_CRITICAL_CATEGORIES | _CONFIRMED_WARNING_CATEGORIES),
            or_(
                EventDelivery.id.is_(None),
                EventDelivery.status.in_((STATUS_SENDING, STATUS_RETRY, STATUS_FAILED)),
            ),
            or_(
                EventDelivery.next_attempt_at.is_(None),
                EventDelivery.next_attempt_at <= now,
            ),
        )
        .order_by(EventDelivery.next_attempt_at.asc(), Event.occurred_at.asc(), Event.id.asc())
        .limit(_MAX_CANDIDATES_PER_CYCLE)
    )
    return [(row[0], row[1]) for row in session.execute(statement).all()]


def _has_newer_event(session: Session, event: Event) -> bool:
    """A newer event for the same object means the warning cleared or escalated."""
    statement = (
        select(Event.id)
        .where(
            Event.category == event.category,
            Event.subject == event.subject,
            Event.id > event.id,
        )
        .limit(1)
    )
    return session.execute(statement).first() is not None


def _same_incident_sent_since(session: Session, event: Event, *, since: datetime) -> bool:
    statement = (
        select(Event)
        .join(EventDelivery, EventDelivery.event_id == Event.id)
        .where(
            EventDelivery.channel == TELEGRAM_CHANNEL,
            EventDelivery.status == STATUS_SENT,
            EventDelivery.delivered_at >= since,
            Event.severity == event.severity,
            Event.category == event.category,
            Event.subject == event.subject,
            Event.id != event.id,
        )
    )
    key = incident_key(event)
    drive_serial = drive_serial_at(session, event)
    return any(
        incident_key(other) == key
        and drive_serial_at(session, other) == drive_serial
        and not incident_superseded(session, other, before_id=event.id)
        for other in session.scalars(statement)
    )


def _claim(
    session: Session,
    event: Event,
    delivery: EventDelivery | None,
    *,
    now: datetime,
) -> EventDelivery:
    if delivery is None:
        delivery = EventDelivery(event_id=event.id, channel=TELEGRAM_CHANNEL, attempts=0)
        session.add(delivery)
    _set_status(delivery, STATUS_SENDING, detail=None)
    delivery.attempts += 1
    delivery.last_attempt_at = now
    return delivery


def _record_send_failure(delivery: EventDelivery, exc: TelegramSendError, *, now: datetime) -> None:
    if delivery.attempts >= _PROLONGED_FAILURE_ATTEMPTS:
        _LOG.warning(
            "telegram_delivery_prolonged_failure",
            event_id=delivery.event_id,
            attempts=delivery.attempts,
        )
    # Clamp the exponent as well as the delay: critical retries have no lifetime limit.
    backoff = min(_RETRY_BASE_SECONDS * 2 ** min(delivery.attempts - 1, 6), _RETRY_MAX_SECONDS)
    delay = max(backoff, exc.retry_after_seconds or 0)
    _set_status(delivery, STATUS_RETRY, detail=exc.reason)
    delivery.next_attempt_at = now + timedelta(seconds=delay)


def _defer(
    session: Session,
    event: Event,
    delivery: EventDelivery | None,
    *,
    now: datetime,
    detail: str,
    next_attempt_at: datetime | None = None,
) -> None:
    if delivery is None:
        delivery = EventDelivery(event_id=event.id, channel=TELEGRAM_CHANNEL, attempts=0)
        session.add(delivery)
    _set_status(delivery, STATUS_RETRY, detail=detail)
    delivery.next_attempt_at = next_attempt_at or now + timedelta(seconds=60)
    session.commit()


def _skip(session: Session, event: Event, delivery: EventDelivery | None, *, detail: str) -> None:
    if delivery is None:
        delivery = EventDelivery(event_id=event.id, channel=TELEGRAM_CHANNEL, attempts=0)
        session.add(delivery)
    _set_status(delivery, STATUS_SKIPPED, detail=detail)
    session.commit()


def _set_status(delivery: EventDelivery, status: str, *, detail: str | None) -> None:
    delivery.status = status
    delivery.detail = detail
    delivery.next_attempt_at = None
