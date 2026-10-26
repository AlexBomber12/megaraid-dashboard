"""Every buzzer control: browser submission, refusal feedback, exact argv and audit."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import _LiveServerHandle, login_and_navigate
from tests.safety.harness import FakeStorcli, database_observation, prepare
from tests.safety.scenarios import ACTIONS, Action


@pytest.mark.parametrize(
    "action", [a for a in ACTIONS if a.family == "buzzer"], ids=lambda a: a.name
)
@pytest.mark.parametrize("outcome", ["success", "failure", "maintenance"])
def test_buzzer_control_flow(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    action: Action,
    outcome: str,
) -> None:
    page = authenticated_page
    prepare(action, _live_server_handle, fake_storcli)
    if outcome == "maintenance":
        app = _live_server_handle.app
        app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": False})
    fake_storcli.configure(fault="failure" if outcome == "failure" else "success")
    login_and_navigate(page, f"{live_server}/controller")
    before = database_observation(snapshot_with_drives)
    button = page.locator(f'form[action="{action.path}"] button')
    expect(button).to_be_enabled()
    with page.expect_response(
        lambda response: response.url.endswith(action.path) and response.request.method == "POST"
    ) as response:
        button.click()
    expected = {"success": 303, "failure": 502, "maintenance": 403}[outcome]
    assert response.value.status == expected
    assert fake_storcli.mutations == ([] if outcome == "maintenance" else [list(action.command)])
    if outcome == "success":
        page.wait_for_url(f"{live_server}/")
        page.goto(f"{live_server}/events?category=controller_buzzer_{action.name.split('-')[-1]}")
        expect(page.locator("body")).to_contain_text("succeeded")
    else:
        expect(page.locator("[data-controller-action-error]:visible")).to_contain_text(
            "TEST refusal" if outcome == "failure" else "maintenance_mode"
        )
        expect(button).to_be_enabled()
    after = database_observation(snapshot_with_drives)
    assert after[1] == before[1]
    assert len(after[0]) == len(before[0]) + 1
    assert {"success": "succeeded", "failure": "failed", "maintenance": "rejected"}[
        outcome
    ] in after[0][-1][1]
