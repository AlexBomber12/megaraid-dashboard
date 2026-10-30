"""Real Secure cookie and browser-generated CSRF; tests never synthesize a valid header."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import login_and_navigate
from tests.e2e.test_maintenance_flow import start_maintenance
from tests.safety.harness import FakeStorcli, database_observation


def test_secure_csrf_cookie_and_browser_post(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    assert live_server.startswith("https://")
    start_maintenance(authenticated_page, live_server)
    assert database_observation(fresh_db)[0]
    cookie = next(c for c in authenticated_page.context.cookies() if c["name"] == "__Host-csrf")
    assert cookie["secure"] and cookie["path"] == "/" and cookie["sameSite"] == "Strict"
    assert cookie["domain"] == "127.0.0.1"


@pytest.mark.parametrize("failure", ["missing-cookie", "missing-header", "mismatch"])
def test_csrf_refusal_from_browser_click(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
    fake_storcli: FakeStorcli,
    failure: str,
) -> None:
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    before = database_observation(fresh_db)
    page.get_by_label("Maintenance reason").fill("must be refused")
    if failure == "missing-cookie":
        page.context.clear_cookies()
    else:

        def break_header(route):
            headers = dict(route.request.headers)
            headers.pop("x-csrf-token", None)
            if failure == "mismatch":
                headers["x-csrf-token"] = "invalid"
            route.continue_(headers=headers)

        page.route("**/maintenance/start", break_header)
    with page.expect_response("**/maintenance/start") as response:
        page.get_by_role("button", name="Start Maintenance").click()
    assert response.value.status == 403
    assert "csrf token missing or mismatched" in response.value.text()
    expect(page.get_by_role("button", name="Start Maintenance")).to_be_enabled()
    assert fake_storcli.calls == []
    assert database_observation(fresh_db) == before
    # Product requirement: refusals must be visible, including HTMX 403 responses.
    expect(page.get_by_role("alert")).to_contain_text("csrf")


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
