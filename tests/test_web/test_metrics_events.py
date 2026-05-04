from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts import AlertMessage
from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import record_event
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.notifier import run_notifier_cycle
from megaraid_dashboard.web.metrics import _reset_runtime_metrics_for_tests, create_metrics_app


@dataclass
class FakeAlertTransport:
    sent: list[tuple[AlertMessage, str]] = field(default_factory=list)

    def send(self, message: AlertMessage, *, to: str) -> None:
        self.sent.append((message, to))


@pytest.fixture(autouse=True)
def reset_runtime_metrics() -> None:
    _reset_runtime_metrics_for_tests()


def test_metrics_scrape_reports_event_counter(session: Session) -> None:
    for index in range(3):
        record_event(
            session,
            severity="critical",
            category="pd_state",
            subject=f"PD e252:s{index}",
            summary="Drive entered failed state.",
        )
    session.commit()

    response_text = _scrape_metrics()

    assert 'megaraid_events_total{category="pd_state",severity="critical"} 3.0' in response_text


def test_metrics_scrape_reports_alerts_sent_counter(session: Session) -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    for index in range(2):
        session.add(
            Event(
                occurred_at=now - timedelta(minutes=index + 1),
                severity="critical",
                category="pd_state",
                subject=f"PD e252:s{index}",
                summary="Drive entered failed state.",
            )
        )
    session.commit()
    transport = FakeAlertTransport()

    result = run_notifier_cycle(session, transport, settings=_settings(), now=now)

    assert result.sent == 2
    assert len(transport.sent) == 2
    assert "megaraid_alerts_sent_total 2.0" in _scrape_metrics()


def _scrape_metrics() -> str:
    metrics_app = create_metrics_app()
    with TestClient(metrics_app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    return response.text


def _settings() -> Settings:
    return Settings(
        alert_smtp_host="smtp.example.test",
        alert_smtp_port=587,
        alert_smtp_user="alert@example.test",
        alert_smtp_password="test-token",
        alert_from="alert@example.test",
        alert_to="ops@example.test",
        admin_username="admin",
        admin_password_hash="hash",
        storcli_path="/usr/local/sbin/storcli64",
        metrics_interval_seconds=300,
        database_url="sqlite:///./megaraid.db",
        log_level="INFO",
    )
