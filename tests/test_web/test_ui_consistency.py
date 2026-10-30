from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER

PACKAGE_ROOT = Path("src/megaraid_dashboard")
TEMPLATE_DIR = PACKAGE_ROOT / "templates"
STYLESHEET = PACKAGE_ROOT / "static" / "css" / "app.css"
FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "storcli" / "redacted"
FALL_PRESENT_FIXTURE = FIXTURE_DIR / "c0_fall_show_all_present.json"

# Classes that only act as JS/test hooks or state markers and carry no styling.
_UNSTYLED_HOOK_CLASSES = {
    "drives",
    "events-timeline__item",
    "foreign-config__form",
    "idle",
    "maintenance-start",
    "timeline",
}

_BASE_LIVE_REGIONS = {"maintenance-slot", "site-status"}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.classes: set[str] = set()
        self.live_region_ids: list[str] = []
        self.advanced_action_forms: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        self.classes.update(attr_map.get("class", "").split())
        if "data-live-region" in attr_map and attr_map.get("id"):
            self.live_region_ids.append(attr_map["id"])
        if tag == "form" and "data-drive-advanced-action" in attr_map:
            self.advanced_action_forms.append(attr_map)


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


@pytest.fixture
def fake_foreign_config(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.loads(FALL_PRESENT_FIXTURE.read_text(encoding="utf-8"))

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        return dict(payload)

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)


@pytest.mark.parametrize(
    ("path", "page_regions"),
    [
        ("/", {"overview-live"}),
        (
            "/controller",
            {
                "controller-live-status",
                "controller-live-ops",
                "controller-live-details",
                "controller-live-identity",
                "controller-live-actions",
                "maintenance",
            },
        ),
        ("/drives", {"drives-live"}),
        (
            "/drives/252/4",
            {"drive-live-status", "drive-live-details", "drive-live-actions", "maintenance"},
        ),
    ],
)
def test_monitoring_pages_render_identified_live_regions(
    path: str,
    page_regions: set[str],
    sample_snapshot: StorcliSnapshot,
) -> None:
    parsed = _parse(_get(sample_snapshot, path).text)

    assert len(parsed.live_region_ids) == len(set(parsed.live_region_ids))
    assert set(parsed.live_region_ids) == _BASE_LIVE_REGIONS | page_regions


@pytest.mark.parametrize("path", ["/", "/controller", "/drives", "/drives/252/4", "/events"])
def test_pages_render_system_health_status_bar(
    path: str,
    sample_snapshot: StorcliSnapshot,
) -> None:
    response = _get(sample_snapshot, path)

    assert response.status_code == 200
    assert '<div class="status-bar-v2">' in response.text
    assert "Version <strong>" in response.text


