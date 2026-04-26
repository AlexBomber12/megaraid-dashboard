from __future__ import annotations

from collections.abc import Iterator
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
from megaraid_dashboard.storcli import StorcliSnapshot
from megaraid_dashboard.web.middleware import ForwardedPrefixMiddleware


@pytest.fixture(autouse=True)
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-bcrypt-hash")
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


def test_overview_navigation_and_assets_are_prefix_aware(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/", headers={"X-Forwarded-Prefix": "/raid"})

    assert response.status_code == 200
    assert "SERVER RAID Status" in response.text
    assert "/raid/static/css/app.css" in response.text
    assert "/raid/static/vendor/htmx.min.js" in response.text
    assert "/raid/partials/overview" in response.text
    assert {"/raid/", "/raid/drives", "/raid/events"}.issubset(_anchor_hrefs(response.text))


def test_overview_navigation_is_prefix_free_without_forwarded_prefix(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/")

    assert response.status_code == 200
    assert "/static/css/app.css" in response.text
    assert "/static/vendor/htmx.min.js" in response.text
    assert "/partials/overview" in response.text
    assert {"/", "/drives", "/events"}.issubset(_anchor_hrefs(response.text))
    assert "/raid/" not in response.text


def test_empty_database_renders_empty_state_on_full_page_and_partial() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        full_response = client.get("/")
        partial_response = client.get("/partials/overview")

    assert full_response.status_code == 200
    assert "Waiting for first metrics collection" in full_response.text
    assert "The collector has not yet completed its first run." in full_response.text
    assert "Next run within 300 seconds." in full_response.text
    assert "Waiting for first metrics collection" in partial_response.text
    assert "<!doctype html>" not in partial_response.text
    assert "site-header" not in partial_response.text


def test_partial_endpoint_returns_data_block_fragment(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/partials/overview")

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div\n  id="data-block"')
    assert "<!doctype html>" not in response.text
    assert "site-header" not in response.text
    assert "SERVER RAID Status" in response.text


def test_data_block_has_auto_refresh_attributes() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/")

    assert 'id="data-block"' in response.text
    assert 'hx-get="/partials/overview"' in response.text
    assert 'hx-trigger="every 30s"' in response.text
    assert 'hx-target="this"' in response.text
    assert 'hx-swap="outerHTML"' in response.text


def test_vendored_htmx_exists_and_is_referenced() -> None:
    assert Path("src/megaraid_dashboard/static/vendor/htmx.min.js").exists()

    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/")

    assert "/static/vendor/htmx.min.js" in response.text


def test_static_assets_are_served_with_far_future_cache_header() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert "public" in response.headers["Cache-Control"]
    assert "max-age=31536000" in response.headers["Cache-Control"]
    assert "immutable" in response.headers["Cache-Control"]


def test_drives_placeholder_redirects_to_overview_with_content(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives", follow_redirects=True)

    assert response.status_code == 200
    assert "SERVER RAID Status" in response.text


def test_events_placeholder_returns_coming_soon() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/events")

    assert response.status_code == 200
    assert "Coming soon" in response.text


def test_health_response_is_unchanged() -> None:
    test_app = create_app()
    with TestClient(test_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        insert_snapshot(session, sample_snapshot)
        session.commit()


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
        attributes = dict(attrs)
        href = attributes.get("href")
        if href is not None:
            self.hrefs.add(href)
