from __future__ import annotations

from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
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


def test_main_page_refresh_region_has_htmx_polling_attributes(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        with test_app.state.session_factory() as session:
            insert_snapshot(session, sample_snapshot)
            session.commit()

        response = client.get("/")

    refresh_region = _find_refresh_region(response.text)
    assert response.status_code == 200
    assert refresh_region is not None
    assert refresh_region["tag"] == "section"
    assert refresh_region["hx-get"] == "/partials/main-page"
    assert refresh_region["hx-trigger"] == "every 30s"
    assert refresh_region["hx-swap"] == "innerHTML"
    assert response.text.count("<main") == 1


def _find_refresh_region(html: str) -> dict[str, str] | None:
    parser = _RefreshRegionParser()
    parser.feed(html)
    return parser.refresh_region


class _RefreshRegionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refresh_region: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "section":
            return
        attr_map = {key: value or "" for key, value in attrs}
        if attr_map.get("class") == "main-page-v2__refresh":
            self.refresh_region = {"tag": tag, **attr_map}
