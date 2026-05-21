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
from megaraid_dashboard.db.models import ControllerSnapshot
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


def test_get_with_default_range_returns_200_json() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_regular_snapshots(test_app, count=2, step=timedelta(minutes=5))

        response = client.get("/controller/roc-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["range_label"] == "last 24 hours"
    assert payload["points"][0]["temperature_celsius"] == 70


def test_get_without_auth_returns_401() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/controller/roc-history")

    assert response.status_code == 401


def test_range_hours_1_returns_minute_resolution_data() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_regular_snapshots(test_app, count=12, step=timedelta(minutes=5))

        response = client.get("/controller/roc-history?range_hours=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["range_label"] == "last 1 hour"
    assert len(payload["points"]) == 12


def test_range_hours_720_returns_daily_resolution_data() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_regular_snapshots(test_app, count=30, step=timedelta(days=1))

        response = client.get("/controller/roc-history?range_hours=720")

    assert response.status_code == 200
    payload = response.json()
    assert payload["range_label"] == "last 30 days"
    assert len(payload["points"]) == 30


def test_invalid_range_hours_returns_422() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/roc-history?range_hours=999")

    assert response.status_code == 422


def test_cache_control_header_includes_max_age_30() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/roc-history")

    assert response.headers["Cache-Control"] == "private, max-age=30"


def test_json_shape_matches_series_contract() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_regular_snapshots(test_app, count=1, step=timedelta(minutes=5))

        response = client.get("/controller/roc-history")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "points",
        "range_label",
        "min_celsius",
        "avg_celsius",
        "max_celsius",
        "current_celsius",
        "warning_threshold",
        "critical_threshold",
    }
    point = payload["points"][0]
    assert set(point) == {"captured_at", "temperature_celsius"}
    assert payload["min_celsius"] == 70
    assert payload["avg_celsius"] == 70
    assert payload["max_celsius"] == 70
    assert payload["current_celsius"] == 70
    assert payload["warning_threshold"] == 95
    assert payload["critical_threshold"] == 105


def _seed_regular_snapshots(
    test_app: FastAPI,
    *,
    count: int,
    step: timedelta,
) -> None:
    latest = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    first = latest - (step * (count - 1))
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session, session.begin():
        for index in range(count):
            session.add(
                ControllerSnapshot(
                    captured_at=first + (step * index),
                    model_name="MegaRAID SAS 9270CV-8i",
                    serial_number="SV00000001",
                    firmware_version="23.34.0-0019",
                    bios_version="6.36.00.3_4.19.08.00_0x06180200",
                    driver_version="07.727.03.00",
                    alarm_state="Off",
                    cv_present=True,
                    bbu_present=False,
                    roc_temperature_celsius=70 + (index % 5),
                    raw_json=None,
                )
            )