@pytest.mark.usefixtures("fake_foreign_config")
def test_foreign_config_page_renders_status_bar_without_live_refresh(
    sample_snapshot: StorcliSnapshot,
) -> None:
    response = _get(
        sample_snapshot,
        "/controller/foreign-config",
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert '<div class="status-bar-v2">' in response.text
    assert '<body data-live-refresh="off">' in response.text


def test_destructive_advanced_actions_require_typed_serial_phrase(
    sample_snapshot: StorcliSnapshot,
) -> None:
    forms = _parse(_get(sample_snapshot, "/drives/252/4").text).advanced_action_forms
    destructive = [form for form in forms if form["data-confirm-tone"] == "danger"]
    safe = [form for form in forms if form["data-confirm-tone"] == "warning"]

    assert destructive
    assert safe
    assert len(destructive) + len(safe) == len(forms)
    for form in destructive:
        assert form["data-confirm-phrase"] == "WD-WM00000005"
        assert form["data-confirm-title"].endswith("on drive 252:4?")
    for form in safe:
        assert "data-confirm-phrase" not in form
        assert form["data-confirm-title"].endswith("on drive 252:4?")


def test_events_partial_keeps_events_title_for_operator_actions(
    sample_snapshot: StorcliSnapshot,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_operator_event(test_app)
        audit_path = client.get("/audit", follow_redirects=False).headers["location"]
        page = client.get(audit_path)
        partial = client.get(audit_path.replace("/events", "/partials/events", 1))
        events_partial = client.get("/partials/events")

    assert "<title>Events - MegaRAID Dashboard</title>" in page.text
    assert "<h1>Events</h1>" in page.text
    assert "<h1>Events</h1>" in partial.text
    assert "<h1>Events</h1>" in events_partial.text


@pytest.mark.usefixtures("fake_foreign_config")
def test_rendered_pages_use_only_defined_css_classes(sample_snapshot: StorcliSnapshot) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        _insert_operator_event(test_app)
        html = "".join(
            client.get(path, headers={"Accept": "text/html"}).text
            for path in (
                "/",
                "/controller",
                "/drives",
                "/drives/252/0",
                "/drives/252/4",
                "/drives/252/4/charts?range_days=7",
                "/events",
                "/controller/foreign-config",
            )
        )

    missing = _parse(html).classes - _stylesheet_classes() - _UNSTYLED_HOOK_CLASSES
    assert missing == set()


def test_templates_and_scripts_reference_only_defined_css_classes() -> None:
    defined = _stylesheet_classes() | _UNSTYLED_HOOK_CLASSES
    missing: dict[str, set[str]] = {}
    for path in sorted(TEMPLATE_DIR.rglob("*.html")):
        for value in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            value = re.sub(r"\{\{.*?\}\}", "{}", value)
            value = re.sub(r"\{%.*?%\}", " ", value)
            for name in value.split():
                if "{" not in name and name not in defined:
                    missing.setdefault(name, set()).add(path.name)
    for path in sorted((PACKAGE_ROOT / "static" / "js").glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for value in re.findall(r'className = "([^"]*)"|class="([^"]*)"', source):
            for name in " ".join(value).split():
                if name not in defined:
                    missing.setdefault(name, set()).add(path.name)

    assert missing == {}


def test_live_refresh_pauses_for_editing_dialogs_and_wizard() -> None:
    source = (PACKAGE_ROOT / "static" / "js" / "live-refresh.js").read_text(encoding="utf-8")

    assert "const INTERVAL_MS = 30000;" in source
    assert 'document.body.dataset.liveRefresh === "off"' in source
    assert 'document.querySelector("dialog[open]")' in source
    assert 'document.querySelector("[data-live-pause]:not([hidden])")' in source
    assert "active.matches(EDITABLE)" in source
    assert "active.matches(INTERACTIVE)" in source
    assert 'setStatus("live", "Live · " + fmtTime(new Date()));' in source


def test_replace_wizard_stages_pause_live_refresh(sample_snapshot: StorcliSnapshot) -> None:
    html = _get(sample_snapshot, "/drives/252/4").text

    stages = re.findall(r'<div class="replace-wizard__stage" data-stage="[^"]+"([^>]*)>', html)
    assert len(stages) == 6
    assert all("data-live-pause" in attrs and "hidden" in attrs for attrs in stages)


def test_confirm_dialog_blocks_submit_until_phrase_matches() -> None:
    source = (PACKAGE_ROOT / "static" / "js" / "confirm-dialog.js").read_text(encoding="utf-8")

    assert "confirmButton.disabled = Boolean(phrase);" in source
    assert "confirmButton.disabled = input.value.trim() !== phrase;" in source
    assert 'resolve(dialog.returnValue === "confirm");' in source
    assert "event.stopImmediatePropagation();" in source
    assert source.rstrip().endswith("true\n  );\n\n  window.confirmAction = confirmAction;\n})();")


def _stylesheet_classes() -> set[str]:
    stylesheet = re.sub(r"/\*.*?\*/", "", STYLESHEET.read_text(encoding="utf-8"), flags=re.S)
    return set(re.findall(r"\.(-?[_a-zA-Z][\w-]*)", stylesheet))


def _get(
    sample_snapshot: StorcliSnapshot,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        return client.get(path, headers=headers)


def _parse(html: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(html)
    return parser


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    with test_app.state.session_factory() as session:
        insert_snapshot(session, sample_snapshot)
        session.commit()


def _insert_operator_event(test_app: FastAPI) -> None:
    with test_app.state.session_factory() as session:
        session.add(
            Event(
                occurred_at=datetime(2026, 4, 25, 12, 1, tzinfo=UTC),
                severity="warning",
                category="operator_action",
                subject="maintenance",
                summary="Maintenance started for drive 252:4",
            )
        )
        session.commit()
