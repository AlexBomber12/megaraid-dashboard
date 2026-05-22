from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services import overview as overview_module
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


def test_main_page_renders_new_overview(
    monkeypatch: pytest.MonkeyPatch,
    sample_snapshot: StorcliSnapshot,
) -> None:
    monkeypatch.setattr(
        overview_module,
        "_load_patrol_read_state",
        lambda snapshot: _OperationState(
            progress_percent=None,
            is_running=False,
            last_run_timestamp="2026-04-25 03:46:00",
        ),
    )
    test_app = create_app()
    snapshot = _snapshot_with_first_drive_media_error(sample_snapshot)
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(
            test_app,
            snapshot,
            raw_payload={"Next Patrol Read launch": "Jun 1, 2026 00:00"},
        )
        _insert_events(test_app, count=12)

        response = client.get("/")

    anchors = _anchor_hrefs(response.text)
    assert response.status_code == 200
    assert "<h1>SERVER RAID Status</h1>" not in response.text
    assert "Updated:" not in response.text
    assert response.text.count("LSI MegaRAID SAS 9270CV-8i") == 1
    assert (
        '<a href="/controller" class="controller-card controller-card--optimal">' in response.text
    )
    assert '<div class="controller-state">OPTIMAL</div>' in response.text
    assert 'status-badge--optimal">OPTIMAL' not in response.text
    assert '<div class="controller-card-row2">' in response.text
    assert response.text.count('<div class="metric-item">') == 4
    assert '<span class="metric-label">RoC</span>' in response.text
    assert '<span class="metric-label">CacheVault</span>' in response.text
    assert '<span class="metric-label">BBU</span>' in response.text
    assert '<span class="metric-label">Errors (24h)</span>' in response.text
    assert "110&deg;C" in response.text
    assert "OPTIMAL" in response.text
    assert response.text.count('class="drive-tile-v2 ') == 8
    assert '<span class="drive-error-badge-v2">1</span>' in response.text
    assert response.text.count('class="activity-item-v2"') == 10
    assert '<span class="activity-item-v2__age">pd_state</span>' not in response.text
    assert '<span class="activity-item-v2__age">' in response.text
    assert "ago</span>" in response.text
    assert "Last patrol read Apr 25" in response.text
    assert "2026-04-25T03:46:00Z UTC" not in response.text
    assert "Next in " in response.text
    assert "Notifier" in response.text
    assert "ok" in response.text
    assert "Collector" in response.text
    assert "DB" in response.text
    assert response.text.count('class="status-bar"') == 1
    assert 'class="site-footer"' not in response.text
    assert "Build unknown" not in response.text
    assert "/controller" in anchors
    assert "/controller/foreign-config" not in anchors
    assert "/drives/252:0" in anchors
    assert "site-nav-v2__link--active" in response.text
    assert 'aria-current="page"' in response.text
    assert ">Overview</a>" in response.text


def _insert_app_snapshot(
    test_app: FastAPI,
    sample_snapshot: StorcliSnapshot,
    *,
    raw_payload: dict[str, object] | None = None,
) -> None:
    with test_app.state.session_factory() as session:
        insert_snapshot(
            session,
            sample_snapshot,
            store_raw=raw_payload is not None,
            raw_payload=raw_payload,
        )
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


def _snapshot_with_first_drive_media_error(sample_snapshot: StorcliSnapshot) -> StorcliSnapshot:
    physical_drives = [
        drive.model_copy(update={"media_errors": 1 if index == 0 else 0})
        for index, drive in enumerate(sample_snapshot.physical_drives)
    ]
    return sample_snapshot.model_copy(update={"physical_drives": physical_drives})


def _anchor_hrefs(html: str) -> set[str]:
    parser = _AnchorParser()
    parser.feed(html)
    return parser.hrefs


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for key, value in attrs:
            if key == "href" and value is not None:
                self.hrefs.add(value)


@dataclass(frozen=True)
class _OperationState:
    progress_percent: int | None
    is_running: bool = True
    last_run_timestamp: str | None = None
