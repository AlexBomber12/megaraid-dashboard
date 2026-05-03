from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard import __version__
from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event, PhysicalDriveMetricsHourly
from megaraid_dashboard.storcli import StorcliSnapshot
from megaraid_dashboard.web.middleware import ForwardedPrefixMiddleware
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


def test_forwarded_prefix_middleware_sets_root_path_and_url_for() -> None:
    probe_app = FastAPI()
    probe_app.add_middleware(ForwardedPrefixMiddleware)

    @probe_app.get("/", name="probe")
    async def probe(request: Request) -> dict[str, str]:
        return {
            "root_path": cast(str, request.scope.get("root_path", "")),
            "url_path": request.url_for("probe").path,
        }

    client = TestClient(probe_app)

    prefixed = client.get("/", headers={"X-Forwarded-Prefix": "/raid"})
    unprefixed = client.get("/")

    assert prefixed.json() == {"root_path": "/raid", "url_path": "/raid/"}
    assert unprefixed.json() == {"root_path": "", "url_path": "/"}


@pytest.mark.parametrize(("forwarded_prefix", "expected_root_path"), [("/raid/", "/raid")])
def test_forwarded_prefix_middleware_normalizes_safe_trailing_slash(
    forwarded_prefix: str,
    expected_root_path: str,
) -> None:
    probe_app = FastAPI()
    probe_app.add_middleware(ForwardedPrefixMiddleware)

    @probe_app.get("/", name="probe")
    async def probe(request: Request) -> dict[str, str]:
        return {
            "root_path": cast(str, request.scope.get("root_path", "")),
            "url_path": request.url_for("probe").path,
        }

    client = TestClient(probe_app)

    response = client.get("/", headers={"X-Forwarded-Prefix": forwarded_prefix})

    assert response.json() == {
        "root_path": expected_root_path,
        "url_path": f"{expected_root_path}/",
    }


@pytest.mark.parametrize(
    "forwarded_prefix",
    [
        "//attacker.example",
        "/raid//admin",
        "/../raid",
        "/raid?next=evil",
        "/raid#fragment",
        "/raid value",
        "raid",
    ],
)
def test_forwarded_prefix_middleware_ignores_unsafe_prefixes(forwarded_prefix: str) -> None:
    probe_app = FastAPI()
    probe_app.add_middleware(ForwardedPrefixMiddleware)

    @probe_app.get("/", name="probe")
    async def probe(request: Request) -> dict[str, str]:
        return {
            "root_path": cast(str, request.scope.get("root_path", "")),
            "url_path": request.url_for("probe").path,
        }

    client = TestClient(probe_app)

    response = client.get("/", headers={"X-Forwarded-Prefix": forwarded_prefix})

    assert response.json() == {"root_path": "", "url_path": "/"}


