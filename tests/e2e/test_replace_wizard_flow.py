"""Replacement wizard: typed confirmation, cancellation, dry-run traces and real feedback."""

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import update

from megaraid_dashboard.db import PhysicalDriveSnapshot
from tests.e2e.conftest import _LiveServerHandle, login_and_navigate
from tests.safety.harness import FakeStorcli, database_observation, prepare
from tests.safety.scenarios import ACTIONS


def open_wizard(page: Page) -> None:
    page.locator('[data-replace-action="open"]').click()
    expect(page.locator('[data-stage="confirm"]')).to_be_visible()
    page.locator('[data-replace-action="continue-to-serial"]').click()
    serial = page.locator('[data-replace-input="serial"]')
    run = page.locator('[data-replace-action="run-step1"]')
    expect(run).to_be_disabled()
    serial.fill("WRONG")
    expect(run).to_be_disabled()
    serial.fill("WCA275678000")
    expect(run).to_be_enabled()


@pytest.mark.parametrize("outcome", ["success", "failure", "maintenance", "dry-run"])
def test_replace_offline_missing_browser(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    outcome: str,
) -> None:
    page = authenticated_page
    action = next(a for a in ACTIONS if a.name == "replace-missing")
    prepare(action, _live_server_handle, fake_storcli)
    fake_storcli.configure(fault="failure" if outcome == "failure" else "success")
    if outcome == "maintenance":
        app = _live_server_handle.app
        app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": False})
    login_and_navigate(page, f"{live_server}/drives/252:0")
    before = database_observation(snapshot_with_drives)
    open_wizard(page)
    page.locator('[data-stage="serial"] [data-replace-action="cancel"]').click()
    expect(page.locator('[data-replace-action="open"]')).to_be_visible()
    assert fake_storcli.calls == []
    assert database_observation(snapshot_with_drives) == before
    open_wizard(page)
    if outcome != "dry-run":
        page.locator('[data-replace-input="dry-run"]').uncheck()
    with page.expect_response("**/replace/offline") as response:
        page.locator('[data-replace-action="run-step1"]').click()
    assert (
        response.value.status
        == {"success": 200, "failure": 502, "maintenance": 403, "dry-run": 200}[outcome]
    )
    if outcome in {"success", "dry-run"}:
        expect(page.locator('[data-stage="physical-swap"]')).to_be_visible()
        expected = (
            []
            if outcome == "dry-run"
            else [
                ["/c0/e252/s0", "set", "offline", "J"],
                ["/c0/e252/s0", "set", "missing", "J"],
            ]
        )
        assert fake_storcli.mutations == expected
        if outcome == "dry-run":
            assert fake_storcli.calls == []
            assert database_observation(snapshot_with_drives) == before
    else:
        expect(page.locator("[data-replace-output]")).to_contain_text(
            "storcli command failed" if outcome == "failure" else "maintenance_mode"
        )
        assert fake_storcli.mutations == (
            []
            if outcome == "maintenance"
            else [
                ["/c0/e252/s0", "set", "offline", "J"],
            ]
        )
    assert database_observation(snapshot_with_drives)[1] == before[1]
    expect(page.locator("[data-replace-wizard]")).to_have_attribute("aria-busy", "false")


@pytest.mark.parametrize("outcome", ["success", "failure", "dry-run"])
def test_replacement_insert_browser(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    outcome: str,
) -> None:
    page = authenticated_page
    action = next(a for a in ACTIONS if a.name == "replace-offline")
    prepare(action, _live_server_handle, fake_storcli)
    login_and_navigate(page, f"{live_server}/drives/252:0")
    open_wizard(page)
    page.locator('[data-replace-input="dry-run"]').uncheck()
    page.locator('[data-replace-action="run-step1"]').click()
    expect(page.locator('[data-stage="physical-swap"]')).to_be_visible()
    # Simulate collector observing an inserted replacement; never modify physical hardware.
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        session.execute(
            update(PhysicalDriveSnapshot)
            .where(PhysicalDriveSnapshot.slot_id == 0)
            .values(serial_number="NEW-DRIVE", state="UGood")
        )
    fake_storcli.configure(
        serial="NEW-DRIVE",
        drive_state="UGood",
        fault="failure" if outcome == "failure" else "success",
    )
    page.locator('[data-replace-action="continue-to-insert"]').click()
    new_serial = page.locator('[data-replace-input="new-serial"]')
    run = page.locator('[data-replace-action="run-step3"]')
    expect(run).to_be_disabled()
    new_serial.fill("WCA275678000")
    expect(run).to_be_disabled()
    new_serial.fill("NEW-DRIVE")
    expect(run).to_be_enabled()
    if outcome != "dry-run":
        page.locator('[data-replace-input="dry-run-step3"]').uncheck()
    before = database_observation(snapshot_with_drives)
    fake_storcli.clear()
    with page.expect_response("**/replace/insert") as response:
        run.click()
    assert response.value.status == (502 if outcome == "failure" else 200)
    assert fake_storcli.mutations == (
        []
        if outcome == "dry-run"
        else [
            ["/c0/e252/s0", "insert", "dg=0", "array=0", "row=0", "J"],
        ]
    )
    if outcome == "dry-run":
        assert fake_storcli.calls == []
        assert database_observation(snapshot_with_drives) == before
        expect(page.locator("[data-replace-output]")).to_contain_text("dry_run")
    elif outcome == "failure":
        expect(page.locator("[data-replace-output]")).to_contain_text("storcli command failed")
    else:
        expect(page.locator('[data-stage="rebuild"]')).to_be_visible()
        page.locator('[data-stage="rebuild"] [data-replace-action="close"]').click()
        expect(page.locator('[data-replace-action="open"]')).to_be_visible()
    assert database_observation(snapshot_with_drives)[1] == before[1]


@pytest.mark.parametrize("stage", ["confirm", "physical-swap", "insert"])
def test_wizard_cancel_keeps_controller_and_database_unchanged(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    stage: str,
) -> None:
    page = authenticated_page
    # Offln allows both offline and missing validation without any live command.
    action = next(a for a in ACTIONS if a.name == "replace-missing")
    prepare(action, _live_server_handle, fake_storcli)
    login_and_navigate(page, f"{live_server}/drives/252:0")
    before = database_observation(snapshot_with_drives)
    if stage == "confirm":
        page.locator('[data-replace-action="open"]').click()
    else:
        open_wizard(page)
        page.locator('[data-replace-action="run-step1"]').click()
        expect(page.locator('[data-stage="physical-swap"]')).to_be_visible()
        if stage == "insert":
            page.locator('[data-replace-action="continue-to-insert"]').click()
            expect(page.locator('[data-stage="insert"]')).to_be_visible()
    page.locator(f'[data-stage="{stage}"] [data-replace-action="cancel"]').click()
    expect(page.locator('[data-replace-action="open"]')).to_be_focused()
    expect(page.locator("[data-replace-stepper]")).to_be_hidden()
    assert fake_storcli.calls == []
    assert database_observation(snapshot_with_drives) == before


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
