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


class _ReplaceWizardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.wizard_roots: list[dict[str, str]] = []
        self.buttons: list[dict[str, str]] = []
        self.inputs: list[dict[str, str]] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        if tag == "section" and "data-replace-wizard" in attr_map:
            self.wizard_roots.append(attr_map)
        if tag == "button" and "data-replace-action" in attr_map:
            self.buttons.append(attr_map)
        if tag == "input" and "data-replace-input" in attr_map:
            self.inputs.append(attr_map)
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


def test_drive_detail_renders_replace_wizard_with_metadata(
    sample_snapshot: StorcliSnapshot,
) -> None:
    response = _drive_detail_response(sample_snapshot)
    parsed = _parse_replace_wizard(response.text)

    assert response.status_code == 200
    assert parsed.wizard_roots == [
        {
            "class": "replace-wizard",
            "aria-label": "Replace drive",
            "data-replace-wizard": "",
            "data-enclosure": "252",
            "data-slot": "4",
            "data-serial": "WD-WM00000005",
            "data-replace-offline-url": "/drives/252:4/replace/offline",
            "data-replace-missing-url": "/drives/252:4/replace/missing",
            "data-replace-topology-url": "/drives/252:4/replace/topology",
            "data-replace-insert-url": "/drives/252:4/replace/insert",
            "data-replace-rebuild-status-url": "/drives/252:4/replace/rebuild-status",
        }
    ]
    assert "Replace drive" in response.text
    assert "252:4" in response.text
    assert "WDC WD30EFRX-68EUZN0" in response.text
    assert "WD-WM00000005" in response.text


