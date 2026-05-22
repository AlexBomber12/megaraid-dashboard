from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.storcli import StorcliSnapshot
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


def test_main_page_refresh_returns_swap_safe_partial(sample_snapshot: StorcliSnapshot) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_events(test_app, count=10)

        response = client.get("/partials/main-page")

    html = response.text.lower()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert "<html" not in html
    assert "<head" not in html
    assert '<a\n  class="controller-card-v2 controller-card-v2--optimal"' in response.text
    assert "OPTIMAL" in response.text
    assert "Updated:" in response.text
    assert '<time datetime="' in response.text
    assert "data-local-time hidden" in response.text
    assert response.text.count('class="drive-tile-v2 ') == 8
    assert '<section class="activity-v2" aria-label="Recent activity">' in response.text
    assert response.text.count('class="activity-item-v2"') == 10


def test_main_page_refresh_requires_auth() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/partials/main-page")

    assert response.status_code == 401


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    with test_app.state.session_factory() as session:
        insert_snapshot(session, sample_snapshot)
        session.commit()


def _insert_events(test_app: FastAPI, *, count: int) -> None:
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    with test_app.state.session_factory() as session:
        for index in range(count):
            session.add(
                Event(
                    occurred_at=base_time + timedelta(minutes=index),
                    severity="warning",
                    category="pd_state",
                    subject="e252:s0",
                    summary=f"event {index}",
                )
            )
        session.commit()
