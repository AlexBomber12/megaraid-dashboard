from __future__ import annotations

from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

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
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_drives_page_renders_mobile_data_labels(sample_snapshot: StorcliSnapshot) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives")

    assert response.status_code == 200
    for label in (
        "Slot",
        "Model",
        "Serial",
        "State",
        "Temp",
        "Size",
        "Media Err",
        "Other Err",
        "Predictive",
        "SMART",
    ):
        assert f'data-label="{label}"' in response.text


def test_header_nav_is_wrapped_in_details() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert '<details class="site-nav-details">' in response.text
    assert 'class="site-nav-toggle"' in response.text
    assert '<nav class="site-nav" aria-label="Primary navigation">' in response.text


def test_mobile_layout_does_not_require_new_javascript() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/")

    parser = _ScriptSourceParser()
    parser.feed(response.text)

    assert response.status_code == 200
    assert all("mobile" not in source for source in parser.sources)


def test_mobile_table_keeps_sort_headers_visible() -> None:
    stylesheet = Path("src/megaraid_dashboard/static/css/app.css").read_text()

    mobile_rules = stylesheet.split("@media (max-width: 720px)", maxsplit=1)[1]

    assert ".data-table thead {\n    display: block;" in mobile_rules
    assert ".data-table th[data-sort-key]" in mobile_rules
    assert ".data-table tbody tr {\n    display: block;" in mobile_rules
    assert ".data-table thead {\n    display: none;" not in mobile_rules


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        insert_snapshot(session, sample_snapshot)
        session.commit()


class _ScriptSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        source = attributes.get("src")
        if source is not None:
            self.sources.append(source)
