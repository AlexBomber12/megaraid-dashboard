"""Full capability acceptance through real Chromium/API gates and fake hardware."""

import json

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import login_and_navigate
from tests.safety.scenarios import ACTIONS

pytestmark = pytest.mark.usefixtures("full_controller_handlers")


def test_unapproved_syntax_is_disabled(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    fake_storcli,
    full_controller_handlers,
):
    data = json.loads(full_controller_handlers.read_text())
    data["commands"] = [item for item in data["commands"] if "locate" not in item["command"]]
    full_controller_handlers.write_text(json.dumps(data))
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    expect(page.locator("[data-locate-action=stop]")).to_be_disabled()
    expect(page.locator("body")).to_contain_text("syntax or topology is not approved")
    assert fake_storcli.calls == []


def test_online_spin_down_is_disabled(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    fake_storcli,
):
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    expect(page.get_by_role("button", name="Spin Down", exact=True)).to_be_disabled()
    expect(page.locator("body")).to_contain_text("Only unconfigured UGood or UBad")
    expect(page.get_by_role("button", name="Mark as UBad", exact=True)).to_be_disabled()
    assert fake_storcli.calls == []


@pytest.mark.parametrize(
    "action,outcome",
    [
        (action, outcome)
        for action in ACTIONS
        if action.name != "replace-missing"
        for outcome in ("success", "failure")
        if action.name != "mark-ubad" or outcome == "success"
    ],
    ids=lambda value: value.name if hasattr(value, "name") else value,
)
def test_full_browser_action_matrix(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    action,
    outcome,
):
    # Reuse established click/confirmation/audit scenarios through real full-capability
    # gates. No legacy gate fixture, DOM injection or request interception is active.
    from tests.e2e import (
        test_buzzer_control_flow as buzzer,
    )
    from tests.e2e import (
        test_controller_operations as operations,
    )
    from tests.e2e import (
        test_drive_detail_page as drives,
    )
    from tests.e2e import (
        test_foreign_configuration as foreign,
    )
    from tests.e2e import (
        test_locate_flow as locate,
    )
    from tests.e2e import (
        test_replace_wizard_flow as replacement,
    )
    from tests.safety.harness import prepare

    common = (authenticated_page, live_server, snapshot_with_drives)
    extra = (_live_server_handle, fake_storcli)
    if action.family == "locate":
        prepare(action, _live_server_handle, fake_storcli)
        locate.test_locate_browser_result(
            *common, fake_storcli, action.name.split("-")[-1], outcome, None
        )
    elif action.family == "buzzer":
        buzzer.test_buzzer_control_flow(*common, *extra, action, outcome)
    elif action.family in {"patrol-read", "consistency-check"}:
        operations.test_controller_operation_browser_matrix(*common, *extra, action, outcome)
    elif action.family == "advanced":
        drives.test_advanced_action_browser_matrix(*common, *extra, action, outcome)
    elif action.family == "foreign":
        foreign.test_foreign_action_browser(*common, *extra, action, outcome)
    elif action.name == "replace-insert":
        replacement.test_replacement_insert_browser(*common, *extra, outcome)
    else:
        replacement.test_replace_offline_missing_browser(*common, *extra, outcome)


@pytest.mark.parametrize("step", ["offline", "missing", "insert"])
def test_incomplete_replacement_policy_disables_whole_wizard(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    full_controller_handlers,
    step,
):
    from tests.safety.harness import prepare

    action = next(item for item in ACTIONS if item.name == "replace-offline")
    prepare(action, _live_server_handle, fake_storcli)
    data = json.loads(full_controller_handlers.read_text())
    tail = f"set {step} J" if step != "insert" else "insert dg=0 array=0 row=0 J"
    data["commands"] = [
        item for item in data["commands"] if item["command"] != f"/c0/e252/s0 {tail}"
    ]
    full_controller_handlers.write_text(json.dumps(data))
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    expect(page.get_by_role("button", name="Begin Replacement", exact=True)).to_be_disabled()
    expect(page.locator("body")).to_contain_text(
        f"{step.title()} command or topology is not approved"
    )
    assert fake_storcli.calls == []


