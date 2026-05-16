"""Branch coverage for ``/partials/events`` cursor and ``since`` parsing.

Covers the ``_parse_events_cursor`` ValueError handlers (lines 3268-3269 and
3281-3282), the ``_parse_events_since`` ValueError handler (lines 3292-3293),
and the negative-since rejection (line 3295) in ``web/routes.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
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


def test_events_partial_rejects_malformed_before_occurred_at() -> None:
    """Cover lines 3268-3269: ``datetime.fromisoformat`` raises for garbage input."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/partials/events",
            params={"before_occurred_at": "not-a-datetime", "before_id": "1"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "before_occurred_at must be a valid ISO 8601 datetime"


def test_events_partial_rejects_non_integer_before_id() -> None:
    """Cover lines 3281-3282: ``int(before_id)`` raises for non-integer input."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/partials/events",
            params={
                "before_occurred_at": "2026-04-25T12:00:00+00:00",
                "before_id": "not-an-int",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "before_id must be an integer"


def test_events_partial_rejects_non_integer_since() -> None:
    """Cover lines 3292-3293: ``int(since)`` raises for non-integer input."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/partials/events",
            params={"since": "not-an-int"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "since must be an integer"


def test_events_partial_rejects_negative_since() -> None:
    """Cover line 3295: negative ``since`` must be rejected with a 400."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/partials/events",
            params={"since": "-1"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "since must be non-negative"


def test_events_partial_empty_state_renders_waiting_copy() -> None:
    """Sanity check that the empty-state copy survives the partial response."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/partials/events")

    assert response.status_code == 200
    assert "Waiting for first metrics collection" in response.text
