"""Operator scenarios use the real page, application gates and fake controller boundary."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect
from sqlalchemy import select

from megaraid_dashboard.db import ControllerSnapshot
from tests.e2e.conftest import login_and_navigate
from tests.safety.harness import prepare
from tests.safety.scenarios import ACTIONS


@pytest.mark.usefixtures("full_controller_handlers")
@pytest.mark.parametrize("mode", ["auto", "manual", "unknown"])
def test_schedule_and_selected_mode_are_together_when_locked(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli, mode
):
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": False})
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.raw_json = {
            "patrol_read": {
                "mode": mode,
                "state": "stopped",
                "interval_hours": 168,
                "next_start_timestamp": "11/07/2026, 09:00:00",
            },
            "consistency_check": {"mode": "concurrent", "state": "active"},
            "foreign_config_state": {"present": False},
        }
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    row = page.locator('[data-action-key="Patrol Read"]')
    expect(row.locator("[data-operation-state]")).to_have_text("Stopped")
    expect(page.get_by_role("heading", name="Scheduled Tasks")).to_have_count(0)
    expect(row.get_by_role("button", name="Start", exact=True)).to_be_disabled()
    if mode == "manual":
        expect(row.locator("[data-patrol-schedule]")).to_have_text("Automatic scheduling disabled.")
    else:
        expect(row.locator("[data-patrol-schedule]")).to_contain_text(
            "Every 7 days · Next 07 Nov 2026, 09:00 (controller time)"
        )
    selected = row.locator('button[aria-pressed="true"]')
    if mode == "unknown":
        expect(selected).to_have_count(0)
        expect(row).to_contain_text("Unknown mode")
    else:
        expect(selected).to_have_text(mode.title())
    expect(
        page.locator('[data-action-key="Consistency Check"] [data-operation-state]')
    ).to_have_text("Running")
    assert fake_storcli.calls == []
    if mode == "auto" and (folder := os.environ.get("UI_SCREENSHOT_DIR")):
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / "controller-auto-schedule.png"), full_page=True)
        page.locator(".action-panel").screenshot(path=str(target / "controller-actions-auto.png"))


def test_buzzer_and_foreign_unknowns_are_not_success(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli
):
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.alarm_state = "Unknown"
        snapshot.raw_json = {}
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    buzzer = page.locator('[data-controller-section="buzzer-control"]')
    expect(buzzer.locator('button[aria-pressed="true"]')).to_have_count(0)
    expect(buzzer.locator(".status-badge")).to_have_text("Unknown")
    foreign = page.locator('[data-foreign-config-state="unknown"]')
    expect(foreign).to_contain_text("Unknown")
    expect(foreign).not_to_contain_text("None detected")
    expect(foreign.get_by_role("link", name="Review…")).to_have_count(0)
    page.get_by_label("About Alarm Buzzer", exact=True).click()
    expect(buzzer).to_contain_text("it does not mean the buzzer is sounding")
    page.get_by_label("About Consistency Check", exact=True).press("Enter")
    expect(page.locator("#operation-help-consistency-check")).to_contain_text(
        "Last reported run unknown"
    )
    assert fake_storcli.calls == []


@pytest.mark.parametrize("state", ["active", "stopped", "unknown"])
def test_overview_and_controller_agree_on_observed_operation(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli, state
):
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.captured_at = datetime(2026, 10, 29, 8, tzinfo=UTC)
        snapshot.raw_json = {
            "consistency_check": {"mode": "concurrent", "state": state},
            "patrol_read": {"mode": "auto", "state": "stopped"},
        }
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/")
    ops = page.locator(".controller-ops")
    if state == "active":
        expect(ops).to_contain_text("Consistency Check")
        expect(ops).not_to_contain_text("No active operations")
    elif state == "stopped":
        expect(ops).to_contain_text("No active operations")
    else:
        expect(ops).to_contain_text("Operation status unknown")
    page.get_by_role("navigation", name="Primary navigation").get_by_role(
        "link", name="Controller"
    ).click()
    expect(
        page.locator('[data-action-key="Consistency Check"] [data-operation-state]')
    ).to_have_text({"active": "Running", "stopped": "Stopped", "unknown": "Unknown"}[state])
    expect(page.locator(".page-header")).to_contain_text("Observed October 29, 2026 08:00 UTC")
    assert fake_storcli.calls == []


@pytest.mark.usefixtures("full_controller_handlers")
def test_success_waits_for_fresh_observation_and_prevents_duplicate_command(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli
):
    action = next(a for a in ACTIONS if a.name == "patrol-read-start")
    prepare(action, _live_server_handle, fake_storcli)
    app = _live_server_handle.app
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.raw_json = {
            **snapshot.raw_json,
            "patrol_read": {"mode": "manual", "state": "stopped"},
        }
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    # A user's clock can be ahead of SERVER; pending completion must use server time.
    page.clock.set_system_time(datetime.now(UTC) + timedelta(days=1))
    row = page.locator('[data-action-key="Patrol Read"]')
    start = row.get_by_role("button", name="Start", exact=True)
    with page.expect_response(lambda r: r.url.endswith(action.path) and r.request.method == "POST"):
        start.click()
    expect(row.locator("[data-controller-action-status]")).to_contain_text("Command accepted")
    expect(row.locator("[data-operation-state]")).to_have_text("Stopped")
    expect(start).to_be_disabled()
    # A page refresh with the same saved sample must retain pending state.
    page.get_by_role("button", name="Refresh now", exact=True).click()
    expect(row.locator("[data-controller-action-status]")).to_contain_text("Command accepted")
    expect(start).to_be_disabled()
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.captured_at = datetime.now(UTC) + timedelta(seconds=1)
        snapshot.raw_json = {
            **snapshot.raw_json,
            "patrol_read": {"mode": "manual", "state": "active"},
        }
    page.get_by_role("button", name="Refresh now", exact=True).click()
    expect(row.locator("[data-operation-state]")).to_have_text("Running")
    expect(row.locator("[data-controller-action-status]")).to_be_hidden()
    expect(row.get_by_role("button", name="Stop", exact=True)).to_be_enabled()
    assert fake_storcli.mutations == [list(action.command)]


@pytest.mark.usefixtures("full_controller_handlers")
def test_lost_response_does_not_claim_success_or_retry(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli
):
    action = next(a for a in ACTIONS if a.name == "patrol-read-start")
    prepare(action, _live_server_handle, fake_storcli)
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.raw_json = {
            **snapshot.raw_json,
            "patrol_read": {"mode": "manual", "state": "stopped"},
        }
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    page.route("**/controller/patrol-read/start", lambda route: route.abort())
    row = page.locator('[data-action-key="Patrol Read"]')
    row.get_by_role("button", name="Start", exact=True).click()
    expect(row.locator("[data-controller-action-status]")).to_contain_text("Result unknown")
    expect(row.locator("[data-operation-state]")).to_have_text("Stopped")
    expect(row.get_by_role("button", name="Start", exact=True)).to_be_disabled()
    assert fake_storcli.mutations == []
