from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import MaintenanceState
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
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_maintenance_get_reports_active_with_no_remaining_when_remaining_seconds_truncates_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover branch 312->314: active state where int(remaining seconds) is not > 0.

    The DAO returns active=False once expires_at <= now, but a sub-second window
    where expires_at is still strictly greater than now yet the truncated
    integer of (expires_at - now).total_seconds() is zero is reachable in
    practice. We pin that state by stubbing get_maintenance_state so the route
    must take the False arm of `if remaining > 0` and leave remaining_seconds
    as None.
    """

    def fake_get_maintenance_state(session: Any, *, now: datetime) -> MaintenanceState:
        return MaintenanceState(active=True, expires_at=now, started_by="admin")

    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.get_maintenance_state",
        fake_get_maintenance_state,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/maintenance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] is True
    assert payload["started_by"] == "admin"
    assert payload["remaining_seconds"] is None
    assert payload["expires_at"] is not None


def test_drive_detail_slot_ref_without_colon_returns_404() -> None:
    """Cover line 428: slot_ref with no ':' separator raises HTTPException(404)."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/no-colon-segment")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "slot_ref",
    [
        "abc:4",
        "252:def",
        ":4",
        "252:",
    ],
)
def test_drive_detail_slot_ref_non_integer_parts_return_404(slot_ref: str) -> None:
    """Cover lines 432-433: non-integer enclosure or slot raises HTTPException(404)."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(f"/drives/{slot_ref}")

    assert response.status_code == 404
