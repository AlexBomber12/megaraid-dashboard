from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import set_maintenance_state
from megaraid_dashboard.db.models import Event
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


@pytest.fixture(autouse=True)
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", TEST_ADMIN_PASSWORD_HASH)
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_maintenance_start_returns_200_and_get_shows_active(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/maintenance/start",
            headers=headers,
            json={"duration_minutes": 30, "reason": "replace failed drive"},
        )
        get_response = client.get("/maintenance")

    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["started_by"] == "admin"
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["active"] is True
    assert get_payload["started_by"] == "admin"
    assert get_payload["remaining_seconds"] > 0


@pytest.mark.parametrize(
    "body",
    [
        {"duration_minutes": 0, "reason": "replace failed drive"},
        {"duration_minutes": 1441, "reason": "replace failed drive"},
        {"duration_minutes": 30, "reason": ""},
        {"duration_minutes": 30, "reason": "   "},
    ],
)
def test_maintenance_start_validates_body(
    csrf_headers: Callable[[TestClient], dict[str, str]],
    body: dict[str, object],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/maintenance/start", headers=headers, json=body)

    assert response.status_code == 422


def test_maintenance_stop_after_start_clears_state_and_records_audit(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        start_response = client.post(
            "/maintenance/start",
            headers=headers,
            json={"duration_minutes": 30, "reason": "replace failed drive"},
        )
        stop_response = client.post("/maintenance/stop", headers=headers)
        get_response = client.get("/maintenance")

        assert start_response.status_code == 200
        assert stop_response.status_code == 200
        assert get_response.json() == {
            "active": False,
            "expires_at": None,
            "started_by": None,
            "remaining_seconds": None,
        }
        events = _operator_events(test_app)

    assert [event.summary for event in events] == [
        "maintenance start duration 30 min reason: replace failed drive",
        "maintenance stop",
    ]
    assert {event.operator_username for event in events} == {"admin"}


def test_maintenance_stop_when_inactive_is_noop_without_audit(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/maintenance/stop", headers=headers)
        events = _operator_events(test_app)

    assert response.status_code == 200
    assert response.json() == {"active": False}
    assert events == []


def test_maintenance_get_after_expiration_reports_inactive() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        session_factory = test_app.state.session_factory
        assert isinstance(session_factory, sessionmaker)
        with session_factory() as session, session.begin():
            set_maintenance_state(
                session,
                active=True,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
                started_by="admin",
            )

        response = client.get("/maintenance")

    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["remaining_seconds"] is None


def test_maintenance_start_without_csrf_returns_403() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/maintenance/start",
            json={"duration_minutes": 30, "reason": "replace failed drive"},
        )

    assert response.status_code == 403


def test_maintenance_stop_without_auth_returns_401(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as authed_client:
        headers = _csrf_request_headers(authed_client, csrf_headers)

    with TestClient(test_app) as client:
        response = client.post("/maintenance/stop", headers=headers)

    assert response.status_code == 401


def _operator_events(test_app: object) -> list[Event]:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return list(
            session.scalars(
                select(Event)
                .where(Event.category == "operator_action")
                .order_by(Event.occurred_at, Event.id)
            )
        )


def _csrf_request_headers(
    client: TestClient,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> dict[str, str]:
    headers = csrf_headers(client)
    token = headers["X-CSRF-Token"]
    return {**headers, "Cookie": f"__Host-csrf={token}"}
