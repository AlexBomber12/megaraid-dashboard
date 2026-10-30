"""Browser proofs for the independent audit, through real full-capability gates."""

import json

import pytest
from playwright.sync_api import expect
from sqlalchemy import update

from megaraid_dashboard.db import PhysicalDriveSnapshot
from tests.e2e.conftest import login_and_navigate
from tests.e2e.test_replace_wizard_flow import open_wizard
from tests.safety.harness import prepare
from tests.safety.scenarios import ACTIONS

pytestmark = pytest.mark.usefixtures("full_controller_handlers")


@pytest.mark.parametrize("name", ["replace-offline", "replace-missing"])
def test_unobserved_replacement_state_never_shows_swap_instructions(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli, name
):
    action = next(item for item in ACTIONS if item.name == name)
    prepare(action, _live_server_handle, fake_storcli)
    fake_storcli.configure(fault="success-no-effect")
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    open_wizard(page)
    page.locator('[data-replace-input="dry-run"]').uncheck()
    page.locator('[data-replace-action="run-step1"]').click()
    expect(page.locator("[data-replace-output]")).to_contain_text("uncertain")
    expect(page.locator('[data-stage="physical-swap"]')).to_be_hidden()
    assert len(fake_storcli.mutations) == (1 if name == "replace-offline" else 2)


def test_reload_resumes_insertion_without_repeating_offline_missing(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli
):
    action = next(item for item in ACTIONS if item.name == "replace-offline")
    prepare(action, _live_server_handle, fake_storcli)
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    open_wizard(page)
    page.locator('[data-replace-input="dry-run"]').uncheck()
    page.locator('[data-replace-action="run-step1"]').click()
    expect(page.locator('[data-stage="physical-swap"]')).to_be_visible()
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        session.execute(
            update(PhysicalDriveSnapshot)
            .where(PhysicalDriveSnapshot.slot_id == 0)
            .values(serial_number="NEW-DRIVE", state="UGood")
        )
    fake_storcli.configure(serial="NEW-DRIVE", drive_state="UGood")
    page.reload()
    fake_storcli.clear()
    page.get_by_role("button", name="Resume…", exact=True).click()
    expect(page.locator('[data-stage="insert"]')).to_be_visible()
    page.locator('[data-replace-input="new-serial"]').fill("NEW-DRIVE")
    page.locator('[data-replace-input="dry-run-step3"]').uncheck()
    page.locator('[data-replace-action="run-step3"]').click()
    expect(page.locator('[data-stage="rebuild"]')).to_be_visible()
    page.locator('[data-stage="rebuild"] [data-replace-action="close"]').click()
    page.get_by_role("button", name="View Rebuild", exact=True).click()
    expect(page.locator('[data-stage="rebuild"]')).to_be_visible()
    assert fake_storcli.mutations == [["/c0/e252/s0", "insert", "dg=0", "array=0", "row=0", "J"]]


@pytest.mark.parametrize("name", ["foreign-import", "foreign-clear"])
def test_reviewed_page_cannot_mutate_different_same_size_foreign_metadata(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli, name
):
    action = next(item for item in ACTIONS if item.name == name)
    body = prepare(action, _live_server_handle, fake_storcli)
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller/foreign-config")
    panel = page.locator(f"#foreign-{name.split('-')[-1]}")
    page.locator(f'[data-row-toggle][aria-controls="foreign-{name.split("-")[-1]}"]').click()
    panel.locator('[name="confirmation"]').fill(body["confirmation"])
    panel.locator('[name="dry_run"]').uncheck()
    state = json.loads((fake_storcli.path.parent / "state.json").read_text())
    state["foreign"]["FOREIGN PD LIST"][0]["DID"] = 999
    fake_storcli.configure(foreign=state["foreign"])
    with page.expect_response(lambda response: response.url.endswith(action.path)) as response:
        panel.locator("button").click()
    assert response.value.status == 409
    expect(panel.locator("pre")).to_contain_text(
        "mismatch" if name == "foreign-import" else "changed"
    )
    assert fake_storcli.mutations == []


@pytest.mark.parametrize("denied", ["start", "stop"])
def test_locate_feedback_preserves_independent_command_approval(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    full_controller_handlers,
    denied,
):
    action = next(item for item in ACTIONS if item.name == "locate-start")
    prepare(action, _live_server_handle, fake_storcli)
    policy = json.loads(full_controller_handlers.read_text())
    policy["commands"] = [
        item for item in policy["commands"] if item["command"] != f"/c0/e252/s0 {denied} locate J"
    ]
    full_controller_handlers.write_text(json.dumps(policy))
    fake_storcli.configure(fault="failure")
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    allowed = "stop" if denied == "start" else "start"
    expect(page.locator(f"[data-locate-action={denied}]")).to_be_disabled()
    page.locator(f"[data-locate-action={allowed}]").click()
    expect(page.locator("[data-action-feedback]")).to_contain_text("not confirmed")
    expect(page.locator(f"[data-locate-action={denied}]")).to_be_disabled()


def test_all_read_only_controls_with_full_authorization(
    authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli
):
    from tests.e2e.test_control_availability import test_read_only_controls_never_post_or_mutate

    test_read_only_controls_never_post_or_mutate(
        authenticated_page, live_server, snapshot_with_drives, _live_server_handle, fake_storcli
    )


def test_maintenance_start_stop_with_full_authorization(
    authenticated_page, live_server, snapshot_with_drives, fake_storcli
):
    from tests.e2e.test_maintenance_flow import test_maintenance_start_stop_persists_and_refreshes

    test_maintenance_start_stop_persists_and_refreshes(
        authenticated_page, live_server, snapshot_with_drives, fake_storcli
    )
