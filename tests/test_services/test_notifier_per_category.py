from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from megaraid_dashboard.alerts import AlertMessage
from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.notifier import _suppress_window_minutes, run_notifier_cycle


@dataclass
class FakeAlertTransport:
    sent: list[tuple[AlertMessage, str]] = field(default_factory=list)

    def send(self, message: AlertMessage, *, to: str) -> None:
        self.sent.append((message, to))


def test_controller_temperature_uses_24_hour_suppress_window() -> None:
    event = _event(category="controller_temperature")

    assert _suppress_window_minutes(event, _settings(alert_suppress_window_minutes=15)) == 1440


def test_other_categories_use_global_suppress_window() -> None:
    event = _event(category="pd_state")

    assert _suppress_window_minutes(event, _settings(alert_suppress_window_minutes=60)) == 60


def test_controller_temperature_dedup_suppresses_within_24_hours(session: Session) -> None:
    first_sent_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    now = first_sent_at + timedelta(hours=23, minutes=59)
    _add_event(session, occurred_at=first_sent_at, notified_at=first_sent_at)
    new_event = _add_event(session, occurred_at=now)
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result.attempted == 1
    assert result.sent == 0
    assert result.deduplicated == 1
    assert transport.sent == []
    assert session.get(Event, new_event.id).notified_at == now  # type: ignore[union-attr]


def test_controller_temperature_dedup_expires_after_24_hours(session: Session) -> None:
    first_sent_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    now = first_sent_at + timedelta(hours=24, minutes=1)
    _add_event(session, occurred_at=first_sent_at, notified_at=first_sent_at)
    new_event = _add_event(session, occurred_at=now)
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result.attempted == 1
    assert result.sent == 1
    assert result.deduplicated == 0
    assert len(transport.sent) == 1
    assert session.get(Event, new_event.id).notified_at == now  # type: ignore[union-attr]


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


def _event(*, category: str) -> Event:
    return Event(
        occurred_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        severity="critical",
        category=category,
        subject="Controller",
        summary="test event",
    )


def _add_event(
    session: Session,
    *,
    occurred_at: datetime,
    notified_at: datetime | None = None,
) -> Event:
    event = _event(category="controller_temperature")
    event.occurred_at = occurred_at
    event.notified_at = notified_at
    session.add(event)
    session.flush()
    return event
