"""Locate regression assertions remain strict while the separate product fix lands."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import login_and_navigate
from tests.safety.harness import FakeStorcli, database_observation


@pytest.mark.parametrize("action", ["start", "stop"])
@pytest.mark.parametrize("fault", ["success", "failure"])
def test_locate_browser_result(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
    action: str,
    fault: str,
) -> None:
    page = authenticated_page
    fake_storcli.configure(fault=fault)
    login_and_navigate(page, f"{live_server}/drives/252:0")
    before = database_observation(snapshot_with_drives)
    button = page.locator(f'[data-locate-action="{action}"]')
    # Stop must be available after starting, rather than manufacturing browser state.
    if action == "stop":
        fake_storcli.configure(fault="success")
        with page.expect_response("**/locate/start"):
            page.locator('[data-locate-action="start"]').click()
        fake_storcli.clear()
        fake_storcli.configure(fault=fault)
    expect(button).to_be_enabled(timeout=1500)
    with page.expect_response(f"**/locate/{action}") as response:
        button.click()
    assert fake_storcli.mutations == [["/c0/e252/s0", action, "locate", "J"]]
    assert response.value.status == (200 if fault == "success" else 502)
    feedback = page.locator("[data-action-feedback]")
    expect(feedback).to_contain_text(
        "accepted" if fault == "success" else "fail", ignore_case=True, timeout=1500
    )
    expect(feedback).to_contain_text("unverified", ignore_case=True, timeout=1500)
    if fault == "success" and action == "start":
        expect(button).to_be_disabled()
    else:
        expect(button).to_be_enabled()
    expect(page.locator('[data-locate-action="stop"]')).to_be_enabled()
    events, drives = database_observation(snapshot_with_drives)
    assert drives == before[1]
    assert events and (fault == "success" or "fail" in events[-1][1].lower())
    page.reload()
    expect(page.locator(".drive-detail-v2")).to_have_count(1)
    if fault == "success" and action == "start":
        expect(page.locator('[data-locate-action="start"]')).to_be_disabled()
        expect(page.locator('[data-locate-action="stop"]')).to_be_enabled()
