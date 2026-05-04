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
from megaraid_dashboard.db.dao import insert_snapshot, set_maintenance_state
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
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("path", ["/", "/drives", "/drives/252:4", "/events"])
def test_maintenance_banner_is_hidden_when_inactive(
    path: str,
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get(path)

    assert response.status_code == 200
    assert 'class="maintenance-banner"' not in response.text


@pytest.mark.parametrize("path", ["/", "/drives", "/drives/252:4", "/events"])
def test_maintenance_banner_is_visible_when_active(
    path: str,
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _set_active_maintenance(test_app)

        response = client.get(path)

    assert response.status_code == 200
    assert 'class="maintenance-banner"' in response.text
    assert "Maintenance mode active" in response.text
    assert "started by admin" in response.text
    assert "data-maintenance-countdown" in response.text


def test_maintenance_banner_stop_button_posts_to_stop(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _set_active_maintenance(test_app)

        response = client.get("/")

    assert response.status_code == 200
    assert 'hx-post="/maintenance/stop"' in response.text
    assert "Stop maintenance" in response.text
    assert "window.location.reload()" in response.text


def test_overview_hides_start_form_when_maintenance_active(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _set_active_maintenance(test_app)

        response = client.get("/")

    assert response.status_code == 200
    assert 'class="maintenance-start"' not in response.text
    assert 'hx-post="/maintenance/start"' not in response.text


def test_overview_shows_start_form_when_maintenance_inactive(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/")

    assert response.status_code == 200
    assert 'class="maintenance-start"' in response.text
    assert 'hx-post="/maintenance/start"' in response.text
    assert 'hx-ext="json-enc"' in response.text
    for duration in ("15", "30", "60", "240", "1440"):
        assert f'<option value="{duration}"' in response.text


def test_maintenance_js_defines_json_encoder_extension() -> None:
    source = Path("src/megaraid_dashboard/static/js/maintenance.js").read_text(encoding="utf-8")

    assert 'defineExtension("json-enc"' in source
    assert 'headers["Content-Type"] = "application/json"' in source
    assert "JSON.stringify" in source


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        insert_snapshot(session, sample_snapshot)
        session.commit()


def _set_active_maintenance(test_app: FastAPI) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            started_by="admin",
        )
