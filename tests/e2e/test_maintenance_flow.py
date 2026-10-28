"""Browser-only maintenance flow over HTTPS; production JS supplies CSRF."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import login_and_navigate
from tests.safety.harness import FakeStorcli, database_observation


def start_maintenance(page: Page, live_server: str) -> None:
    login_and_navigate(page, f"{live_server}/controller")
    page.get_by_label("Reason (required)").fill("isolated safety test")
    page.get_by_label("Duration").select_option("15")
    with page.expect_response("**/maintenance/start") as response:
        page.get_by_role("button", name="Start Maintenance").click()
    assert response.value.status == 200
    expect(page.locator(".maintenance-banner")).to_be_visible()


def test_maintenance_start_stop_persists_and_refreshes(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
) -> None:
    page = authenticated_page
    start_maintenance(page, live_server)
    page.reload()
    expect(page.get_by_role("button", name="Stop Maintenance")).to_have_count(2)
    events, _ = database_observation(snapshot_with_drives)
    assert any(
        "maintenance start" in summary and "isolated safety test" in summary
        for _, summary in events
    )
    with page.expect_response("**/maintenance/stop") as response:
        page.locator("#maintenance").get_by_role("button", name="Stop Maintenance").click()
    assert response.value.status == 200
    expect(page.get_by_role("button", name="Start Maintenance")).to_be_visible()
    page.reload()
    expect(page.locator(".maintenance-banner")).to_have_count(0)
    events, _ = database_observation(snapshot_with_drives)
    assert any("maintenance stop" in summary for _, summary in events)
    assert fake_storcli.calls == []


def test_maintenance_blank_reason_does_not_submit(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
    fake_storcli: FakeStorcli,
) -> None:
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    posts = []
    page.on(
        "request", lambda request: posts.append(request.url) if request.method == "POST" else None
    )
    page.get_by_role("button", name="Start Maintenance").click()
    assert page.get_by_label("Reason (required)").evaluate("el => !el.validity.valid")
    assert posts == []
    assert database_observation(fresh_db)[0] == []
    assert fake_storcli.calls == []


def test_ui_maintenance_window_authorizes_controller_action(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
) -> None:
    page = authenticated_page
    start_maintenance(page, live_server)
    page.goto(f"{live_server}/controller")
    with page.expect_response("**/controller/buzzer/silence") as response:
        page.locator('[data-buzzer-action="silence"] button').click()
    assert response.value.status == 303, "Starting UI maintenance must authorize controller writes"
    assert fake_storcli.mutations == [["/c0", "set", "alarm=silence", "J"]]


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
