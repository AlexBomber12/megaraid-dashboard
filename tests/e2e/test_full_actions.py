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
