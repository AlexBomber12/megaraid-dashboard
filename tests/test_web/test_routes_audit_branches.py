"""Branch coverage for ``/audit`` redirect plus the operator-action filter.

The redirect itself is happy-path-only, but the operator-action category
filter must surface only ``operator_action`` events on the resulting events
page; this also exercises the no-op ``operator_action_only=True`` branch
of the category filter pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator
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
        "METRICS_LOCK_PATH": str(tmp_path / "metrics.lock"),
        "DATABASE_URL": "sqlite:///:memory:",
        "LOG_LEVEL": "INFO",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_audit_redirect_preserves_carry_forward_query_path() -> None:
    """The redirect target points at /events with a single operator_action category."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/audit", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/events?category=operator_action"


def test_audit_filter_excludes_non_operator_action_categories() -> None:
    """Following the redirect must show operator actions but exclude foreign categories."""
    test_app = create_app()
    occurred_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_operator_action(test_app, username="admin", message="locate start drive e252:s4")
        _insert_event(
            test_app,
            occurred_at=occurred_at + timedelta(minutes=1),
            category="pd_state",
            subject="pd-state",
            summary="PD state changed",
        )
        _insert_event(
            test_app,
            occurred_at=occurred_at + timedelta(minutes=2),
            category="temperature",
            subject="temp-warn",
            summary="Drive temperature warning",
        )

        response = client.get("/audit", follow_redirects=True)

    assert response.status_code == 200
    assert "locate start drive" in response.text
    assert "PD state changed" not in response.text
    assert "Drive temperature warning" not in response.text


def test_audit_empty_state_when_no_operator_actions_recorded() -> None:
    """``/audit`` with no operator events yields the empty-state messaging."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_event(
            test_app,
            occurred_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
            category="pd_state",
            subject="pd-state",
            summary="PD state changed",
        )

        response = client.get("/audit", follow_redirects=True)

    assert response.status_code == 200
    assert "PD state changed" not in response.text
    assert "Waiting for first metrics collection" in response.text


def _insert_operator_action(test_app: FastAPI, *, username: str, message: str) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        record_operator_action(session, username=username, message=message)
        session.commit()


def _insert_event(
    test_app: FastAPI,
    *,
    occurred_at: datetime,
    category: str,
    subject: str,
    summary: str,
) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        event = record_event(
            session,
            severity="info",
            category=category,
            subject=subject,
            summary=summary,
        )
        event.occurred_at = occurred_at
        session.commit()
