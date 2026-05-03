from __future__ import annotations

from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


class _DriveActionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.buttons: list[dict[str, str]] = []
        self.feedback_spans: list[dict[str, str]] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        if tag == "button" and "data-locate-action" in attr_map:
            self.buttons.append(attr_map)
        if tag == "span" and "data-action-feedback" in attr_map:
            self.feedback_spans.append(attr_map)
        if tag == "script" and "src" in attr_map:
            self.scripts.append(attr_map["src"])


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


def test_drive_detail_renders_locate_buttons_with_post_urls(
    sample_snapshot: StorcliSnapshot,
) -> None:
    response = _drive_detail_response(sample_snapshot)
    parsed = _parse_drive_actions(response.text)

    assert response.status_code == 200
    assert _button_by_action(parsed, "start")["hx-post"] == "/drives/252:4/locate/start"
    assert _button_by_action(parsed, "stop")["hx-post"] == "/drives/252:4/locate/stop"


def test_drive_detail_locate_buttons_post_to_start_and_stop_endpoints(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse_drive_actions(_drive_detail_response(sample_snapshot).text)

    assert _button_by_action(parsed, "start")["hx-post"].endswith("/locate/start")
    assert _button_by_action(parsed, "stop")["hx-post"].endswith("/locate/stop")


def test_drive_detail_locate_feedback_is_announced(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse_drive_actions(_drive_detail_response(sample_snapshot).text)

    assert parsed.feedback_spans == [
        {
            "class": "drive-actions__feedback",
            "data-action-feedback": "",
            "aria-live": "polite",
        }
    ]


def test_drive_detail_references_drive_actions_script(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse_drive_actions(_drive_detail_response(sample_snapshot).text)

    assert any(src.startswith("/static/js/drive-actions.js?v=") for src in parsed.scripts)


def test_drive_detail_locate_buttons_use_htmx_csrf_shim(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse_drive_actions(_drive_detail_response(sample_snapshot).text)
    start = _button_by_action(parsed, "start")
    stop = _button_by_action(parsed, "stop")

    assert any(src.startswith("/static/js/csrf.js?v=") for src in parsed.scripts)
    for button in (start, stop):
        assert button["hx-swap"] == "none"
        assert button["hx-target"] == "this"
        assert "aria-busy" in button["hx-on::before-request"]
        assert "window.driveActions.flash(this, event.detail)" in button["hx-on::after-request"]


def _drive_detail_response(sample_snapshot: StorcliSnapshot) -> Any:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        return client.get("/drives/252/4")


def _parse_drive_actions(html: str) -> _DriveActionParser:
    parser = _DriveActionParser()
    parser.feed(html)
    return parser


def _button_by_action(parsed: _DriveActionParser, action: str) -> dict[str, str]:
    matches = [button for button in parsed.buttons if button["data-locate-action"] == action]
    assert len(matches) == 1
    return matches[0]


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = test_app.state.session_factory
    with session_factory() as session:
        assert isinstance(session_factory, sessionmaker)
        assert isinstance(session, Session)
        insert_snapshot(session, sample_snapshot)
        session.commit()
