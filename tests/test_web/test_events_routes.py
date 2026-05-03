from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot, record_event
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


@dataclass(frozen=True)
class _InsertedEvent:
    id: int
    occurred_at: datetime


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
        "DATABASE_URL": "sqlite:///:memory:",
        "LOG_LEVEL": "INFO",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_events_empty_database_renders_empty_state_without_load_more() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/events")
        partial_response = client.get("/partials/events")

    assert response.status_code == 200
    assert "Waiting for first metrics collection" in response.text
    assert "No events recorded yet." in response.text
    assert "Load more" not in response.text
    assert 'id="events-data"' in response.text
    assert 'id="events-pagination"' in response.text
    assert 'hx-trigger="every 30s"' in response.text
    assert partial_response.status_code == 200
    assert "Waiting for first metrics collection" in partial_response.text
    assert "No events recorded yet." in partial_response.text


def test_events_route_requires_authentication() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/events")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="megaraid-dashboard"'


@pytest.mark.parametrize(
    ("count", "expected_subject", "expect_load_more"),
    [(51, "event-50", True), (50, "event-49", False)],
)
def test_events_page_renders_timeline_and_load_more_state(
    count: int,
    expected_subject: str,
    expect_load_more: bool,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_events(test_app, count=count)

        headers = {"X-Forwarded-Prefix": "/raid"} if expect_load_more else {}
        response = client.get("/events", headers=headers)

    assert response.status_code == 200
    assert 'aria-label="Events timeline"' in response.text
    assert 'id="events-list"' in response.text
    assert "<thead>" not in response.text
    assert "drive-table" not in response.text
    assert expected_subject in response.text
    assert ("Load more" in response.text) is expect_load_more
    assert response.text.count('id="events-pagination"') == 1
    assert 'id="events-poller"' in response.text
    assert 'hx-trigger="every 30s"' in response.text
    assert 'hx-swap="none"' in response.text
    if expect_load_more:
        assert "/partials/events" in response.text
        assert "before_occurred_at=" in response.text
        assert response.text.count('id="events-load-more"') == 1


def test_events_page_links_event_detector_slot_tokens_with_forwarded_prefix() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_event(
            test_app,
            occurred_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
            subject="PD e252:s4",
            summary="PD e252:s4 state is Failed",
        )

        response = client.get("/events", headers={"X-Forwarded-Prefix": "/raid"})

    assert response.status_code == 200
    assert 'PD <a href="/raid/drives/252:4">e252:s4</a> state is Failed' in response.text


def test_events_partial_without_cursor_returns_auto_refresh_fragment() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_events(test_app, count=51)
        response = client.get("/partials/events")

    assert response.status_code == 200
    assert response.text.lstrip().startswith("<div")
    assert 'id="events-data"' in response.text
    assert "event-50" in response.text
    assert "Load more" in response.text
    assert 'id="events-pagination"' in response.text
    assert 'hx-swap-oob="true"' in response.text
    assert "/partials/events" in response.text
    assert "since=51" in response.text
    assert 'hx-trigger="every 30s"' in response.text
    assert 'hx-swap="none"' in response.text
    assert "<!doctype html>" not in response.text
    assert "site-header" not in response.text


def test_events_partial_without_cursor_refreshes_header_timestamp(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_app_events(test_app, count=1)
        response = client.get("/partials/events")

    assert response.status_code == 200
    assert "<h1>Events</h1>" in response.text
    assert "LSI MegaRAID SAS9270CV-8i" in response.text
    assert 'datetime="2026-04-25T12:00:00Z" data-local-time hidden' in response.text
    assert "<noscript>2026-04-25T12:00:00Z UTC</noscript>" in response.text
    assert "Waiting for first metrics collection" not in response.text


def test_events_partial_with_valid_cursor_returns_load_more_fragment() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        inserted = _insert_app_events(test_app, count=102)
        cursor_event = inserted[51]

        response = client.get(
            "/partials/events",
            params={
                "before_occurred_at": cursor_event.occurred_at.isoformat(),
                "before_id": str(cursor_event.id),
            },
        )

    assert response.status_code == 200
    assert 'id="events-data"' not in response.text
    assert "<thead>" not in response.text
    assert "<li" in response.text
    assert "<!doctype html>" not in response.text
    assert "event-50" in response.text
    assert "event-51" not in response.text
    assert 'id="events-pagination" hx-swap-oob="true"' in response.text
    assert 'hx-target="#events-list"' in response.text
    assert 'hx-swap="beforeend"' in response.text


def test_events_category_filter_is_preserved_across_partial_requests() -> None:
    test_app = create_app()
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        cachevault_events = tuple(
            _insert_app_event(
                test_app,
                occurred_at=base_time + timedelta(minutes=index),
                category="cv_state",
                subject=f"cachevault-{index}",
            )
            for index in range(52)
        )
        _insert_app_event(
            test_app,
            occurred_at=base_time + timedelta(minutes=1, seconds=30),
            category="pd_state",
            subject="physical-drive-leak",
        )

        initial_page_response = client.get("/events", params={"category": "cv_state"})
        refresh_response = client.get("/partials/events", params={"category": "cv_state"})
        cursor_event = cachevault_events[2]
        pagination_response = client.get(
            "/partials/events",
            params={
                "before_occurred_at": cursor_event.occurred_at.isoformat(),
                "before_id": str(cursor_event.id),
                "category": "cv_state",
            },
        )

    assert initial_page_response.status_code == 200
    assert refresh_response.status_code == 200
    assert pagination_response.status_code == 200
    assert "category=cv_state" in initial_page_response.text
    assert "since=" in initial_page_response.text
    assert "category=cv_state" in refresh_response.text
    assert "since=" in refresh_response.text
    assert "cachevault-51" in refresh_response.text
    assert "physical-drive-leak" not in refresh_response.text
    assert "cachevault-1" in pagination_response.text
    assert "physical-drive-leak" not in pagination_response.text


def test_events_filters_by_severity_and_category() -> None:
    test_app = create_app()
    occurred_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_event(
            test_app,
            occurred_at=occurred_at,
            severity="critical",
            category="pd_state",
            subject="matching-critical-pd",
        )
        _insert_app_event(
            test_app,
            occurred_at=occurred_at + timedelta(minutes=1),
            severity="warning",
            category="pd_state",
            subject="wrong-severity",
        )
        _insert_app_event(
            test_app,
            occurred_at=occurred_at + timedelta(minutes=2),
            severity="critical",
            category="temperature",
            subject="wrong-category",
        )

        response = client.get(
            "/events",
            params={"severity": "critical", "category": "pd_state"},
        )

    assert response.status_code == 200
    assert "matching-critical-pd" in response.text
    assert "wrong-severity" not in response.text
    assert "wrong-category" not in response.text


def test_events_filter_chips_reflect_active_state() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/events", params={"severity": "critical", "category": "pd_state"})

    assert response.status_code == 200
    assert 'class="filter-chip filter-chip--active"' in response.text
    assert 'href="/events?category=pd_state"' in response.text
    assert 'href="/events?severity=critical"' in response.text


def test_events_category_filter_chips_use_persisted_category_keys() -> None:
    test_app = create_app()
    expected_categories = (
        "controller",
        "pd_state",
        "vd_state",
        "cv_state",
        "smart_alert",
        "media_errors",
        "other_errors",
        "predictive_failures",
        "temperature",
        "controller_temperature",
        "disk_space",
        "system",
        "operator_action",
    )

    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/events")

    assert response.status_code == 200
    for category in expected_categories:
        assert f'href="/events?category={category}"' in response.text
    for legacy_category in ("vd", "pd", "cachevault", "physical_drive"):
        assert f'href="/events?category={legacy_category}"' not in response.text


def test_events_since_poll_returns_oob_newer_events_only() -> None:
    test_app = create_app()
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        first = _insert_app_event(test_app, occurred_at=base_time, subject="old-event")
        _insert_app_event(
            test_app,
            occurred_at=base_time + timedelta(minutes=1),
            subject="new-event",
        )

        response = client.get(
            "/events", params={"since": str(first.id)}, headers={"HX-Request": "true"}
        )

    assert response.status_code == 200
    assert 'hx-swap-oob="afterbegin:#events-list"' in response.text
    assert "new-event" in response.text
    assert "old-event" not in response.text
    assert 'id="events-poller"' in response.text


def test_events_since_poll_replaces_empty_state_when_first_events_arrive() -> None:
    test_app = create_app()
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_event(test_app, occurred_at=base_time, subject="first-event")

        response = client.get("/events", params={"since": "0"}, headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert '<div\n    id="events-data"' in response.text
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert 'id="events-list"' in response.text
    assert "first-event" in response.text
    assert 'hx-swap-oob="afterbegin:#events-list"' not in response.text
    assert 'id="events-poller"' in response.text
    assert 'hx-swap-oob="true"' in response.text


def test_events_since_poll_preserves_since_when_no_new_events() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/events",
            params={"since": "42"},
            headers={"HX-Request": "true"},
        )

    assert response.status_code == 200
    assert 'hx-swap-oob="afterbegin:#events-list"' in response.text
    assert "since=42" in response.text


@pytest.mark.parametrize(
    ("params", "expected_detail"),
    [
        (
            {"before_occurred_at": datetime(2026, 4, 25, 12, 0, tzinfo=UTC).isoformat()},
            "before_occurred_at and before_id must be provided together",
        ),
        ({"before_id": "1"}, "before_occurred_at and before_id must be provided together"),
        (
            {"before_occurred_at": "2026-04-25T12:00:00", "before_id": "1"},
            "before_occurred_at must include a timezone",
        ),
    ],
)
def test_events_partial_rejects_invalid_cursors(
    params: dict[str, str],
    expected_detail: str,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/partials/events", params=params)

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_events_page_formats_time_and_severity_badges() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        occurred_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
        for index, severity in enumerate(("info", "warning", "critical", "other")):
            _insert_app_event(
                test_app,
                occurred_at=occurred_at + timedelta(minutes=index),
                severity=severity,
                subject=f"severity-{severity}",
            )

        response = client.get("/events")

    assert response.status_code == 200
    assert 'datetime="2026-04-25T12:00:00Z" data-local-time' in response.text
    assert "2026-04-25T12:00:00Z UTC" in response.text
    assert "status-badge--optimal" in response.text
    assert "status-badge--warning" in response.text
    assert "status-badge--critical" in response.text
    assert "status-badge--unknown" in response.text
    assert "Info" in response.text
    assert "Warning" in response.text
    assert "Critical" in response.text
    assert "Unknown" in response.text


def _insert_app_events(test_app: FastAPI, *, count: int) -> tuple[_InsertedEvent, ...]:
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    return tuple(
        _insert_app_event(
            test_app,
            occurred_at=base_time + timedelta(minutes=index),
            subject=f"event-{index}",
        )
        for index in range(count)
    )


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        insert_snapshot(session, sample_snapshot)
        session.commit()


def _insert_app_event(
    test_app: FastAPI,
    *,
    occurred_at: datetime,
    severity: str = "info",
    category: str = "pd_state",
    subject: str = "event",
    summary: str = "Drive state changed",
) -> _InsertedEvent:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        event = record_event(
            session,
            severity=severity,
            category=category,
            subject=subject,
            summary=summary,
        )
        event.occurred_at = occurred_at
        session.commit()
        return _InsertedEvent(id=event.id, occurred_at=event.occurred_at)
