"""Explicit capability disables browser controls even during a maintenance window."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import _LiveServerHandle, login_and_navigate
from tests.safety.harness import FakeStorcli, prepare
from tests.safety.scenarios import ACTIONS


@pytest.mark.parametrize("path", ["/controller", "/drives/252:0", "/controller/foreign-config"])
def test_read_only_rollout_controls(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    path: str,
) -> None:
    prepare(
        next(a for a in ACTIONS if a.name == "foreign-import"), _live_server_handle, fake_storcli
    )
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(
        update={
            "controller_read_only": True,
            "maintenance_mode": True,
            "destructive_mode": True,
        }
    )
    page = authenticated_page
    posts = []
    page.on(
        "request", lambda request: posts.append(request.url) if request.method == "POST" else None
    )
    login_and_navigate(page, f"{live_server}{path}")
    expect(page.locator("main")).to_contain_text("Controller actions disabled: read-only rollout.")
    controls = page.locator(
        'form[method=post] button, [data-locate-action], [data-replace-action="open"]'
    )
    assert controls.count() > 0
    for button in controls.all():
        expect(button).to_be_disabled()
    # Client-side refresh and initialization must not re-enable descendants of the fieldset.
    page.locator("button[data-live-refresh]").click()
    for button in controls.all():
        expect(button).to_be_disabled()
    assert posts == []
    assert fake_storcli.mutations == []
