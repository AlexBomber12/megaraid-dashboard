from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import record_event


@dataclass(frozen=True)
class _InsertedEvent:
    id: int
    occurred_at: datetime


@pytest.fixture(autouse=True)
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    env = {
        "ALERT_SMTP_HOST": "smtp.example.test",
        "ALERT_SMTP_PORT": "587",
        "ALERT_SMTP_USER": "alert@example.test",
        "ALERT_SMTP_PASSWORD": "test-token",
        "ALERT_FROM": "alert@example.test",
        "ALERT_TO": "ops@example.test",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": "test-bcrypt-hash",
        "STORCLI_PATH": "/usr/local/sbin/storcli64",
        "METRICS_INTERVAL_SECONDS": "300",
        "COLLECTOR_ENABLED": "false",
        "COLLECTOR_LOCK_PATH": str(tmp_path / "collector.lock"),
        "DATABASE_URL": "sqlite:///:memory:",
        "LOG_LEVEL": "INFO",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_events_empty_database_renders_empty_state_without_load_more() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/events")

    assert response.status_code == 200
    assert "No events recorded yet." in response.text
    assert "Load more" not in response.text


@pytest.mark.parametrize(
    ("count", "expected_subject", "expect_load_more"),
    [(51, "event-50", True), (50, "event-49", False)],
)
def test_events_page_renders_table_and_load_more_state(
    count: int,
    expected_subject: str,
    expect_load_more: bool,
) -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        _insert_app_events(test_app, count=count)

        headers = {"X-Forwarded-Prefix": "/raid"} if expect_load_more else {}
        response = client.get("/events", headers=headers)

    assert response.status_code == 200
    assert '<th scope="col">Time</th>' in response.text
    assert expected_subject in response.text
    assert ("Load more" in response.text) is expect_load_more
    if expect_load_more:
        assert 'hx-get="/raid/partials/events"' in response.text


def test_events_partial_without_cursor_returns_auto_refresh_fragment() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        _insert_app_events(test_app, count=51)
        response = client.get("/partials/events")

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div\n  id="events-data"')
    assert "event-50" in response.text
    assert "Load more" not in response.text
    assert 'hx-get="/partials/events"' in response.text
    assert 'hx-trigger="every 30s"' in response.text
    assert 'hx-target="this"' in response.text
    assert 'hx-swap="outerHTML"' in response.text
    assert "<!doctype html>" not in response.text
    assert "site-header" not in response.text


def test_events_partial_with_valid_cursor_returns_load_more_fragment() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        inserted = _insert_app_events(test_app, count=51)
        cursor_event = inserted[1]

        response = client.get(
            "/partials/events",
            params={
                "before_occurred_at": cursor_event.occurred_at.isoformat(),
                "before_id": str(cursor_event.id),
            },
        )

    assert response.status_code == 200
    assert 'id="events-data"' not in response.text
    assert "<!doctype html>" not in response.text
    assert "event-0" in response.text
    assert "event-1" not in response.text


@pytest.mark.parametrize(
    ("params", "expected_detail"),
    [
        (
            {"before_occurred_at": datetime(2026, 4, 25, 12, 0, tzinfo=UTC).isoformat()},
            "before_occurred_at and before_id must be provided together",
        ),
        ({"before_id": "1"}, "before_occurred_at and before_id must be provided together"),
        (
            {"before_occurred_at": "2026-04-25T12:00:00", "before_id": "1"},
            "before_occurred_at must include a timezone",
        ),
    ],
)
def test_events_partial_rejects_invalid_cursors(
    params: dict[str, str],
    expected_detail: str,
) -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/partials/events", params=params)

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_events_page_formats_time_and_severity_badges() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        occurred_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
        for index, severity in enumerate(("info", "warning", "critical", "other")):
            _insert_app_event(
                test_app,
                occurred_at=occurred_at + timedelta(minutes=index),
                severity=severity,
                subject=f"severity-{severity}",
            )

        response = client.get("/events")

    assert response.status_code == 200
    assert "2026-04-25 14:00:00 CEST" in response.text
    assert "status-badge--optimal" in response.text
    assert "status-badge--warning" in response.text
    assert "status-badge--critical" in response.text
    assert "status-badge--unknown" in response.text
    assert "Info" in response.text
    assert "Warning" in response.text
    assert "Critical" in response.text
    assert "Unknown" in response.text


def _insert_app_events(test_app: FastAPI, *, count: int) -> tuple[_InsertedEvent, ...]:
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    return tuple(
        _insert_app_event(
            test_app,
            occurred_at=base_time + timedelta(minutes=index),
            subject=f"event-{index}",
        )
        for index in range(count)
    )


def _insert_app_event(
    test_app: FastAPI,
    *,
    occurred_at: datetime,
    severity: str = "info",
    category: str = "physical_drive",
    subject: str = "event",
    summary: str = "Drive state changed",
) -> _InsertedEvent:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        event = record_event(
            session,
            severity=severity,
            category=category,
            subject=subject,
            summary=summary,
        )
        event.occurred_at = occurred_at
        session.commit()
        return _InsertedEvent(id=event.id, occurred_at=event.occurred_at)
