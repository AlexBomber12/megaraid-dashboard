from __future__ import annotations

from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
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


def test_overview_renders_new_header_with_all_nav_links() -> None:
    response = _get_overview()

    assert response.status_code == 200
    assert '<header class="site-header-v2">' in response.text
    for label in ("Overview", "Controller", "Drives", "Events", "Audit"):
        assert f">{label}</a>" in response.text


def test_overview_nav_link_is_marked_active() -> None:
    response = _get_overview()
    parser = _LinkParser()
    parser.feed(response.text)

    overview_link = parser.links["Overview"]

    assert "site-nav-v2__link--active" in overview_link["class"]
    assert overview_link["aria-current"] == "page"


def test_controller_nav_link_points_to_controller_detail() -> None:
    response = _get_overview()
    parser = _LinkParser()
    parser.feed(response.text)

    controller_link = parser.links["Controller"]

    assert controller_link["href"] == "/controller"


def test_audit_nav_link_is_marked_active_after_redirect() -> None:
    response = _get_path("/audit")
    parser = _LinkParser()
    parser.feed(response.text)

    audit_link = parser.links["Audit"]
    events_link = parser.links["Events"]

    assert "site-nav-v2__link--active" in audit_link["class"]
    assert audit_link["aria-current"] == "page"
    assert "site-nav-v2__link--active" not in events_link["class"]
    assert "aria-current" not in events_link


def test_nav_active_marker_text_does_not_render_before_header() -> None:
    response = _get_overview()
    body_start = response.text.index("<body>")
    header_start = response.text.index('<header class="site-header-v2">')
    before_header = response.text[body_start:header_start]

    assert "overview" not in before_header


def test_refresh_indicator_dot_is_green() -> None:
    stylesheet = Path("src/megaraid_dashboard/static/css/app.css").read_text(encoding="utf-8")

    assert 'class="refresh-dot" aria-hidden="true"' in _get_overview().text
    assert ".refresh-dot {" in stylesheet
    assert "background: var(--color-optimal);" in stylesheet


def test_utc_clock_element_is_present() -> None:
    response = _get_overview()

    assert 'class="utc-clock"' in response.text
    assert "data-local-time-clock" in response.text


def test_header_uses_sticky_css_class() -> None:
    stylesheet = Path("src/megaraid_dashboard/static/css/app.css").read_text(encoding="utf-8")

    assert '<header class="site-header-v2">' in _get_overview().text
    assert ".site-header-v2 {" in stylesheet
    assert "position: sticky;" in stylesheet


def test_header_nav_has_v2_mobile_overflow_handling() -> None:
    stylesheet = Path("src/megaraid_dashboard/static/css/app.css").read_text(encoding="utf-8")

    assert ".site-nav-v2 {" in stylesheet
    assert "overflow-x: auto;" in stylesheet
    assert "-webkit-overflow-scrolling: touch;" in stylesheet
    assert "@media (max-width: 560px)" in stylesheet
    assert "flex-wrap: wrap;" in stylesheet


def _get_overview() -> object:
    return _get_path("/")


def _get_path(path: str) -> object:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        return client.get(path)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: dict[str, dict[str, str]] = {}
        self._active_attrs: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = {name: value or "" for name, value in attrs}
        if "site-nav-v2__link" in attributes.get("class", ""):
            self._active_attrs = attributes

    def handle_data(self, data: str) -> None:
        if self._active_attrs is None:
            return
        label = data.strip()
        if label:
            self.links[label] = self._active_attrs
            self._active_attrs = None
