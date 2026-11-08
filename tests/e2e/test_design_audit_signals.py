"""Computed alarm colours and layout under simultaneous hardware faults, without hardware."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import Browser, Locator, Page, expect
from sqlalchemy import select, update

from megaraid_dashboard.db import ControllerSnapshot, Event, PhysicalDriveSnapshot, get_engine
from tests.e2e.conftest import _LiveServerHandle
from tests.safety.harness import FakeStorcli


def _field(page: Page, label: str) -> Locator:
    return page.locator("dl > div").filter(has=page.get_by_text(label, exact=True)).locator("dd")


def _token_colour(page: Page, token: str) -> str:
    return page.evaluate(
        """token => {
          const probe = document.createElement('span');
          probe.style.color = 'var(' + token + ')';
          document.body.append(probe);
          const colour = getComputedStyle(probe).color;
          probe.remove();
          return colour;
        }""",
        token,
    )


@pytest.mark.parametrize("width", [1440, 1024, 390])
def test_combined_faults_keep_alarm_colours_and_fit_viewport(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
    width: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from megaraid_dashboard.services import drive_detail

    connection_builder = drive_detail._build_connection
    # Link speeds are optional view metadata; exercise the degraded rendering as well.
    monkeypatch.setattr(
        drive_detail,
        "_build_connection",
        lambda drive: replace(
            connection_builder(drive),
            device_speed_text="6.0Gb/s",
            link_speed_text="3.0Gb/s",
            link_speed_is_degraded=True,
        ),
    )
    page = authenticated_page
    page.set_viewport_size({"width": width, "height": 1000})
    engine = get_engine(snapshot_with_drives)
    try:
        with engine.begin() as connection:
            connection.execute(
                update(PhysicalDriveSnapshot)
                .where(PhysicalDriveSnapshot.slot_id == 6)
                .values(
                    state="Offln",
                    smart_alert=True,
                    predictive_failures=2,
                )
            )
            connection.execute(
                update(PhysicalDriveSnapshot)
                .where(PhysicalDriveSnapshot.slot_id == 3)
                .values(media_errors=12, other_errors=3, temperature_celsius=61)
            )
        page.goto(f"{live_server}/drives")
        critical = _token_colour(page, "--color-critical")
        warning = _token_colour(page, "--color-warning")
        row = page.locator("tbody tr").filter(
            has=page.get_by_role("link", name="252:6", exact=True)
        )
        expect(row).to_contain_text(
            "Drive is offline. S.M.A.R.T. alert is active. 2 predictive failures."
        )
        expect(row.locator('[data-label="Predictive"]')).to_have_css("color", critical)
        expect(row.locator('[data-label="SMART alert"]')).to_have_css("color", critical)
        expect(page.locator(".summary-chips")).to_contain_text("2 Critical", ignore_case=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        expect(row.locator('[data-label="Temp"]')).to_have_text("35°C")
        expect(row.locator('[data-label="Temp"]')).to_have_attribute(
            "title", "Current 35°C / Warning 55°C / Critical 60°C"
        )
        if folder := os.environ.get("UI_SCREENSHOT_DIR"):
            page.screenshot(path=str(Path(folder) / f"combined-faults-{width}.png"), full_page=True)

        page.goto(f"{live_server}/drives/252:6")
        expect(_field(page, "SMART alert")).to_have_css("color", critical)
        expect(_field(page, "Predictive Failures")).to_have_css("color", critical)
        expect(_field(page, "Link Speed")).to_have_css("color", warning)
        expect(_field(page, "Media Errors")).to_have_css(
            "color", _token_colour(page, "--color-muted")
        )
        if folder:
            page.screenshot(path=str(Path(folder) / f"fault-detail-{width}.png"), full_page=True)

        page.goto(f"{live_server}/drives/252:3")
        expect(_field(page, "Temperature")).to_have_css("color", critical)
        expect(_field(page, "Temperature")).to_have_text("61°C")
        expect(_field(page, "Media Errors")).to_have_css("color", warning)
        expect(_field(page, "Other Errors")).to_have_css("color", warning)

        with engine.begin() as connection:
            connection.execute(
                update(PhysicalDriveSnapshot).values(
                    state="Onln",
                    smart_alert=False,
                    predictive_failures=0,
                    media_errors=0,
                    other_errors=0,
                    temperature_celsius=35,
                )
            )
        page.goto(f"{live_server}/drives")
        expect(page.locator(".summary-chips")).to_contain_text("8 Healthy", ignore_case=True)
        expect(page.locator(".drive-health-reason")).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert fake_storcli.calls == []
    finally:
        engine.dispose()


def test_chart_structure_thresholds_and_instances_survive_every_range(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
) -> None:
    page = authenticated_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{live_server}/drives/252:6")
    for days in (30, 7, 365, 30, 7):
        if days != 30 or page.locator("#chart-area").get_attribute("data-range-days") != "30":
            page.get_by_role("tab", name=f"{days}d", exact=True).click()
        expect(page.locator("#chart-area")).to_have_attribute("data-range-days", str(days))
        expect(page.get_by_role("heading", name="Temperature History", exact=True)).to_have_count(1)
        expect(page.get_by_role("heading", name="Error Counter History", exact=True)).to_have_count(
            1
        )
        expect(page.locator("#temperature-history-chart")).to_have_count(1)
        expect(page.locator("#error-history-chart")).to_have_count(1)
        page.wait_for_function("Object.keys(window.Chart?.instances || {}).length === 2")
        assert (
            page.locator("#temperature-history-data").evaluate(
                "el => JSON.parse(el.textContent).thresholdDatasets.length"
            )
            == 2
        )
        assert page.evaluate("Object.keys(Chart.instances).length") == 2
        expect(page.get_by_role("tab", name=f"{days}d", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
    assert errors == []
    assert fake_storcli.calls == []


def test_controller_error_counts_keep_warning_colour_and_unknown_drive_is_counted(
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
        snapshot.raw_json = {**snapshot.raw_json, "Memory Correctable Errors": "2"}
        session.add(
            Event(
                occurred_at=datetime.now(UTC),
                severity="critical",
                category="pd_state",
                subject="252:6",
                summary="Drive is offline.",
            )
        )
        drive = session.scalar(
            select(PhysicalDriveSnapshot).where(PhysicalDriveSnapshot.slot_id == 6)
        )
        assert drive is not None
        drive.state = "Unrecognized-" + "x" * 200
    page = authenticated_page
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(live_server + "/controller")
    warning = _token_colour(page, "--color-warning")
    expect(_field(page, "Mem ECC Errors")).to_have_text("2")
    expect(_field(page, "Mem ECC Errors")).to_have_css("color", warning)
    expect(_field(page, "Errors (24h)")).to_have_text("1")
    expect(_field(page, "Errors (24h)")).to_have_css("color", warning)
    page.goto(live_server + "/drives")
    expect(page.locator(".summary-chips")).to_contain_text("1 Attention", ignore_case=True)
    expect(page.locator(".summary-chips")).to_contain_text("7 Healthy", ignore_case=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert fake_storcli.calls == []


@pytest.fixture(params=[("en-US", "Europe/Rome"), ("en-GB", "America/New_York")])
def local_page(
    browser: Browser,
    test_admin_creds: dict[str, str],
    request: pytest.FixtureRequest,
) -> Iterator[Page]:
    locale, timezone = request.param
    context = browser.new_context(
        ignore_https_errors=True,
        http_credentials={
            "username": test_admin_creds["username"],
            "password": test_admin_creds["password"],
        },
        locale=locale,
        timezone_id=timezone,
    )
    context.route(
        "**/*",
        lambda route: (
            route.continue_()
            if urlsplit(route.request.url).hostname in {"localhost", "127.0.0.1", "::1"}
            else route.abort()
        ),
    )
    try:
        yield context.new_page()
    finally:
        context.close()


def test_observed_uses_local_24h_time_and_refresh_preserves_controller_schedule(
    local_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    page = local_page
    app = _live_server_handle.app
    observed = datetime(2026, 10, 31, 23, 19, tzinfo=UTC)
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        assert snapshot is not None
        snapshot.captured_at = observed
        snapshot.raw_json = {
            **snapshot.raw_json,
            "patrol_read": {
                "mode": "auto",
                "state": "stopped",
                "interval_hours": 168,
                "next_start_timestamp": "10/31/2026, 09:00:00",
            },
        }
    timezone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    for path in ("/drives", "/drives/252:6", "/controller"):
        page.goto(live_server + path)
        stamp = page.locator(".page-header time")
        expect(stamp).to_have_attribute("datetime", "2026-10-31T23:19:00Z")
        expect(stamp).to_contain_text(observed.astimezone(ZoneInfo(timezone)).strftime("%H:%M"))
        expect(stamp).not_to_contain_text("AM")
        expect(stamp).not_to_contain_text("PM")
    schedule = page.locator("[data-patrol-schedule]")
    expect(schedule).to_contain_text("31 Oct 2026, 09:00 (controller time)")
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        assert snapshot is not None
        snapshot.captured_at = datetime(2026, 10, 31, 23, 25, tzinfo=UTC)
    page.get_by_role("button", name="Refresh now", exact=True).click()
    expect(page.locator(".page-header time")).to_have_attribute("datetime", "2026-10-31T23:25:00Z")
    expect(page.locator(".page-header time")).to_contain_text(
        datetime(2026, 10, 31, 23, 25, tzinfo=UTC).astimezone(ZoneInfo(timezone)).strftime("%H:%M")
    )
    expect(schedule).to_contain_text("31 Oct 2026, 09:00 (controller time)")
    assert fake_storcli.calls == []
