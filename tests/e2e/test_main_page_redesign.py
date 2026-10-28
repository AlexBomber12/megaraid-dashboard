"""End-to-end coverage for the redesigned main dashboard page."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import login_and_navigate, wait_for_live_update


def test_main_page_redesign_navigation_and_live_refresh(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    """Exercise the redesigned overview, drive-grid navigation, and live refresh."""
    del snapshot_with_drives
    login_and_navigate(authenticated_page, f"{live_server}/")

    primary_nav = authenticated_page.locator("nav[aria-label='Primary navigation'] a")
    expect(primary_nav).to_have_text(["Overview", "Controller", "Drives", "Events"])
    expect(primary_nav.filter(has_text="Overview")).to_have_attribute("aria-current", "page")

    controller_card = authenticated_page.locator(".controller-card")
    expect(controller_card).to_be_visible()
    expect(controller_card.locator(".controller-state")).to_have_text("OPTIMAL")
    expect(controller_card.locator(".controller-card-row2 .metric-item")).to_have_count(4)

    drive_tiles = authenticated_page.locator(".drive-tile-v2")
    expect(drive_tiles).to_have_count(8)
    drive_tiles.first.click()
    authenticated_page.wait_for_url("**/drives/252:0")

    authenticated_page.goto(f"{live_server}/")
    authenticated_page.locator("nav[aria-label='Primary navigation']").get_by_text(
        "Controller",
        exact=True,
    ).click()
    authenticated_page.wait_for_url("**/controller")

    authenticated_page.goto(f"{live_server}/")
    expect(authenticated_page.locator("footer.status-bar .status-bar-v2")).to_be_visible()
    live_status = authenticated_page.locator("[data-live-status]")
    expect(live_status).to_have_attribute("data-state", "live")
    expect(live_status).to_contain_text("Live · ")

    # The 30 s timer re-fetches the page and swaps the live regions in place.
    authenticated_page.evaluate("document.getElementById('overview-live').dataset.stale = '1'")
    with authenticated_page.expect_response(
        lambda fetched: fetched.url == f"{live_server}/" and fetched.status == 200,
        timeout=45_000,
    ):
        wait_for_live_update(authenticated_page, timeout=45_000)
    expect(authenticated_page.locator("#overview-live")).not_to_have_attribute("data-stale", "1")
    expect(authenticated_page.locator("#overview-live .drive-tile-v2")).to_have_count(8)
    expect(live_status).to_have_attribute("data-state", "live")

    # The header button forces an immediate refresh.
    with authenticated_page.expect_response(
        lambda fetched: fetched.url == f"{live_server}/" and fetched.status == 200,
    ):
        authenticated_page.get_by_role("button", name="Refresh now").click()
    expect(live_status).to_contain_text("Live · ")


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
