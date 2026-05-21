from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import ControllerSnapshot
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
    monkeypatch.setenv("MAINTENANCE_MODE", "true")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_controller_detail_page_renders_expected_sections() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_controller_snapshot(test_app, alarm_state="On")

        response = client.get("/controller")

    assert response.status_code == 200
    html = response.text
    sections = _controller_sections(html)
    assert {
        "health-snapshot",
        "live-operations",
        "cachevault",
        "roc-history",
        "raid-config",
        "scheduled-tasks",
        "hardware-identity",
        "buzzer-control",
        "foreign-config",
    }.issubset(sections)
    assert ">Overview</a>" in html
    assert "<span>Controller</span>" in html
    assert "MegaRAID SAS 9270CV-8i" in html
    assert "SN SV00000001." in html
    assert 'class="chart-canvas-v2"' in html
    assert 'data-range-hours="1"' in html
    assert 'data-range-hours="24" role="tab" aria-selected="true"' in html
    assert 'data-range-hours="168"' in html
    assert 'data-range-hours="720"' in html
    assert "Patrol Read" in html
    assert html.count("<dt>") >= 18
    assert 'data-foreign-config-state="absent"' in html
    assert "No foreign configuration detected." in html


def test_buzzer_buttons_reflect_current_alarm_state() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_controller_snapshot(test_app, alarm_state="Off")

        response = client.get("/controller")

    buttons = _buttons_by_buzzer_action(response.text)
    assert set(buttons) == {"silence", "disable", "enable"}
    assert buttons["silence"].disabled is True
    assert buttons["disable"].disabled is True
    assert buttons["enable"].disabled is False
    assert buttons["silence"].label == "Silence"
    assert buttons["disable"].label == "Disable"
    assert buttons["enable"].label == "Enable"


def test_foreign_config_present_state_renders_clear_action() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_controller_snapshot(
            test_app,
            alarm_state="On",
            foreign_config={
                "present": True,
                "drive_count": 2,
                "source_controller_serial": "SRC123",
            },
        )

        response = client.get("/controller")

    assert response.status_code == 200
    assert 'data-foreign-config-state="present"' in response.text
    assert "Foreign configuration detected on 2 drive(s)." in response.text
    assert "Source controller SN SRC123" in response.text
    assert 'action="/controller/foreign-config/clear"' in response.text
    assert "Clear foreign config?" in response.text


def test_buzzer_silence_form_posts_with_csrf(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        assert use_sudo is False
        assert binary_path == "/usr/local/sbin/storcli64"
        calls.append(args)
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_controller_snapshot(test_app, alarm_state="On")
        page = client.get("/controller")
        action = _form_action_by_buzzer_action(page.text, "silence")
        headers = _csrf_request_headers(client, csrf_headers, path="/controller")

        response = client.post(action, headers=headers, follow_redirects=False)

    assert action == "/controller/buzzer/silence"
    assert response.status_code == 303
    assert calls == [["/c0", "set", "alarm=silence"]]


def _insert_controller_snapshot(
    test_app: FastAPI,
    *,
    alarm_state: str,
    foreign_config: dict[str, Any] | None = None,
) -> None:
    raw_json: dict[str, Any] = {
        "Revision No": "03",
        "ChipRevision": "B0",
        "Mfg Date": "04/25/2020",
        "Rework Date": "N/A",
        "SAS Address": "500605b00abc1234",
        "PCI Address": "00:03:00:00",
        "Backend Port Count": "8",
        "NVRAM Size": "32 KB",
        "Flash Size": "16 MB",
        "On Board Memory Size": "1024 MB",
        "Current Size of FW Cache (MB)": "1024",
        "Alarm": alarm_state,
        "Patrol Read Reoccurrence": "168 hours",
        "foreign_config": foreign_config or {"present": False, "drive_count": 0},
    }
    with test_app.state.session_factory() as session:
        session.add(
            ControllerSnapshot(
                captured_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
                model_name="MegaRAID SAS 9270CV-8i",
                serial_number="SV00000001",
                firmware_version="23.34.0-0019",
                bios_version="6.36.00.3_4.19.08.00_0x06180200",
                driver_version="07.727.03.00",
                alarm_state=alarm_state,
                cv_present=False,
                bbu_present=False,
                roc_temperature_celsius=70,
                raw_json=raw_json,
            )
        )
        session.commit()


def _controller_sections(html: str) -> set[str]:
    parser = _SectionParser()
    parser.feed(html)
    return parser.sections


def _buttons_by_buzzer_action(html: str) -> dict[str, _Button]:
    parser = _BuzzerButtonParser()
    parser.feed(html)
    return parser.buttons


def _form_action_by_buzzer_action(html: str, action: str) -> str:
    parser = _BuzzerButtonParser()
    parser.feed(html)
    return parser.form_actions[action]


def _csrf_request_headers(
    client: TestClient,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    *,
    path: str,
) -> dict[str, str]:
    headers = csrf_headers(client, path=path)
    token = headers["X-CSRF-Token"]
    return {**headers, "Cookie": f"__Host-csrf={token}"}


class _Button:
    def __init__(self, *, label: str, disabled: bool) -> None:
        self.label = label
        self.disabled = disabled


class _SectionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "section":
            return
        for key, value in attrs:
            if key == "data-controller-section" and value is not None:
                self.sections.add(value)


class _BuzzerButtonParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_action: str | None = None
        self.current_button_action: str | None = None
        self.current_button_disabled = False
        self.current_button_label: list[str] = []
        self.buttons: dict[str, _Button] = {}
        self.form_actions: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and attributes.get("data-buzzer-action") is not None:
            self.current_action = attributes["data-buzzer-action"]
            self.form_actions[self.current_action] = attributes.get("action", "")
        if tag == "button" and self.current_action is not None:
            self.current_button_action = self.current_action
            self.current_button_disabled = "disabled" in attributes
            self.current_button_label = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self.current_button_action is not None:
            self.buttons[self.current_button_action] = _Button(
                label="".join(self.current_button_label).strip(),
                disabled=self.current_button_disabled,
            )
            self.current_button_action = None
        if tag == "form":
            self.current_action = None

    def handle_data(self, data: str) -> None:
        if self.current_button_action is not None:
            self.current_button_label.append(data)
