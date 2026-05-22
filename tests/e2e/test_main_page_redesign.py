"""End-to-end coverage for the redesigned main dashboard page."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.conftest import login_and_navigate, wait_for_htmx_swap


def test_main_page_redesign_navigation_and_polling(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    """Exercise the redesigned overview, drive-grid navigation, and HTMX polling."""
    del snapshot_with_drives
    login_and_navigate(authenticated_page, f"{live_server}/")

    primary_nav = authenticated_page.locator("nav[aria-label='Primary navigation'] a")
    expect(primary_nav).to_have_count(5)
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
    expect(authenticated_page.locator(".status-bar[role='status']")).to_be_visible()

    with authenticated_page.expect_response(
        lambda fetched: "/partials/main-page" in fetched.url and fetched.status == 200,
        timeout=45_000,
    ) as response_info:
        wait_for_htmx_swap(authenticated_page, timeout=45_000)
    response = response_info.value
    assert "Drive backplane" in response.text()