@pytest.mark.parametrize("approve_next", [False, True])
@pytest.mark.parametrize("name", ["consistency-check-start", "patrol-read-mode"])
def test_selected_ui_option_has_independent_approval(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    full_controller_handlers,
    approve_next,
    name,
):
    from sqlalchemy import select

    from megaraid_dashboard.db import ControllerSnapshot
    from tests.safety.harness import prepare

    action = next(item for item in ACTIONS if item.name == name)
    prepare(action, _live_server_handle, fake_storcli)
    data = json.loads(full_controller_handlers.read_text())
    alternatives = (
        {"/c0/vall start cc J", "/c0/v0 start cc J"}
        if name == "consistency-check-start"
        else {
            "/c0 set patrolread=on mode=auto J",
            "/c0 set patrolread=on mode=manual J",
            "/c0 set patrolread=off J",
        }
    )
    selected = (
        "/c0/vall start cc J"
        if name == "consistency-check-start"
        else "/c0 set patrolread=on mode=auto J"
    )
    other = (
        "/c0/v0 start cc J"
        if name == "consistency-check-start"
        else "/c0 set patrolread=on mode=manual J"
    )
    data["commands"] = [
        item
        for item in data["commands"]
        if item["command"] not in alternatives
        or item["command"] == (selected if approve_next else other)
    ]
    full_controller_handlers.write_text(json.dumps(data))
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.raw_json = {
            **snapshot.raw_json,
            action.family.replace("-", "_"): {"mode": "manual", "state": "stopped"},
        }
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    button = page.locator(f'form[action="{action.path}"] button')
    if approve_next:
        expect(button).to_be_enabled()
        with page.expect_response(lambda response: response.url.endswith(action.path)) as response:
            button.click()
        assert response.value.status == 200
        assert fake_storcli.mutations == [selected.split()]
    else:
        expect(button).to_be_disabled()
        assert fake_storcli.calls == []


@pytest.mark.parametrize("absent", ["offline", "missing", "insert"])
def test_direct_missing_cannot_bypass_the_disabled_replacement_wizard(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    full_controller_handlers,
    absent,
):
    # The API matrix covers direct POSTs; this proves each refusal is visible before a click.
    from tests.safety.harness import prepare

    action = next(item for item in ACTIONS if item.name == "replace-missing")
    prepare(action, _live_server_handle, fake_storcli)
    data = json.loads(full_controller_handlers.read_text())
    token = f"set {absent}" if absent != "insert" else "insert dg="
    data["commands"] = [item for item in data["commands"] if token not in item["command"]]
    full_controller_handlers.write_text(json.dumps(data))
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    expect(page.get_by_role("button", name="Begin Replacement", exact=True)).to_be_disabled()
    expect(page.locator("body")).to_contain_text(
        f"{absent.title()} command or topology is not approved"
    )
    assert fake_storcli.calls == []


def test_foreign_recovery_browser_without_any_local_virtual_drive(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
):
    from sqlalchemy import delete

    from megaraid_dashboard.db import VirtualDriveSnapshot
    from tests.e2e import test_foreign_configuration as foreign

    with _live_server_handle.app.state.session_factory() as session, session.begin():
        session.execute(delete(VirtualDriveSnapshot))
    config = json.loads((fake_storcli.path.parent / "state.json").read_text())
    config["controller"].update(
        {"Virtual Drives": 0, "VD LIST": [], "Drive Groups": 0, "TOPOLOGY": []}
    )
    fake_storcli.configure(controller=config["controller"])
    action = next(item for item in ACTIONS if item.name == "foreign-import")
    foreign.test_foreign_action_browser(
        authenticated_page,
        live_server,
        snapshot_with_drives,
        _live_server_handle,
        fake_storcli,
        action,
        "success",
    )
