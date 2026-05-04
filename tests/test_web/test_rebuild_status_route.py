from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.audit import record_operator_action
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


def test_drive_rebuild_status_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        calls.append(list(args))
        return _rebuild_payload(percent=42, state="In progress", eta="1234 Minutes")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/2:0/replace/rebuild-status")

    assert response.status_code == 200
    assert response.json() == {
        "enclosure": 2,
        "slot": 0,
        "percent_complete": 42,
        "state": "In progress",
        "time_remaining_minutes": 1234,
    }
    assert calls == [["/c0/e2/s0", "show", "rebuild", "J"]]


def test_drive_rebuild_status_records_completion_audit_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=100, state="Complete")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        first = client.get("/drives/2:0/replace/rebuild-status")
        second = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(events) == 1
    assert events[0].category == "operator_action"
    assert events[0].severity == "info"
    assert events[0].summary == "rebuild complete drive 2:0"
    assert events[0].operator_username == "admin"


def test_drive_rebuild_status_does_not_duplicate_completion_audit_after_later_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=100, state="Complete")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        first = client.get("/drives/2:0/replace/rebuild-status")
        _record_operator_action(test_app, summary="locate start drive 2:0")
        second = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert first.status_code == 200
    assert second.status_code == 200
    assert [event.summary for event in events] == [
        "rebuild complete drive 2:0",
        "locate start drive 2:0",
    ]


def _rebuild_payload(
    *,
    percent: int,
    state: str,
    eta: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "Progress%": f"{percent}%",
        "State": state,
    }
    if eta is not None:
        row["Estimated Time Left"] = eta
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {"Drive /c0/e2/s0 - Rebuild Progress": [row]},
            }
        ]
    }


def _all_events(test_app: FastAPI) -> list[Event]:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return list(session.scalars(select(Event)).all())


def _record_operator_action(test_app: FastAPI, *, summary: str) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session, session.begin():
        record_operator_action(session, username="admin", message=summary)
