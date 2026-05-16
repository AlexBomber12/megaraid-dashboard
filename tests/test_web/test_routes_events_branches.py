"""Branch coverage for ``/events`` and event helper functions.

Covers ``_events_fragment_response`` when ``since=0`` and the database is
empty (line 3105), the ``_normalize_query_values`` duplicate-skip branch
(line 3313), and the entire ``_events_empty_next_run_text`` scheduler
ladder (lines 3421-3434) in ``web/routes.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.web.routes import _normalize_query_values
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


def test_events_since_zero_with_empty_database_returns_fragment() -> None:
    """Cover line 3105: ``since=0`` on an empty database falls through to a fragment.

    The branch only fires when ``load_events_page`` returns no events under
    the ``since=0`` short-circuit; ``load_events_fragment`` is invoked
    explicitly with ``since=0`` to produce the empty-state fragment that the
    SSE poller swaps back into the page.
    """
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/partials/events",
            params={"since": "0"},
            headers={"HX-Request": "true"},
        )

    assert response.status_code == 200
    # The since=0 fragment must preserve since=0 in the poller URL so the
    # browser keeps polling until the first events arrive.
    assert "since=0" in response.text
    assert 'hx-swap-oob="afterbegin:#events-list"' in response.text
    assert "<!doctype html>" not in response.text


def test_normalize_query_values_skips_duplicates_and_empty_values() -> None:
    """Cover the 3313->3311 branch: skip empty/duplicate stripped values."""
    assert _normalize_query_values(("foo", " foo ", "", "   ", "bar")) == ("foo", "bar")


def test_events_route_drops_duplicate_query_filters() -> None:
    """End-to-end exercise of the duplicate-skip branch via the events page."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/events", params=[("category", "pd_state"), ("category", "pd_state")]
        )

    assert response.status_code == 200
    # Filter chip URL should reflect a single occurrence, not duplicates.
    assert "category=pd_state&amp;category=pd_state" not in response.text


def test_events_empty_next_run_text_with_collector_enabled_but_no_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover lines 3421-3423: scheduler is None branch."""
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        if hasattr(test_app.state, "scheduler"):
            delattr(test_app.state, "scheduler")
        response = client.get("/events")

    assert response.status_code == 200
    assert "No collection run is currently scheduled." in response.text


def test_events_empty_next_run_text_when_metrics_job_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover lines 3424-3426: ``get_job`` returns None for unknown id."""
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        test_app.state.scheduler = _StubScheduler(job=None)
        response = client.get("/events")

    assert response.status_code == 200
    assert "No collection run is currently scheduled." in response.text


def test_events_empty_next_run_text_when_metrics_job_has_no_next_run_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the OR-branch in line 3425: ``metrics_job.next_run_time is None``."""
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        test_app.state.scheduler = _StubScheduler(job=_StubJob(next_run_time=None))
        response = client.get("/events")

    assert response.status_code == 200
    assert "No collection run is currently scheduled." in response.text


def test_events_empty_next_run_text_with_aware_next_run_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the 3429-False / 3432 branch: aware ``next_run_time`` astimezone path."""
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    aware_next_run = datetime.now(UTC) + timedelta(seconds=120)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        test_app.state.scheduler = _StubScheduler(job=_StubJob(next_run_time=aware_next_run))
        response = client.get("/events")

    assert response.status_code == 200
    assert "Next scheduled run in " in response.text
    assert " seconds." in response.text


def test_events_empty_next_run_text_with_naive_next_run_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the 3429-True / 3430 branch: naive ``next_run_time`` is treated as UTC."""
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    naive_next_run = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=180)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        test_app.state.scheduler = _StubScheduler(job=_StubJob(next_run_time=naive_next_run))
        response = client.get("/events")

    assert response.status_code == 200
    assert "Next scheduled run in " in response.text


class _StubJob:
    def __init__(self, *, next_run_time: datetime | None) -> None:
        self.next_run_time = next_run_time


class _StubScheduler:
    def __init__(self, *, job: _StubJob | None) -> None:
        self._job = job

    def get_job(self, job_id: str) -> _StubJob | None:
        if job_id != "metrics_collector":
            return None
        return self._job