def test_overview_navigation_and_assets_are_prefix_aware(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/", headers={"X-Forwarded-Prefix": "/raid"})

    assert response.status_code == 200
    assert "SERVER RAID Status" in response.text
    assert response.text.count('class="status-tile status-tile--') == 6
    for label in ("Controller", "VD", "RAID", "BBU", "MaxTemp", "RoC"):
        assert label in response.text
    assert "status-tile--optimal" in response.text
    assert 'class="alert-status"' in response.text
    assert response.text.count('class="alert-status__cell') == 4
    assert "alert-status__cell--neutral" in response.text
    assert "Notifier OK" in response.text
    assert "status-badge--optimal" in response.text
    assert "/raid/static/css/app.css" in response.text
    assert "/raid/static/vendor/htmx.min.js" in response.text
    assert "/raid/static/js/csrf.js" in response.text
    assert "/raid/static/js/local-time.js" in response.text
    assert "/raid/static/vendor/chart.min.js" not in response.text
    assert re.search(r"/raid/static/css/app\.css\?v=[0-9a-f]{12}", response.text) is not None
    assert (
        re.search(r"/raid/static/vendor/htmx\.min\.js\?v=[0-9a-f]{12}", response.text) is not None
    )
    assert re.search(r"/raid/static/js/csrf\.js\?v=[0-9a-f]{12}", response.text) is not None
    assert re.search(r"/raid/static/js/local-time\.js\?v=[0-9a-f]{12}", response.text) is not None
    assert 'data-local-time-clock aria-live="off" hidden' in response.text
    assert "/raid/partials/overview" in response.text
    assert {"/raid/", "/raid/drives", "/raid/events"}.issubset(_anchor_hrefs(response.text))
    status_tile_hrefs = _status_tile_hrefs(response.text)
    assert status_tile_hrefs
    assert all(href.startswith("/raid/") for href in status_tile_hrefs)
    assert "/raid/drives?sort=temperature-desc" not in status_tile_hrefs


def test_overview_navigation_is_prefix_free_without_forwarded_prefix(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/")

    assert response.status_code == 200
    assert "/static/css/app.css" in response.text
    assert "/static/vendor/htmx.min.js" in response.text
    assert "/static/js/csrf.js" in response.text
    assert "/static/js/local-time.js" in response.text
    assert "/static/vendor/chart.min.js" not in response.text
    assert re.search(r"/static/css/app\.css\?v=[0-9a-f]{12}", response.text) is not None
    assert re.search(r"/static/vendor/htmx\.min\.js\?v=[0-9a-f]{12}", response.text) is not None
    assert re.search(r"/static/js/csrf\.js\?v=[0-9a-f]{12}", response.text) is not None
    assert re.search(r"/static/js/local-time\.js\?v=[0-9a-f]{12}", response.text) is not None
    assert "/partials/overview" in response.text
    assert {"/", "/drives", "/events"}.issubset(_anchor_hrefs(response.text))
    assert "/drives?sort=temperature-desc" not in _status_tile_hrefs(response.text)
    assert "/raid/" not in response.text


def test_empty_database_renders_empty_state_on_full_page_and_partial() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        full_response = client.get("/")
        partial_response = client.get("/partials/overview")

    assert full_response.status_code == 200
    assert full_response.text.count('class="status-tile status-tile--') == 6
    assert "RoC" in full_response.text
    assert "status-tile--neutral" in full_response.text
    assert "Unknown" in full_response.text
    assert "Waiting for first metrics collection" in full_response.text
    assert "The collector has not yet completed its first run." in full_response.text
    assert "Metrics collection is disabled; no collection run is scheduled." in full_response.text
    assert 'class="alert-status"' in full_response.text
    assert full_response.text.count('class="alert-status__cell') == 4
    assert "alert-status__cell--neutral" in full_response.text
    assert "Never" in full_response.text
    assert "Notifier OK" in full_response.text
    assert "Waiting for first metrics collection" in partial_response.text
    assert partial_response.text.count('class="status-tile status-tile--') == 6
    assert "RoC" in partial_response.text
    assert "status-tile--neutral" in partial_response.text
    assert (
        "Metrics collection is disabled; no collection run is scheduled." in partial_response.text
    )
    assert 'class="alert-status"' in partial_response.text
    assert partial_response.text.count('class="alert-status__cell') == 4
    assert "Never" in partial_response.text
    assert "<!doctype html>" not in partial_response.text
    assert "site-header" not in partial_response.text


def test_dashboard_requires_authentication() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="megaraid-dashboard"'


def test_partial_endpoint_returns_data_block_fragment(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/partials/overview")

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div\n  id="data-block"')
    assert "<!doctype html>" not in response.text
    assert "site-header" not in response.text
    assert "SERVER RAID Status" in response.text
    assert response.text.count('class="status-tile status-tile--') == 6
    assert "RoC" in response.text
    assert "status-tile--optimal" in response.text
    assert 'class="alert-status"' in response.text
    assert response.text.count('class="alert-status__cell') == 4


def test_alert_status_pending_count_uses_warning_cell(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_pending_alert(test_app)

        response = client.get("/")

    assert response.status_code == 200
    assert response.text.count('class="alert-status__cell') == 4
    assert "alert-status__cell--warning" in response.text
    assert "status-badge--critical" in response.text
    assert "Pending" in response.text


def test_overview_renders_recent_activity_timeline_with_links(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_app_event(
            test_app,
            occurred_at=datetime(2026, 4, 25, 12, 1, tzinfo=UTC),
            category="physical_drive",
            summary="Drive state changed",
        )
        _insert_app_event(
            test_app,
            occurred_at=datetime(2026, 4, 25, 12, 2, tzinfo=UTC),
            category="cachevault",
            summary="CacheVault state changed",
            severity="warning",
        )

        response = client.get("/")

    assert response.status_code == 200
    assert '<section class="timeline"' in response.text
    assert "Drive state changed" in response.text
    assert "CacheVault state changed" in response.text
    assert '<a class="timeline__category" href="/events?category=physical_drive">' in response.text
    assert '<a class="timeline__category" href="/events?category=cachevault">' in response.text
    assert 'datetime="2026-04-25T12:02:00Z" data-local-time' in response.text
    assert "#icon-alert-triangle" in response.text


def test_overview_recent_activity_empty_state(sample_snapshot: StorcliSnapshot) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/")

    assert response.status_code == 200
    assert '<section class="timeline"' in response.text
    assert "No events yet." in response.text


def test_timeline_category_link_filters_events_page(sample_snapshot: StorcliSnapshot) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_app_event(
            test_app,
            occurred_at=datetime(2026, 4, 25, 12, 1, tzinfo=UTC),
            category="physical_drive",
            summary="Drive state changed",
        )
        _insert_app_event(
            test_app,
            occurred_at=datetime(2026, 4, 25, 12, 2, tzinfo=UTC),
            category="cachevault",
            summary="CacheVault state changed",
        )

        response = client.get("/events?category=cachevault")

    assert response.status_code == 200
    assert "CacheVault state changed" in response.text
    assert "Drive state changed" not in response.text


def test_data_block_has_auto_refresh_attributes() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/")

    assert 'id="data-block"' in response.text
    assert 'hx-get="/partials/overview"' in response.text
    assert 'hx-trigger="every 30s"' in response.text
    assert 'hx-target="this"' in response.text
    assert 'hx-swap="outerHTML"' in response.text


def test_vendored_htmx_exists_and_is_referenced() -> None:
    assert Path("src/megaraid_dashboard/static/vendor/htmx.min.js").exists()
    assert Path("src/megaraid_dashboard/static/js/csrf.js").exists()
    assert Path("src/megaraid_dashboard/static/js/local-time.js").exists()

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/")

    assert "/static/vendor/htmx.min.js" in response.text
    assert "/static/js/csrf.js" in response.text
    assert "/static/js/local-time.js" in response.text


def test_static_assets_are_served_with_far_future_cache_header() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        css_response = client.get("/static/css/app.css")
        csrf_response = client.get("/static/js/csrf.js")
        local_time_response = client.get("/static/js/local-time.js")
        chart_response = client.get("/static/vendor/chart.min.js")

    assert css_response.status_code == 200
    assert csrf_response.status_code == 200
    assert local_time_response.status_code == 200
    assert chart_response.status_code == 200
    assert "public" in css_response.headers["Cache-Control"]
    assert "max-age=31536000" in css_response.headers["Cache-Control"]
    assert "immutable" not in css_response.headers["Cache-Control"]
    assert "public" in local_time_response.headers["Cache-Control"]
    assert "max-age=31536000" in local_time_response.headers["Cache-Control"]
    assert "public" in chart_response.headers["Cache-Control"]
    assert "max-age=31536000" in chart_response.headers["Cache-Control"]


@pytest.mark.parametrize("path", ["/", "/drives", "/events"])
def test_operator_pages_render_local_time_markup(
    path: str,
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get(path)

    assert response.status_code == 200
    assert re.search(
        r'<time datetime="2026-04-25T12:[0-9]{2}:00Z" data-local-time hidden>',
        response.text,
    )
    assert re.search(r"<noscript>2026-04-25T12:[0-9]{2}:00Z UTC</noscript>", response.text)
    assert 'data-local-time-clock aria-live="off" hidden' in response.text


def test_drives_route_renders_drive_list_with_prefix_aware_detail_links(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives", headers={"X-Forwarded-Prefix": "/raid"})

    assert response.status_code == 200
    assert "Physical Drives" in response.text
    assert response.history == []
    drive_links = {
        href
        for href in _anchor_hrefs(response.text)
        if re.fullmatch(r"/raid/drives/252/[0-7]", href)
    }
    assert len(drive_links) == 8
    assert "/raid/drives/252/4" in drive_links


def test_drive_list_slot_column_links_to_drive_detail(sample_snapshot: StorcliSnapshot) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives")

    assert response.status_code == 200
    assert '<a href="/drives/252/4">e252:s4</a>' in response.text


def test_drive_detail_returns_404_when_no_snapshot_exists() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/252/4")

    assert response.status_code == 404


def test_drive_detail_returns_404_when_latest_snapshot_lacks_requested_slot(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives/252/99")

    assert response.status_code == 404


def test_drive_detail_renders_attributes_and_chart_area(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives/252/4")
        overview_response = client.get("/")

    assert response.status_code == 200
    assert "Drive 252:4" in response.text
    assert '<span class="mono">WDC WD30EFRX-68EUZN0</span>' in response.text
    assert "WD-WM00000005" in response.text
    assert 'id="chart-area"' in response.text
    assert "/static/vendor/chart.min.js" in response.text
    assert "/static/vendor/chart.min.js" not in overview_response.text


def test_drive_charts_partial_returns_only_chart_area(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives/252/4/charts?range_days=30")

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div id="chart-area"')
    assert "<!doctype html>" not in response.text
    assert "site-header" not in response.text
    assert "chartRetryLimit = 40" in response.text
    assert "syncRangeTabs();" in response.text
    assert 'chartArea.addEventListener("htmx:beforeSwap"' in response.text
    assert "destroyChartsIn(chartArea);" in response.text


def test_drive_charts_range_changes_dataset_labels(sample_snapshot: StorcliSnapshot) -> None:
    older_snapshot = sample_snapshot.model_copy(
        update={"captured_at": sample_snapshot.captured_at.replace(day=10)}
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, older_snapshot)
        _insert_app_snapshot(test_app, sample_snapshot)

        seven_day_response = client.get("/drives/252/4/charts?range_days=7")
        thirty_day_response = client.get("/drives/252/4/charts?range_days=30")

    assert seven_day_response.status_code == 200
    assert thirty_day_response.status_code == 200
    assert "2026-04-10 12:00" not in seven_day_response.text
    assert "2026-04-10 12:00" in thirty_day_response.text


def test_drive_charts_pins_refresh_to_detail_serial_and_captured_at(
    sample_snapshot: StorcliSnapshot,
) -> None:
    old_drive = next(
        drive
        for drive in sample_snapshot.physical_drives
        if drive.enclosure_id == 252 and drive.slot_id == 4
    )
    assert old_drive.temperature_celsius is not None
    replaced_drives = [
        drive.model_copy(
            update={
                "serial_number": "NEW-SLOT-4",
                "temperature_celsius": 99,
                "media_errors": 99,
            }
        )
        if drive.enclosure_id == 252 and drive.slot_id == 4
        else drive
        for drive in sample_snapshot.physical_drives
    ]
    replaced_snapshot = sample_snapshot.model_copy(
        update={
            "captured_at": datetime(2026, 4, 25, 12, 10, tzinfo=UTC),
            "physical_drives": replaced_drives,
        }
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_app_snapshot(test_app, replaced_snapshot)

        response = client.get(
            "/drives/252/4/charts",
            params={
                "range_days": 7,
                "serial_number": old_drive.serial_number,
                "captured_at": sample_snapshot.captured_at.isoformat(),
            },
        )

    scripts = _json_scripts(response.text)
    temperature_payload = scripts["temperature-history-data"]
    assert temperature_payload["labels"] == ["2026-04-25 12:00"]
    assert temperature_payload["datasets"][0]["data"] == [float(old_drive.temperature_celsius)]
    assert temperature_payload["replacementMarkers"] == []


def test_drive_charts_aligns_duplicate_points_when_temperature_is_missing(
    sample_snapshot: StorcliSnapshot,
) -> None:
    slot_drive = next(
        drive
        for drive in sample_snapshot.physical_drives
        if drive.enclosure_id == 252 and drive.slot_id == 4
    )
    missing_temperature_drives = [
        drive.model_copy(update={"temperature_celsius": None, "media_errors": 11})
        if drive.enclosure_id == 252 and drive.slot_id == 4
        else drive
        for drive in sample_snapshot.physical_drives
    ]
    present_temperature_drives = [
        drive.model_copy(update={"temperature_celsius": 45, "media_errors": 22})
        if drive.enclosure_id == 252 and drive.slot_id == 4
        else drive
        for drive in sample_snapshot.physical_drives
    ]
    missing_temperature_snapshot = sample_snapshot.model_copy(
        update={"physical_drives": missing_temperature_drives}
    )
    present_temperature_snapshot = sample_snapshot.model_copy(
        update={"physical_drives": present_temperature_drives}
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, missing_temperature_snapshot)
        _insert_app_snapshot(test_app, present_temperature_snapshot)

        response = client.get(
            "/drives/252/4/charts",
            params={
                "range_days": 7,
                "serial_number": slot_drive.serial_number,
                "captured_at": sample_snapshot.captured_at.isoformat(),
            },
        )

    scripts = _json_scripts(response.text)
    temperature_payload = scripts["temperature-history-data"]
    error_payload = scripts["error-history-data"]
    assert temperature_payload["labels"] == ["2026-04-25 12:00", "2026-04-25 12:00"]
    assert temperature_payload["datasets"][0]["data"] == [None, 45.0]
    assert error_payload["datasets"][0]["data"] == [11, 22]


def test_drive_charts_embed_round_trippable_json_and_threshold_datasets(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives/252/4/charts?range_days=7")

    scripts = _json_scripts(response.text)
    temperature_payload = scripts["temperature-history-data"]
    assert temperature_payload["thresholds"] == {"warning": 55, "critical": 60}
    assert [dataset["label"] for dataset in temperature_payload["thresholdDatasets"]] == [
        "Warning Threshold",
        "Critical Threshold",
    ]
    assert len(temperature_payload["labels"]) == len(temperature_payload["datasets"][0]["data"])
    assert "error-history-data" in scripts


def test_drive_charts_y_axis_includes_high_configured_thresholds(
    monkeypatch: pytest.MonkeyPatch,
    sample_snapshot: StorcliSnapshot,
) -> None:
    monkeypatch.setenv("TEMP_WARNING_CELSIUS", "80")
    monkeypatch.setenv("TEMP_CRITICAL_CELSIUS", "90")
    get_settings.cache_clear()
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives/252/4/charts?range_days=7")

    scripts = _json_scripts(response.text)
    temperature_payload = scripts["temperature-history-data"]
    assert temperature_payload["thresholds"] == {"warning": 80, "critical": 90}
    assert temperature_payload["yMax"] == 95


def test_drive_charts_replacement_markers_use_point_index_for_duplicate_labels(
    sample_snapshot: StorcliSnapshot,
) -> None:
    old_drives = [
        drive.model_copy(update={"serial_number": "OLD-SLOT-4"})
        if drive.enclosure_id == 252 and drive.slot_id == 4
        else drive
        for drive in sample_snapshot.physical_drives
    ]
    old_snapshot = sample_snapshot.model_copy(
        update={
            "captured_at": sample_snapshot.captured_at.replace(hour=10),
            "physical_drives": old_drives,
        }
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, old_snapshot)
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives/252/4/charts?range_days=365")

    scripts = _json_scripts(response.text)
    temperature_payload = scripts["temperature-history-data"]
    labels = temperature_payload["labels"]
    replacement_markers = temperature_payload["replacementMarkers"]
    assert labels == ["2026-04-25", "2026-04-25"]
    assert replacement_markers == [
        {
            "pointIndex": 1,
            "timestamp": "2026-04-25",
            "label": "Drive replaced",
            "previousSerialNumber": "OLD-SLOT-4",
            "currentSerialNumber": "WD-WM00000005",
        }
    ]
    assert "labels.indexOf(marker.timestamp)" not in response.text


def test_drive_charts_preserves_same_bucket_replacement_metrics(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_hourly_metric(
            test_app,
            serial_number="OLD-SLOT-4",
            temperature_avg=41,
            media_errors_max=1,
        )
        _insert_hourly_metric(
            test_app,
            serial_number="WD-WM00000005",
            temperature_avg=45,
            media_errors_max=2,
        )

        response = client.get("/drives/252/4/charts?range_days=365")

    scripts = _json_scripts(response.text)
    temperature_payload = scripts["temperature-history-data"]
    error_payload = scripts["error-history-data"]
    labels = temperature_payload["labels"]
    duplicate_label_indexes = [index for index, label in enumerate(labels) if label == "2025-06-01"]
    assert duplicate_label_indexes == [0, 1]
    assert temperature_payload["datasets"][0]["data"][:2] == [41.0, 45.0]
    assert error_payload["datasets"][0]["data"][:2] == [1, 2]
    assert temperature_payload["replacementMarkers"][0]["pointIndex"] == 1


def test_drive_charts_anchors_replacement_marker_to_surviving_raw_point(
    sample_snapshot: StorcliSnapshot,
) -> None:
    current_snapshot = sample_snapshot.model_copy(
        update={"captured_at": datetime(2026, 4, 25, 12, 10, tzinfo=UTC)}
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, current_snapshot)
        _insert_hourly_metric(
            test_app,
            serial_number="OLD-SLOT-4",
            temperature_avg=41,
            media_errors_max=1,
            bucket_start=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        )
        _insert_hourly_metric(
            test_app,
            serial_number="WD-WM00000005",
            temperature_avg=45,
            media_errors_max=2,
            bucket_start=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        )

        response = client.get("/drives/252/4/charts?range_days=7")

    scripts = _json_scripts(response.text)
    temperature_payload = scripts["temperature-history-data"]
    assert temperature_payload["labels"] == ["2026-04-25 12:00", "2026-04-25 12:10"]
    assert temperature_payload["replacementMarkers"] == [
        {
            "pointIndex": 1,
            "timestamp": "2026-04-25 12:10",
            "label": "Drive replaced",
            "previousSerialNumber": "OLD-SLOT-4",
            "currentSerialNumber": "WD-WM00000005",
        }
    ]


def test_drive_charts_preserves_multiple_replacement_markers_in_same_bucket(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_hourly_metric(
            test_app,
            serial_number="OLD-A-SLOT-4",
            temperature_avg=39,
            media_errors_max=0,
        )
        _insert_hourly_metric(
            test_app,
            serial_number="OLD-B-SLOT-4",
            temperature_avg=41,
            media_errors_max=1,
        )
        _insert_hourly_metric(
            test_app,
            serial_number="WD-WM00000005",
            temperature_avg=45,
            media_errors_max=2,
        )

        response = client.get("/drives/252/4/charts?range_days=365")

    scripts = _json_scripts(response.text)
    temperature_payload = scripts["temperature-history-data"]
    labels = temperature_payload["labels"]
    duplicate_label_indexes = [index for index, label in enumerate(labels) if label == "2025-06-01"]
    assert duplicate_label_indexes == [0, 1, 2]
    assert temperature_payload["replacementMarkers"][:2] == [
        {
            "pointIndex": 1,
            "timestamp": "2025-06-01",
            "label": "Drive replaced",
            "previousSerialNumber": "OLD-A-SLOT-4",
            "currentSerialNumber": "OLD-B-SLOT-4",
        },
        {
            "pointIndex": 2,
            "timestamp": "2025-06-01",
            "label": "Drive replaced",
            "previousSerialNumber": "OLD-B-SLOT-4",
            "currentSerialNumber": "WD-WM00000005",
        },
    ]


def test_drive_detail_prefixes_chart_hx_get_urls(sample_snapshot: StorcliSnapshot) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives/252/4", headers={"X-Forwarded-Prefix": "/raid"})

    assert response.status_code == 200
    assert 'hx-get="/raid/drives/252/4/charts"' in response.text
    assert (
        'hx-vals=\'{"range_days": 7, "serial_number": "WD-WM00000005", '
        '"captured_at": "2026-04-25T12:00:00+00:00"}\''
    ) in response.text


@pytest.mark.parametrize(
    "asset_relative_path",
    [
        Path("static/js/local-time.js"),
        Path("static/vendor/chart.min.js"),
    ],
)
def test_static_asset_version_includes_js_bytes(
    asset_relative_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from megaraid_dashboard.web import routes

    (tmp_path / "static" / "css").mkdir(parents=True)
    (tmp_path / "static" / "js").mkdir()
    (tmp_path / "static" / "vendor").mkdir()
    (tmp_path / "static" / "css" / "app.css").write_text("css", encoding="utf-8")
    (tmp_path / "static" / "js" / "local-time.js").write_text("local-time", encoding="utf-8")
    (tmp_path / "static" / "icons.svg").write_text("icons", encoding="utf-8")
    (tmp_path / "static" / "vendor" / "htmx.min.js").write_text("htmx", encoding="utf-8")
    (tmp_path / "static" / "vendor" / "chart.min.js").write_text("chart", encoding="utf-8")
    changed_path = tmp_path / asset_relative_path
    changed_path.write_text("asset-a", encoding="utf-8")
    monkeypatch.setattr(routes, "_PACKAGE_ROOT", tmp_path)
    monkeypatch.setattr(routes, "STATIC_ASSET_VERSION", "")
    first_version = routes._static_asset_version()

    changed_path.write_text("asset-b", encoding="utf-8")
    monkeypatch.setattr(routes, "STATIC_ASSET_VERSION", "")
    second_version = routes._static_asset_version()

    assert first_version != second_version


def test_events_route_returns_read_only_empty_state() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/events")

    assert response.status_code == 200
    assert "No events recorded yet." in response.text
    assert "Coming soon" not in response.text


def test_health_response_is_unchanged() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        insert_snapshot(session, sample_snapshot)
        session.commit()


def _insert_pending_alert(test_app: FastAPI) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        session.add(
            Event(
                occurred_at=datetime.now(UTC),
                severity="critical",
                category="physical_drive",
                subject="e252:s4",
                summary="Drive state changed",
            )
        )
        session.commit()


def _insert_app_event(
    test_app: FastAPI,
    *,
    occurred_at: datetime,
    category: str,
    summary: str,
    severity: str = "info",
) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        session.add(
            Event(
                occurred_at=occurred_at,
                severity=severity,
                category=category,
                subject="e252:s4",
                summary=summary,
            )
        )
        session.commit()


def _insert_hourly_metric(
    test_app: FastAPI,
    *,
    serial_number: str,
    temperature_avg: float,
    media_errors_max: int,
    bucket_start: datetime | None = None,
) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        session.add(
            PhysicalDriveMetricsHourly(
                bucket_start=bucket_start or datetime(2025, 6, 1, 3, 0, tzinfo=UTC),
                enclosure_id=252,
                slot_id=4,
                serial_number=serial_number,
                temperature_celsius_min=int(temperature_avg),
                temperature_celsius_max=int(temperature_avg),
                temperature_celsius_avg=temperature_avg,
                temperature_sample_count=1,
                media_errors_max=media_errors_max,
                other_errors_max=0,
                predictive_failures_max=0,
                sample_count=1,
            )
        )
        session.commit()


def _anchor_hrefs(html: str) -> set[str]:
    parser = _AnchorParser()
    parser.feed(html)
    return parser.hrefs


def _status_tile_hrefs(html: str) -> set[str]:
    return set(
        re.findall(
            r'<a\s+class="status-tile[^"]*"\s+href="([^"]+)"',
            html,
            flags=re.MULTILINE,
        )
    )


def _json_scripts(html: str) -> dict[str, dict[str, object]]:
    parser = _JsonScriptParser()
    parser.feed(html)
    return {
        script_id: cast(dict[str, object], json.loads(payload))
        for script_id, payload in parser.scripts.items()
    }


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        href = attributes.get("href")
        if href is not None:
            self.hrefs.add(href)


class _JsonScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: dict[str, str] = {}
        self._active_script_id: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        if attributes.get("type") != "application/json":
            return
        script_id = attributes.get("id")
        if script_id is None:
            return
        self._active_script_id = script_id
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._active_script_id is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or self._active_script_id is None:
            return
        self.scripts[self._active_script_id] = "".join(self._chunks)
        self._active_script_id = None
        self._chunks = []