def test_drive_detail_renders_replace_serial_controls(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse_replace_wizard(_drive_detail_response(sample_snapshot).text)

    assert _input_by_name(parsed, "serial")["type"] == "text"
    dry_run = _input_by_name(parsed, "dry-run")
    assert dry_run["type"] == "checkbox"
    assert "checked" in dry_run
    run_button = _button_by_action(parsed, "run-step1")
    assert run_button["class"] == "button button--warning"
    assert "disabled" in run_button


def test_drive_detail_replace_urls_include_forwarded_prefix(
    sample_snapshot: StorcliSnapshot,
) -> None:
    response = _drive_detail_response(sample_snapshot, headers={"X-Forwarded-Prefix": "/raid"})
    parsed = _parse_replace_wizard(response.text)

    assert parsed.wizard_roots[0]["data-replace-offline-url"] == (
        "/raid/drives/252:4/replace/offline"
    )
    assert parsed.wizard_roots[0]["data-replace-missing-url"] == (
        "/raid/drives/252:4/replace/missing"
    )
    assert parsed.wizard_roots[0]["data-replace-topology-url"] == (
        "/raid/drives/252:4/replace/topology"
    )
    assert parsed.wizard_roots[0]["data-replace-insert-url"] == (
        "/raid/drives/252:4/replace/insert"
    )
    assert parsed.wizard_roots[0]["data-replace-rebuild-status-url"] == (
        "/raid/drives/252:4/replace/rebuild-status"
    )


def test_drive_detail_replace_cancel_buttons_are_default_focus(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse_replace_wizard(_drive_detail_response(sample_snapshot).text)
    cancel_buttons = [
        button for button in parsed.buttons if button["data-replace-action"] == "cancel"
    ]

    assert len(cancel_buttons) == 4
    assert all("autofocus" in button for button in cancel_buttons)


def test_drive_detail_references_replace_wizard_script(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse_replace_wizard(_drive_detail_response(sample_snapshot).text)

    assert any(src.startswith("/static/js/replace-wizard.js?v=") for src in parsed.scripts)


def test_drive_detail_renders_step3_insert_controls(
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse_replace_wizard(_drive_detail_response(sample_snapshot).text)

    new_serial = _input_by_name(parsed, "new-serial")
    assert new_serial["type"] == "text"
    dry_run_step3 = _input_by_name(parsed, "dry-run-step3")
    assert dry_run_step3["type"] == "checkbox"
    assert "checked" in dry_run_step3
    run_step3 = _button_by_action(parsed, "run-step3")
    assert run_step3["class"] == "button button--warning"
    assert "disabled" in run_step3
    continue_to_insert = _button_by_action(parsed, "continue-to-insert")
    assert continue_to_insert["class"] == "button button--warning"


def test_replace_wizard_js_validates_serial_and_posts_steps_in_order() -> None:
    source = Path("src/megaraid_dashboard/static/js/replace-wizard.js").read_text(encoding="utf-8")

    assert "serialInput.value.trim() !== expectedSerial.trim()" in source
    assert "dry_run: dryRunInput.checked" in source
    assert "const offlineUrl = root.dataset.replaceOfflineUrl;" in source
    assert "const missingUrl = root.dataset.replaceMissingUrl;" in source
    assert "dryRunInput.checked = true;" in source
    assert "if (!offline.ok) return;" in source
    assert "let inFlight = false;" in source
    assert "runButton.disabled = inFlight ||" in source
    assert "if (inFlight) return;" in source
    assert "setInFlight(true);" in source
    assert "setInFlight(false);" in source
    assert "try {" in source
    assert "} finally {" in source
    assert 'appendRequestError("replace request failed", error);' in source
    assert '"X-CSRF-Token": getCookie("__Host-csrf") || ""' in source


def test_replace_wizard_js_step3_posts_insert_without_client_topology() -> None:
    source = Path("src/megaraid_dashboard/static/js/replace-wizard.js").read_text(encoding="utf-8")

    assert "const topologyUrl = root.dataset.replaceTopologyUrl;" in source
    assert "const insertUrl = root.dataset.replaceInsertUrl;" in source
    assert "newSerialInput.value.trim() !== expectedSerial.trim()" in source
    assert 'show("physical-swap");' in source
    assert "await loadTopology();" in source
    assert "if (topology === null) return;" in source
    assert "dry_run: dryRunStep3Input.checked," in source
    # Topology is server-derived; the client must NOT send dg/array/row in
    # the insert request body (otherwise a crafted request could overwrite
    # the server's view of the topology).
    assert "dg: topology.dg," not in source
    assert "array: topology.array," not in source
    assert "row: topology.row," not in source
    assert "postJson(insertUrl, body)" in source


def test_replace_wizard_js_starts_rebuild_polling_after_insert_success() -> None:
    source = Path("src/megaraid_dashboard/static/js/replace-wizard.js").read_text(encoding="utf-8")

    assert "const rebuildStatusUrl = root.dataset.replaceRebuildStatusUrl;" in source
    assert 'show("rebuild");' in source
    assert "startRebuildPolling();" in source
    assert 'rebuildProgress.setAttribute("hx-trigger", "load, every 30s");' in source
    assert 'window.htmx.trigger(rebuildProgress, "htmx:abort");' in source
    assert "rebuildProgress.replaceWith(nextRebuildProgress);" in source


def _drive_detail_response(
    sample_snapshot: StorcliSnapshot,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        return client.get("/drives/252/4", headers=headers)


def _parse_replace_wizard(html: str) -> _ReplaceWizardParser:
    parser = _ReplaceWizardParser()
    parser.feed(html)
    return parser


def _button_by_action(parsed: _ReplaceWizardParser, action: str) -> dict[str, str]:
    matches = [button for button in parsed.buttons if button["data-replace-action"] == action]
    assert len(matches) == 1
    return matches[0]


def _input_by_name(parsed: _ReplaceWizardParser, name: str) -> dict[str, str]:
    matches = [input_ for input_ in parsed.inputs if input_["data-replace-input"] == name]
    assert len(matches) == 1
    return matches[0]


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = test_app.state.session_factory
    with session_factory() as session:
        assert isinstance(session_factory, sessionmaker)
        assert isinstance(session, Session)
        insert_snapshot(session, sample_snapshot)
        session.commit()
