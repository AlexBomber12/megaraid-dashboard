"""Shared theme semantics survive real chart rendering, swaps and fault presentation."""

from datetime import UTC, datetime

from playwright.sync_api import Page, expect
from sqlalchemy import select

from megaraid_dashboard.db import ControllerSnapshot
from tests.e2e.conftest import _LiveServerHandle
from tests.safety.harness import FakeStorcli


def test_neutral_chart_series_and_shared_thresholds_follow_css_tokens(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
) -> None:
    page = authenticated_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_server + "/drives/252:6")
    page.wait_for_function("Object.keys(window.Chart?.instances || {}).length === 2")
    for days in (30, 7, 365):
        if days != 30:
            page.evaluate("document.documentElement.style.setProperty('--color-accent', '#c0ffee')")
            page.get_by_role("tab", name=f"{days}d", exact=True).click()
            expect(page.locator("#chart-area")).to_have_attribute("data-range-days", str(days))
        page.wait_for_function("Object.keys(window.Chart?.instances || {}).length === 2")
        result = page.evaluate("""() => {
          const temp = Chart.getChart(document.getElementById('temperature-history-chart'));
          const errors = Chart.getChart(document.getElementById('error-history-chart'));
          const theme = chartTheme.palette();
          return {tempCount: temp.data.datasets.length,
            tempColour: temp.data.datasets[0].borderColor,
            legend: temp.options.plugins.legend.display,
            thresholds: temp.options.plugins.temperatureThresholds.levels.map(x => x.color),
            errorColours: errors.data.datasets.map(x => x.borderColor),
            errorLegend: errors.options.plugins.legend.display, theme};
        }""")
        assert result["tempCount"] == 1
        assert result["legend"] is False
        assert result["tempColour"] == result["theme"]["accent"]
        assert result["thresholds"] == [result["theme"]["warning"], result["theme"]["critical"]]
        assert result["errorColours"] == [
            result["theme"][key] for key in ("accent", "info", "muted")
        ]
        assert result["errorLegend"] is True
    assert errors == []
    assert fake_storcli.calls == []


def test_unknown_modes_are_neutral_and_overview_observation_updates(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    app = _live_server_handle.app
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        assert snapshot is not None
        snapshot.captured_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
        snapshot.alarm_state = "Unknown"
        snapshot.raw_json = {
            "patrol_read": {"mode": "unknown", "state": "unknown"},
            "foreign_config": {"present": False, "is_known": False},
        }
    page = authenticated_page
    page.goto(live_server + "/controller")
    expect(page.locator('[data-action-key="Patrol Read"] [data-operation-state]')).to_have_class(
        "status-badge status-badge--unknown"
    )
    buzzer = page.locator('[data-action-key="Alarm Buzzer"]')
    expect(buzzer.locator(".status-badge")).to_have_class("status-badge status-badge--unknown")
    expect(buzzer.get_by_role("button", name="Silence", exact=True)).to_be_disabled()
    page.goto(live_server + "/")
    expect(page.locator(".controller-observed time")).to_have_attribute(
        "datetime", "2026-11-01T12:00:00Z"
    )
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        assert snapshot is not None
        snapshot.captured_at = datetime(2026, 11, 1, 12, 5, tzinfo=UTC)
    page.get_by_role("button", name="Refresh now", exact=True).click()
    expect(page.locator(".controller-observed time")).to_have_attribute(
        "datetime", "2026-11-01T12:05:00Z"
    )
    page.locator(".summary-chips").get_by_role("link", name="8 Drives →", exact=True).click()
    expect(page.get_by_role("heading", name="Drives", exact=True)).to_be_visible()
    assert fake_storcli.calls == []
