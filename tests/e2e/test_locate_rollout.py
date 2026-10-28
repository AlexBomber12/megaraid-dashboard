"""Browser acceptance of the real locate-only policy, never the legacy bypass."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import login_and_navigate


@pytest.mark.parametrize("capability", ["none", "locate"])
@pytest.mark.parametrize("active", [False, True])
def test_locate_only_controls(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    capability,
    active,
):
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(
        update={
            "controller_read_only": False,
            "controller_action_capability": capability,
            "maintenance_mode": active,
            "destructive_mode": True,
        }
    )
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    start = page.locator('[data-locate-action="start"]')
    stop = page.locator('[data-locate-action="stop"]')
    if capability == "locate" and active:
        expect(start).to_be_enabled()
        with page.expect_response("**/locate/start") as response:
            start.click()
        assert response.value.status == 200
        expect(stop).to_be_enabled()
        with page.expect_response("**/locate/stop") as response:
            stop.click()
        assert response.value.status == 200
        assert fake_storcli.mutations == [
            ["/c0/e252/s0", "start", "locate", "J"],
            ["/c0/e252/s0", "stop", "locate", "J"],
        ]
    else:
        expect(start).to_be_disabled()
        expect(stop).to_be_disabled()
    for button in page.locator('[data-replace-action="open"], form[method=post] button').all():
        expect(button).to_be_disabled()
    expect(page.locator("main")).to_contain_text("Only Locate Start/Stop is supported")
    login_and_navigate(page, f"{live_server}/controller")
    for button in page.locator("main form[method=post] button").all():
        expect(button).to_be_disabled()
    expect(page.locator("main")).to_contain_text("Only Locate Start/Stop is supported")
    login_and_navigate(page, f"{live_server}/controller/foreign-config")
    for button in page.locator("main form[method=post] button").all():
        expect(button).to_be_disabled()
