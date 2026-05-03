from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import record_event
from megaraid_dashboard.services.audit import record_operator_action
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


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
        "ADMIN_PASSWORD_HASH": TEST_ADMIN_PASSWORD_HASH,
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


def test_audit_route_redirects_to_operator_action_events_filter() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/audit", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/events?category=operator_action"


def test_events_operator_action_filter_shows_audit_rows_only() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_operator_action(test_app, username="admin", message="locate start drive e252:s4")
        _insert_event(test_app, category="pd_state", subject="pd-state", summary="PD state changed")

        response = client.get("/events", params={"category": "operator_action"})

    assert response.status_code == 200
    assert "locate start drive" in response.text
    assert "by admin" in response.text
    assert 'class="event-actor"' in response.text
    assert "PD state changed" not in response.text


def test_events_non_operator_action_row_does_not_render_actor_span() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_event(test_app, category="system", subject="system", summary="Collector paused")

        response = client.get("/events")

    assert response.status_code == 200
    assert "Collector paused" in response.text
    assert 'class="event-actor"' not in response.text
    assert "by admin" not in response.text


def test_events_operator_action_filter_chip_is_available() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/events")

    assert response.status_code == 200
    assert 'href="/events?category=operator_action"' in response.text
    assert ">operator action</a>" in response.text


def _insert_operator_action(test_app: FastAPI, *, username: str, message: str) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        record_operator_action(session, username=username, message=message)
        session.commit()


def _insert_event(test_app: FastAPI, *, category: str, subject: str, summary: str) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        record_event(
            session,
            severity="info",
            category=category,
            subject=subject,
            summary=summary,
        )
        session.commit()
