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


class _DriveDetailV2Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.buttons: list[dict[str, str]] = []
        self.sections: list[str] = []
        self.current_data_section: str | None = None
        self.current_class: str = ""
        self.id_fields = 0
        self.conn_fields = 0
        self.position_slots = 0
        self.highlighted_position_slots = 0
        self.chart_canvas_v2 = False
        self.sparkline_current_count: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        if tag == "a":
            self.links.append(attr_map)
        if tag == "button":
            self.buttons.append(attr_map)
        if "data-drive-detail-section" in attr_map:
            self.sections.append(attr_map["data-drive-detail-section"])
            self.current_data_section = attr_map["data-drive-detail-section"]
        if tag == "dl":
            self.current_class = attr_map.get("class", "")
        if tag == "div" and "id-grid" in self.current_class:
            self.id_fields += 1
        if tag == "div" and "conn-row" in self.current_class:
            self.conn_fields += 1
        if tag == "a" and "position-slot" in attr_map.get("class", ""):
            self.position_slots += 1
            if "this" in attr_map["class"]:
                self.highlighted_position_slots += 1
        if tag == "canvas" and "chart-canvas-v2" in attr_map.get("class", ""):
            self.chart_canvas_v2 = True
        if tag == "svg" and attr_map.get("class") == "error-sparkline":
            self.sparkline_current_count = attr_map.get("data-current-count")

    def handle_endtag(self, tag: str) -> None:
        if tag == "section":
            self.current_data_section = None
        if tag == "dl":
            self.current_class = ""


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


def test_drive_detail_v2_renders_page_shell(sample_snapshot: StorcliSnapshot) -> None:
    response = _drive_detail_response(sample_snapshot, slot_id=0)

    assert response.status_code == 200
    assert "drive-detail-v2" in response.text
    assert "Overview" in response.text
    assert 'href="/drives"' in response.text
    assert 'href="/drives"' in response.text
    assert "Slot 252:0" in response.text
    assert "WDC WD30EFRX-68EUZN0" in response.text
    assert "WD-WM00000001" in response.text


def test_drive_detail_v2_prev_next_nav_states(sample_snapshot: StorcliSnapshot) -> None:
    first = _parse(_drive_detail_response(sample_snapshot, slot_id=0).text)
    middle = _parse(_drive_detail_response(sample_snapshot, slot_id=4).text)
    last = _parse(_drive_detail_response(sample_snapshot, slot_id=7).text)

    assert _nav_labels(first) == ["Next drive"]
    assert _nav_labels(middle) == ["Previous drive", "Next drive"]
    assert _nav_labels(last) == ["Previous drive"]


def test_drive_detail_v2_health_snapshot_actions_and_sparkline(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse(_drive_detail_response(sample_snapshot, slot_id=4).text)

    assert "health-snapshot" in parsed.sections
    assert _button_by_text_attrs(parsed, "start")["hx-post"] == "/drives/252:4/locate/start"
    assert _button_by_text_attrs(parsed, "stop")["hx-post"] == "/drives/252:4/locate/stop"
    assert parsed.sparkline_current_count == "0"


def test_drive_detail_v2_position_and_chart(sample_snapshot: StorcliSnapshot) -> None:
    parsed = _parse(_drive_detail_response(sample_snapshot, slot_id=4).text)

    assert parsed.position_slots == 8
    assert parsed.highlighted_position_slots == 1
    assert parsed.chart_canvas_v2


def test_drive_detail_v2_identity_connection_and_action_counts(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse(_drive_detail_response(sample_snapshot, slot_id=4).text)
    advanced_buttons = [
        button
        for button in parsed.buttons
        if button.get("type") == "submit" and button.get("class", "").startswith("button")
    ]

    assert parsed.id_fields == 9
    assert parsed.conn_fields == 8
    assert len(advanced_buttons) == 4
    assert "Mark as UBad" in _button_texts(_drive_detail_response(sample_snapshot, slot_id=4).text)


def test_drive_detail_v2_replace_card_and_disabled_ubad(
    sample_snapshot: StorcliSnapshot,
) -> None:
    html = _drive_detail_response(sample_snapshot, slot_id=4).text
    parsed = _parse(html)
    mark_ubad = _submit_button_by_label(html, parsed, "Mark as UBad")

    assert "destructive-card" in html
    assert "Begin Replacement" in html
    assert "disabled" in mark_ubad
    assert mark_ubad["title"] == "Only UGood drives can be marked UBad."


def test_drive_charts_v2_compat_returns_404_without_snapshot() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/252/0/charts")

    assert response.status_code == 404


def test_drive_charts_v2_compat_returns_404_for_missing_drive(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        response = client.get("/drives/252/99/charts")

    assert response.status_code == 404


def _drive_detail_response(sample_snapshot: StorcliSnapshot, *, slot_id: int) -> Any:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        return client.get(f"/drives/252/{slot_id}")


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = test_app.state.session_factory
    with session_factory() as session:
        assert isinstance(session_factory, sessionmaker)
        assert isinstance(session, Session)
        insert_snapshot(session, sample_snapshot)
        session.commit()


def _parse(html: str) -> _DriveDetailV2Parser:
    parser = _DriveDetailV2Parser()
    parser.feed(html)
    return parser


def _nav_labels(parsed: _DriveDetailV2Parser) -> list[str]:
    return [link["aria-label"] for link in parsed.links if link.get("class") == "drive-nav__link"]


def _button_by_text_attrs(parsed: _DriveDetailV2Parser, action: str) -> dict[str, str]:
    matches = [button for button in parsed.buttons if button.get("data-locate-action") == action]
    assert len(matches) == 1
    return matches[0]


def _button_texts(html: str) -> list[str]:
    class TextParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.in_button = False
            self.texts: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "button":
                self.in_button = True
                self.texts.append("")

        def handle_data(self, data: str) -> None:
            if self.in_button:
                self.texts[-1] += data.strip()

        def handle_endtag(self, tag: str) -> None:
            if tag == "button":
                self.in_button = False

    parser = TextParser()
    parser.feed(html)
    return parser.texts


def _submit_button_by_label(
    html: str,
    parsed: _DriveDetailV2Parser,
    label: str,
) -> dict[str, str]:
    button_texts = _button_texts(html)
    for index, text in enumerate(button_texts):
        if text == label:
            return parsed.buttons[index]
    raise AssertionError(f"button not found: {label}")
