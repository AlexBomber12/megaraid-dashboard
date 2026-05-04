from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from megaraid_dashboard.alerts import AlertMessage
from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import set_maintenance_state
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.notifier import NotifierCycleResult, run_notifier_cycle


@dataclass
class FakeAlertTransport:
    sent: list[tuple[AlertMessage, str]] = field(default_factory=list)
    fail_exception: type[Exception] = smtplib.SMTPException

    def send(self, message: AlertMessage, *, to: str) -> None:
        self.sent.append((message, to))


def test_run_notifier_cycle_skips_pending_event_during_maintenance(session: Session) -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    event = _add_event(session, occurred_at=now - timedelta(minutes=5))
    set_maintenance_state(
        session,
        active=True,
        expires_at=now + timedelta(minutes=30),
        started_by="admin",
    )
    session.commit()
    transport = FakeAlertTransport()

    with capture_logs() as logs:
        result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result == NotifierCycleResult(
        attempted=0,
        sent=0,
        deduplicated=0,
        failed=0,
        throttle_warning=False,
    )
    assert transport.sent == []
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at is None
    assert logs == [
        {
            "event": "notifier_skipped_for_maintenance",
            "log_level": "info",
            "active_until": "2026-05-04T12:30:00+00:00",
            "started_by": "admin",
        }
    ]


def test_run_notifier_cycle_sends_when_maintenance_inactive(session: Session) -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    event = _add_event(session, occurred_at=now - timedelta(minutes=5))
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result.sent == 1
    assert len(transport.sent) == 1
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at == now


def test_run_notifier_cycle_sends_after_maintenance_expires(session: Session) -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    event = _add_event(session, occurred_at=now - timedelta(minutes=5))
    set_maintenance_state(
        session,
        active=True,
        expires_at=now - timedelta(minutes=2),
        started_by="admin",
    )
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result.sent == 1
    assert len(transport.sent) == 1
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at == now


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "alert_smtp_host": "smtp.example.test",
        "alert_smtp_port": 587,
        "alert_smtp_user": "alert@example.test",
        "alert_smtp_password": "test-token",
        "alert_from": "alert@example.test",
        "alert_to": "ops@example.test",
        "admin_username": "admin",
        "admin_password_hash": "hash",
        "storcli_path": "/usr/local/sbin/storcli64",
        "metrics_interval_seconds": 300,
        "database_url": "sqlite:///./megaraid.db",
        "log_level": "INFO",
    }
    base.update(overrides)
    return Settings(**base)


def _add_event(
    session: Session,
    *,
    occurred_at: datetime,
    severity: str = "critical",
    category: str = "pd_state",
    subject: str = "Drive 252:s3 transitioned to FAILED",
    summary: str = "Drive entered FAILED state from ONLINE.",
) -> Event:
    event = Event(
        occurred_at=occurred_at,
        severity=severity,
        category=category,
        subject=subject,
        summary=summary,
    )
    session.add(event)
    session.flush()
    return event
