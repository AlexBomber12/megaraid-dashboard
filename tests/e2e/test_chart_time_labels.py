"""Readable real canvas ticks and tooltips, including range swaps and local time zones."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from megaraid_dashboard.db import PhysicalDriveMetricsHourly
from tests.e2e.conftest import _LiveServerHandle, login_and_navigate


@pytest.fixture(params=["Europe/Rome", "America/New_York"])
def chart_page(
    browser: Browser, test_admin_creds: dict[str, str], request: pytest.FixtureRequest
) -> Iterator[Page]:
    context = browser.new_context(
        ignore_https_errors=True,
        http_credentials={
            "username": test_admin_creds["username"],
            "password": test_admin_creds["password"],
        },
        timezone_id=request.param,
        locale="en-GB",
    )
    # These browser tests have no external network or hardware dependency.
    context.route(
        "**/*",
        lambda route: (
            route.continue_()
            if urlsplit(route.request.url).hostname in {"127.0.0.1", "localhost", "::1"}
            else route.abort()
        ),
    )
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


def _check_chart(page: Page, canvas_id: str) -> None:
    page.wait_for_function(
        "id => window.Chart?.getChart(document.getElementById(id))?.data.labels.length > 1",
        arg=canvas_id,
    )
    result = page.evaluate(
        """id => {
      const chart = Chart.getChart(document.getElementById(id));
      chart.resize();
      chart.update('none');
      const scale = chart.scales.x;
      const ctx = chart.ctx;
      ctx.save();
      ctx.font = Chart.helpers.toFont(scale.options.ticks.font).string;
      const ticks = scale.ticks.map((tick, index) => {
        const lines = Array.isArray(tick.label) ? tick.label : [tick.label];
        const width = Math.max(...lines.map(line => ctx.measureText(line).width));
        return {label: lines.join(' '), x: scale.getPixelForTick(index), width};
      });
      ctx.restore();
      return {ticks, title: scale.options.title.text, labels: chart.data.labels};
    }""",
        canvas_id,
    )
    assert 1 < len(result["ticks"]) <= 8, result
    timezone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    assert result["title"] == "Local time · " + timezone
    assert all("T" not in tick["label"] and len(tick["label"]) <= 22 for tick in result["ticks"])
    for left, right in zip(result["ticks"], result["ticks"][1:], strict=False):
        assert right["x"] - left["x"] >= (left["width"] + right["width"]) / 2 + 4, result

    point = page.evaluate(
        """id => {
      const chart = Chart.getChart(document.getElementById(id));
      const index = Math.floor(chart.data.labels.length / 2);
      const element = chart.getDatasetMeta(0).data[index];
      const rect = chart.canvas.getBoundingClientRect();
      return {x: rect.x + element.x, y: rect.y + element.y, index};
    }""",
        canvas_id,
    )
    page.locator("#" + canvas_id).scroll_into_view_if_needed()
    # Recompute after scroll so the actual pointer event hits the canvas.
    rect = page.locator("#" + canvas_id).bounding_box()
    element = page.evaluate(
        """({id, index}) => {
      const chart = Chart.getChart(document.getElementById(id));
      const point = chart.getDatasetMeta(0).data[index];
      const y = Math.max(chart.chartArea.top + 2, Math.min(chart.chartArea.bottom - 2, point.y));
      return {x: point.x, y};
    }""",
        {"id": canvas_id, "index": point["index"]},
    )
    page.mouse.move(rect["x"] + element["x"], rect["y"] + element["y"])
    page.wait_for_function(
        "id => Chart.getChart(document.getElementById(id)).tooltip.opacity > 0",
        arg=canvas_id,
    )
    tooltip = page.evaluate(
        "id => Chart.getChart(document.getElementById(id)).tooltip.title.join(' ')", canvas_id
    )
    raw = result["labels"][point["index"]]
    instant = datetime.fromisoformat(raw)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    local = instant.astimezone(ZoneInfo(timezone))
    assert local.strftime("%H:%M:%S") in tooltip
    assert str(local.year) in tooltip
    assert not any(part in tooltip for part in ("+00:00", ".000000", "T00:"))


def test_roc_labels_all_ranges_and_widths(
    chart_page: Page, live_server: str, snapshot_with_drives: str
) -> None:
    page = chart_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def history(route: Route) -> None:
        hours = int(parse_qs(urlsplit(route.request.url).query)["range_hours"][0])
        end = datetime(2026, 10, 31, 14, 30, tzinfo=UTC)
        route.fulfill(
            json={
                "points": [
                    {
                        "captured_at": (end - timedelta(hours=hours * (288 - i) / 288)).isoformat(),
                        "temperature_celsius": 80 + i % 5,
                    }
                    for i in range(289)
                ],
                "warning_threshold": 95,
                "critical_threshold": 105,
                "min_celsius": 75,
            }
        )

    page.route("**/controller/roc-history?*", history)
    login_and_navigate(page, live_server + "/controller")
    _check_chart(page, "roc-history-chart")
    for label in ("1h", "24h", "7d", "30d"):
        page.get_by_role("tab", name=label, exact=True).click()
        expect(page.get_by_role("tab", name=label, exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        for width in (1440, 650, 375):
            page.set_viewport_size({"width": width, "height": 1000})
            _check_chart(page, "roc-history-chart")
            if (folder := os.environ.get("UI_SCREENSHOT_DIR")) and label == "24h":
                timezone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
                page.locator('[data-controller-section="roc-history"]').screenshot(
                    path=str(Path(folder) / f"roc-{timezone.replace('/', '-')}-{width}.png")
                )
    assert errors == []


def test_drive_labels_after_every_htmx_range(
    chart_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
) -> None:
    page = chart_page
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        now = datetime.now(UTC)
        for offset in range(1, 365):
            session.add(
                PhysicalDriveMetricsHourly(
                    bucket_start=now - timedelta(days=offset),
                    enclosure_id=252,
                    slot_id=0,
                    serial_number="WCA275678000",
                    temperature_celsius_min=33,
                    temperature_celsius_max=36,
                    temperature_celsius_avg=34.5,
                    temperature_sample_count=1,
                    media_errors_max=0,
                    other_errors_max=0,
                    predictive_failures_max=0,
                    sample_count=1,
                )
            )
    login_and_navigate(page, live_server + "/drives/252:0")
    _check_chart(page, "temperature-history-chart")
    for label in ("7d", "30d", "365d", "7d"):
        page.get_by_role("tab", name=label, exact=True).click()
        expect(page.locator("#error-history-chart")).to_be_visible()
        expect(page.get_by_role("tab", name=label, exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        for width in (1440, 650, 375):
            page.set_viewport_size({"width": width, "height": 1000})
            for canvas_id in ("temperature-history-chart", "error-history-chart"):
                _check_chart(page, canvas_id)


def test_time_formatters_distinguish_dst_fold_and_handle_missing_times(
    chart_page: Page, live_server: str, snapshot_with_drives: str
) -> None:
    page = chart_page
    login_and_navigate(page, live_server + "/controller")
    result = page.evaluate("""() => {
      const labels = ['2026-10-25T00:30:00.123456+00:00', '2026-10-25T01:30:00.123456+00:00'];
      const axis = localTime.chartAxis(labels, 1);
      const tooltip = localTime.chartTooltip(labels).callbacks.title;
      const invalid = localTime.chartAxis(['not a timestamp', null], 24);
      const legacy = localTime.chartTooltip(['2026-10-31 14:30']).callbacks.title;
      return {
        ticks: [axis.ticks.callback(0), axis.ticks.callback(1)],
        titles: [tooltip([{dataIndex: 0}]), tooltip([{dataIndex: 1}])],
        invalid: [invalid.ticks.callback(0), invalid.ticks.callback(1)],
        emptyTooltip: tooltip([]),
        legacy: legacy([{dataIndex: 0}]),
      };
    }""")
    assert result["titles"][0] != result["titles"][1]
    if page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "Europe/Rome":
        assert result["ticks"] == ["02:30", "02:30"]
        assert "CEST" in result["titles"][0] or "GMT+2" in result["titles"][0]
        assert "CET" in result["titles"][1] or "GMT+1" in result["titles"][1]
    assert result["invalid"] == ["Unknown time", "Unknown time"]
    assert result["emptyTooltip"] == "Unknown time"
    timezone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    expected = datetime(2026, 10, 31, 14, 30, tzinfo=UTC).astimezone(ZoneInfo(timezone))
    assert expected.strftime("%H:%M:%S") in result["legacy"]
