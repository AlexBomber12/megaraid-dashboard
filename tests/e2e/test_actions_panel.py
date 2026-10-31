"""The compact layout must stay usable at the widths missed by the proposal."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect
from sqlalchemy import delete, select

from megaraid_dashboard.db import ControllerSnapshot, VirtualDriveSnapshot
from megaraid_dashboard.db.dao import set_maintenance_state
from tests.e2e.conftest import login_and_navigate

pytestmark = pytest.mark.usefixtures("full_controller_handlers")


def _window(app, active):
    with app.state.session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=active,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            started_by="isolated UI test",
        )


@pytest.mark.parametrize("path", ["/controller", "/drives/252:0", "/controller/foreign-config"])
@pytest.mark.parametrize("state", ["read-only", "locked", "active"])
def test_action_layout_across_breakpoints(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    path,
    state,
):
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(
        update={"controller_read_only": state == "read-only", "maintenance_mode": False}
    )
    _window(app, state == "active")
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.raw_json = {
            **snapshot.raw_json,
            "patrol_read": {"mode": "Manual", "state": "Stopped"},
            "consistency_check": {"mode": "Concurrent", "state": "Stopped"},
        }
    page = authenticated_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login_and_navigate(page, f"{live_server}{path}")
    expect(page.locator(".action-panel")).to_have_count(1)
    for width in (1440, 1100, 900, 760, 650, 561, 375):
        page.set_viewport_size({"width": width, "height": 1000})
        layout = page.evaluate("""() => ({
          width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
          overflow: [...document.querySelectorAll('body *')].filter(el =>
            el.getBoundingClientRect().right > innerWidth + 1).slice(0, 12).map(el => ({
              tag: el.tagName, cls: el.className, right: el.getBoundingClientRect().right
            }))
        })""")
        assert layout["scrollWidth"] <= layout["width"], layout
        assert page.locator(".action-row").evaluate_all("""rows => rows.every(row => {
          if (row.querySelectorAll('.action-row__name h3').length !== 1) return false;
          if (row.querySelector('.action-row__cmd p, .action-row__set p')) return false;
          const status = row.querySelector('.action-row__now')?.getBoundingClientRect();
          return [...row.querySelectorAll('.action-row__cmd, .action-row__set')].every(cell => {
            const box = cell.getBoundingClientRect();
            return !status || box.top >= status.bottom - 1 || box.bottom <= status.top + 1 ||
              box.left >= status.right - 1 || box.right <= status.left + 1;
          });
        })""")
        assert page.locator('main [hx-post], main form[method="post"]').evaluate_all(
            "controls => controls.every(el => el.closest('.action-panel'))"
        )
        if (folder := os.environ.get("UI_SCREENSHOT_DIR")) and width in {1440, 650, 375}:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(target / f"{path.strip('/').replace('/', '-')}-{state}-{width}.png"),
                full_page=True,
            )
    assert errors == []
    assert fake_storcli.mutations == []


def test_mode_selection_tracks_observed_snapshot_on_refresh(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
):
    page = authenticated_page
    app = _live_server_handle.app
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        session.add(
            VirtualDriveSnapshot(
                snapshot_id=snapshot.id,
                vd_id=1,
                name="second",
                raid_level="RAID5",
                size_bytes=1000000,
                state="Optl",
                access="RW",
                cache="RWBD",
            )
        )
    login_and_navigate(page, f"{live_server}/controller")
    for cc_mode, patrol_mode in (("Concurrent", "Auto"), ("Sequential", "Manual")):
        with app.state.session_factory() as session, session.begin():
            snapshot = session.scalar(select(ControllerSnapshot))
            snapshot.raw_json = {
                **snapshot.raw_json,
                "patrol_read": {"mode": patrol_mode, "state": "Stopped"},
                "consistency_check": {"mode": cc_mode, "state": "Active"},
            }
        page.get_by_role("button", name="Refresh now").click()
        for operation, selected in (("Consistency Check", cc_mode), ("Patrol Read", patrol_mode)):
            group = page.get_by_role("group", name=f"{operation} mode", exact=True)
            expect(group.locator('[aria-pressed="true"]')).to_have_text(selected)
            expect(group.locator('[aria-pressed="true"]')).to_be_disabled()
            row = group.locator("xpath=ancestor::div[contains(@class, 'action-row')][1]")
            expect(row.locator(".action-row__now")).not_to_contain_text(f"{selected} mode.")
    assert fake_storcli.mutations == []


def test_cc_mode_visibility_tracks_inventory_on_read_only_refresh(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
):
    page = authenticated_page
    app = _live_server_handle.app
    login_and_navigate(page, f"{live_server}/controller")
    cc_modes = page.get_by_role("group", name="Consistency Check mode", exact=True)
    for vd_count in (1, 2, 0, 1):
        with app.state.session_factory() as session, session.begin():
            snapshot = session.scalar(select(ControllerSnapshot))
            session.execute(
                delete(VirtualDriveSnapshot).where(VirtualDriveSnapshot.snapshot_id == snapshot.id)
            )
            for vd_id in range(vd_count):
                session.add(
                    VirtualDriveSnapshot(
                        snapshot_id=snapshot.id,
                        vd_id=vd_id,
                        name=f"VD {vd_id}",
                        raid_level="RAID5",
                        size_bytes=1000000,
                        state="Optl",
                        access="RW",
                        cache="RWBD",
                    )
                )
            snapshot.raw_json = {
                **snapshot.raw_json,
                "consistency_check": {"mode": "Concurrent", "state": "Active"},
                "patrol_read": {"mode": "Auto", "state": "Stopped"},
            }
        page.get_by_role("button", name="Refresh now").click()
        # Await the matching saved inventory, including transitions with unchanged visibility.
        expect(
            page.locator('[data-controller-section="raid-config"] tr[data-row-state]')
        ).to_have_count(vd_count)
        expect(cc_modes).to_have_count(0 if vd_count == 1 else 1)
        expect(page.locator('form[action="/controller/consistency-check/start"]')).to_have_count(1)
        expect(page.locator('form[action="/controller/consistency-check/stop"]')).to_have_count(1)
        expect(page.get_by_role("group", name="Patrol Read mode", exact=True)).to_have_count(1)
    assert fake_storcli.calls == []


@pytest.mark.parametrize("path", ["/controller", "/drives/252:0"])
def test_expired_window_refreshes_gate_and_recovery_availability(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    path,
):
    app = _live_server_handle.app
    _window(app, True)
    page = authenticated_page
    login_and_navigate(page, f"{live_server}{path}")
    expect(page.locator("#maintenance")).to_contain_text("Unlocked")
    with app.state.session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            started_by="isolated UI test",
        )
    page.get_by_role("button", name="Refresh now").click()
    expect(page.locator("#maintenance")).to_contain_text("Locked")
    expect(page.get_by_role("button", name="Start Maintenance")).to_be_visible()
    expect(page.locator(".maintenance-banner")).to_have_count(0)
    if path.startswith("/drives"):
        expect(page.get_by_role("button", name="Start Locate LED")).to_be_disabled()
        expect(page.get_by_role("button", name="Stop Locate LED")).to_be_enabled()
    else:
        expect(page.locator('[data-buzzer-action="silence"] button')).to_be_disabled()
    assert fake_storcli.mutations == []
