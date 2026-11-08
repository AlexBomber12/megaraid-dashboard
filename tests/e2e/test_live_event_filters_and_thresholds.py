"""Independent live category revisions and readable threshold text at plot edges."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from playwright.sync_api import Page, expect

from megaraid_dashboard.db import Event
from tests.e2e.conftest import _LiveServerHandle
from tests.safety.harness import FakeStorcli


def _event(app: FastAPI, category: str, severity: str, summary: str, when: datetime) -> None:
    with app.state.session_factory() as session, session.begin():
        session.add(
            Event(
                occurred_at=when,
                severity=severity,
                category=category,
                subject="252:5",
                summary=summary,
            )
        )


@pytest.mark.parametrize(
    "query",
    [
        "category=pd_state",
        "severity=warning",
        "category=pd_state&severity=warning",
        "category=temperature",
    ],
)
def test_excluded_event_updates_categories_and_quiet_poll_preserves_navigation(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    query: str,
) -> None:
    app = _live_server_handle.app
    now = datetime.now(UTC)
    _event(app, "pd_state", "warning", "baseline-event", now)
    page = authenticated_page
    page.clock.install()
    page.goto(live_server + "/events?" + query)
    filters = page.locator("#events-filters")
    expect(filters).not_to_contain_text("SMART Alert")
    _event(app, "smart_alert", "critical", "excluded-new-event", now + timedelta(seconds=1))
    with page.expect_response(lambda response: "/partials/events?" in response.url) as update:
        page.clock.run_for(30_001)
    assert 'id="events-filters"' in update.value.text()
    expect(filters).to_contain_text("SMART Alert 1")
    expect(page.get_by_text("excluded-new-event", exact=True)).to_have_count(0)
    poll_url = page.locator("#events-poller").get_attribute("hx-get")
    assert poll_url is not None
    assert all(part in poll_url for part in query.split("&"))
    page.evaluate("window.currentEventFilters = document.getElementById('events-filters')")
    with page.expect_response(lambda response: "/partials/events?" in response.url) as quiet:
        page.clock.run_for(30_001)
    assert 'id="events-filters"' not in quiet.value.text()
    assert page.evaluate("window.currentEventFilters === document.getElementById('events-filters')")
    assert fake_storcli.calls == []


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize(
    ("path", "warning_name", "critical_name", "warning", "critical", "canvas"),
    [
        (
            "/controller",
            "roc_temp_warning_celsius",
            "roc_temp_critical_celsius",
            109,
            110,
            "roc-history-chart",
        ),
        (
            "/drives/252:4",
            "temp_warning_celsius",
            "temp_critical_celsius",
            59,
            60,
            "temperature-history-chart",
        ),
        (
            "/drives/252:4",
            "temp_warning_celsius",
            "temp_critical_celsius",
            21,
            22,
            "temperature-history-chart",
        ),
    ],
    ids=["roc-top", "drive-close", "drive-bottom"],
)
def test_threshold_text_boxes_stay_separate_inside_the_plot(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    path: str,
    warning_name: str,
    critical_name: str,
    warning: int,
    critical: int,
    canvas: str,
) -> None:
    app = _live_server_handle.app
    monkeypatch.setattr(
        app.state,
        "settings",
        app.state.settings.model_copy(update={warning_name: warning, critical_name: critical}),
    )
    page = authenticated_page
    page.set_viewport_size({"width": width, "height": 1000})
    page.add_init_script("""(() => {
      window.thresholdText = [];
      const original = CanvasRenderingContext2D.prototype.fillText;
      CanvasRenderingContext2D.prototype.fillText = function(text, x, y, ...rest) {
        if (text === 'Warning' || text === 'Critical') {
          const size = this.measureText(text);
          window.thresholdText.push({text, x, y,
            top:y-size.actualBoundingBoxAscent, bottom:y+size.actualBoundingBoxDescent});
        }
        return original.call(this, text, x, y, ...rest);
      };
    })();""")
    page.goto(live_server + path)
    page.wait_for_function("window.thresholdText.length >= 2")
    report = page.evaluate(
        """id => ({labels:window.thresholdText.slice(-2),
          area:Chart.getChart(document.getElementById(id)).chartArea})""",
        canvas,
    )
    labels = sorted(report["labels"], key=lambda item: item["top"])
    assert {label["text"] for label in labels} == {"Warning", "Critical"}
    assert labels[0]["bottom"] + 2 <= labels[1]["top"], report
    assert all(
        report["area"]["top"] <= label["top"] <= label["bottom"] <= report["area"]["bottom"]
        for label in labels
    ), report
    if directory := os.environ.get("UI_SCREENSHOT_DIR"):
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        section = "roc-history" if path == "/controller" else "temperature-chart"
        prefix = "controller" if path == "/controller" else "drive-detail"
        page.locator(f'[data-{prefix}-section="{section}"]').screenshot(
            path=str(destination / f"{canvas}-{width}-{warning}-{critical}.png")
        )
    assert fake_storcli.calls == []
