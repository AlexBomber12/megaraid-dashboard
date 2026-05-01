from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts import AlertMessage
from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.notifier import (
    NotifierCycleResult,
    _build_alert_message,
    run_notifier_cycle,
)


@dataclass
class FakeAlertTransport:
    sent: list[tuple[AlertMessage, str]] = field(default_factory=list)
    fail_indexes: set[int] = field(default_factory=set)
    fail_exception: type[Exception] = smtplib.SMTPException
    _calls: int = 0

    def send(self, message: AlertMessage, *, to: str) -> None:
        index = self._calls
        self._calls += 1
        if index in self.fail_indexes:
            raise self.fail_exception("simulated failure")
        self.sent.append((message, to))


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
    notified_at: datetime | None = None,
) -> Event:
    event = Event(
        occurred_at=occurred_at,
        severity=severity,
        category=category,
        subject=subject,
        summary=summary,
        notified_at=notified_at,
    )
    session.add(event)
    session.flush()
    return event


def test_run_notifier_cycle_happy_path(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    event = _add_event(session, occurred_at=now - timedelta(minutes=5))
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result == NotifierCycleResult(
        attempted=1, sent=1, deduplicated=0, failed=0, throttle_warning=False
    )
    assert len(transport.sent) == 1
    message, to = transport.sent[0]
    assert to == "ops@example.test"
    assert message.subject.startswith("[MegaRAID critical]")
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at == now


def test_run_notifier_cycle_severity_filter(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    event = _add_event(
        session,
        occurred_at=now - timedelta(minutes=5),
        severity="warning",
    )
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result.attempted == 0
    assert result.sent == 0
    assert transport.sent == []
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at is None


def test_run_notifier_cycle_threshold_warning_includes_critical(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    settings = _settings(alert_severity_threshold="warning")
    info_event = _add_event(
        session,
        occurred_at=now - timedelta(minutes=7),
        severity="info",
        subject="info-below-threshold",
    )
    warning_event = _add_event(
        session,
        occurred_at=now - timedelta(minutes=5),
        severity="warning",
        subject="warning-at-threshold",
    )
    critical_event = _add_event(
        session,
        occurred_at=now - timedelta(minutes=3),
        severity="critical",
        subject="critical-above-threshold",
    )
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=settings, now=now)

    assert result.attempted == 2
    assert result.sent == 2
    assert {message.subject for message, _ in transport.sent} == {
        "[MegaRAID warning] pd_state: warning-at-threshold",
        "[MegaRAID critical] pd_state: critical-above-threshold",
    }
    assert session.get(Event, info_event.id).notified_at is None  # type: ignore[union-attr]
    assert session.get(Event, warning_event.id).notified_at == now  # type: ignore[union-attr]
    assert session.get(Event, critical_event.id).notified_at == now  # type: ignore[union-attr]


def test_run_notifier_cycle_skips_events_below_since_cutoff(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    settings = _settings(alert_suppress_window_minutes=60)
    event = _add_event(session, occurred_at=now - timedelta(hours=2))
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=settings, now=now)

    assert result.attempted == 0
    assert result.sent == 0
    assert transport.sent == []
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at is None


def test_run_notifier_cycle_dedup_skips_send_but_marks_notified(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    settings = _settings(alert_suppress_window_minutes=60)
    _add_event(
        session,
        occurred_at=now - timedelta(minutes=30),
        notified_at=now - timedelta(minutes=20),
    )
    new_event = _add_event(session, occurred_at=now - timedelta(minutes=5))
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=settings, now=now)

    assert result.attempted == 1
    assert result.sent == 0
    assert result.deduplicated == 1
    assert transport.sent == []
    refreshed = session.get(Event, new_event.id)
    assert refreshed is not None
    assert refreshed.notified_at == now


def test_run_notifier_cycle_dedup_boundary_outside_window_sends(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    settings = _settings(alert_suppress_window_minutes=60)
    _add_event(
        session,
        occurred_at=now - timedelta(hours=3),
        notified_at=now - timedelta(hours=2),
    )
    new_event = _add_event(session, occurred_at=now - timedelta(minutes=5))
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=settings, now=now)

    assert result.attempted == 1
    assert result.sent == 1
    assert result.deduplicated == 0
    assert len(transport.sent) == 1
    refreshed = session.get(Event, new_event.id)
    assert refreshed is not None
    assert refreshed.notified_at == now


def test_run_notifier_cycle_throttle_warning_continues_sending(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    settings = _settings(alert_throttle_per_hour=20, alert_suppress_window_minutes=120)
    for index in range(25):
        _add_event(
            session,
            occurred_at=now - timedelta(minutes=90 + index),
            severity="critical",
            category="pd_state",
            subject=f"throttle-prior-{index}",
            notified_at=now - timedelta(minutes=30 + index),
        )
    new_event = _add_event(
        session,
        occurred_at=now - timedelta(minutes=2),
        severity="critical",
        category="pd_state",
        subject="new-distinct-subject",
    )
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=settings, now=now)

    assert result.throttle_warning is True
    assert result.sent == 1
    assert result.attempted == 1
    refreshed = session.get(Event, new_event.id)
    assert refreshed is not None
    assert refreshed.notified_at == now


def test_run_notifier_cycle_send_failure_isolates_other_events(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    events = [
        _add_event(
            session,
            occurred_at=now - timedelta(minutes=10 - index),
            subject=f"failure-isolation-{index}",
        )
        for index in range(3)
    ]
    session.commit()
    transport = FakeAlertTransport(fail_indexes={1})

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result.attempted == 3
    assert result.sent == 2
    assert result.failed == 1
    assert len(transport.sent) == 2
    statuses = [session.get(Event, event.id).notified_at for event in events]  # type: ignore[union-attr]
    assert statuses[0] == now
    assert statuses[1] is None
    assert statuses[2] == now


def test_run_notifier_cycle_oserror_isolates_other_events(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    events = [
        _add_event(
            session,
            occurred_at=now - timedelta(minutes=10 - index),
            subject=f"oserror-isolation-{index}",
        )
        for index in range(3)
    ]
    session.commit()
    transport = FakeAlertTransport(fail_indexes={1}, fail_exception=OSError)

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result.attempted == 3
    assert result.sent == 2
    assert result.failed == 1
    assert len(transport.sent) == 2
    statuses = [session.get(Event, event.id).notified_at for event in events]  # type: ignore[union-attr]
    assert statuses[0] == now
    assert statuses[1] is None
    assert statuses[2] == now


def test_run_notifier_cycle_rejects_naive_now(session: Session) -> None:
    transport = FakeAlertTransport()
    with pytest.raises(ValueError):
        run_notifier_cycle(
            session,
            transport,
            settings=_settings(),
            now=datetime(2026, 4, 25, 12, 0),
        )


def test_run_notifier_cycle_empty_db_returns_zero_result(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result == NotifierCycleResult(
        attempted=0, sent=0, deduplicated=0, failed=0, throttle_warning=False
    )
    assert transport.sent == []


def test_build_alert_message_caps_subject_at_200_chars() -> None:
    event = Event(
        occurred_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        severity="critical",
        category="pd_state",
        subject="x" * 500,
        summary="long subject summary",
    )
    message = _build_alert_message(event)
    assert len(message.subject) == 200


def test_build_alert_message_body_contains_required_fields() -> None:
    event = Event(
        occurred_at=datetime(2026, 4, 29, 22, 14, 3, tzinfo=UTC),
        severity="critical",
        category="pd_state",
        subject="Drive 252:s3 transitioned to FAILED",
        summary="Drive entered FAILED state from ONLINE.",
    )
    message = _build_alert_message(event)

    body = message.body_text
    assert "Severity:" in body
    assert "Category:" in body
    assert "Subject:" in body
    assert "Summary:" in body
    assert "LSI MegaRAID SAS9270CV-8i" in body
    assert "Do not reply" in body
    assert "2026-04-29 22:14:03 UTC" in body
    assert "CET" in body or "CEST" in body
