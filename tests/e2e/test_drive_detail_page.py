"""End-to-end coverage for the redesigned drive detail page."""

from __future__ import annotations

from playwright.sync_api import Dialog, Page, expect
from sqlalchemy import select

from megaraid_dashboard.db import Event, get_engine, get_sessionmaker
from tests.e2e.conftest import assert_chart_height_within, login_and_navigate


def _operator_event_exists(database_url: str, summary_fragment: str) -> bool:
    engine = get_engine(database_url)
    try:
        session_factory = get_sessionmaker(engine)
        with session_factory() as session:
            return (
                session.scalar(
                    select(Event.id)
                    .where(Event.category == "operator_action")
                    .where(Event.summary.contains(summary_fragment))
                    .limit(1)
                )
                is not None
            )
    finally:
        engine.dispose()


def test_drive_detail_navigation_charts_actions_and_replace(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    """Exercise drive detail navigation, charts, locate action, and replacement wizard entry."""
    login_and_navigate(authenticated_page, f"{live_server}/drives/252:0")

    breadcrumb = authenticated_page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb.get_by_text("Overview")).to_be_visible()
    expect(breadcrumb.get_by_text("Drives")).to_be_visible()
    expect(breadcrumb.get_by_text("Slot 0")).to_be_visible()

    authenticated_page.get_by_label("Next drive").click()
    authenticated_page.wait_for_url("**/drives/252:1")
    authenticated_page.get_by_label("Previous drive").click()
    authenticated_page.wait_for_url("**/drives/252:0")

    expect(authenticated_page.locator("svg.error-sparkline")).to_be_visible()
    backplane_slots = authenticated_page.locator(".backplane-diagram .backplane-slot")
    expect(backplane_slots).to_have_count(8)
    expect(backplane_slots.filter(has_text="S0")).to_have_attribute("aria-current", "page")

    assert_chart_height_within(authenticated_page, "#temperature-history-chart", 280)

    authenticated_page.on("dialog", lambda dialog: _accept_dialog(dialog))
    with authenticated_page.expect_response(
        lambda response: "/drives/252:0/locate/start" in response.url and response.status == 200
    ):
        authenticated_page.get_by_role("button", name="Start locate").click()
    assert _operator_event_exists(snapshot_with_drives, "locate start drive 252:0")

    mark_ubad = authenticated_page.get_by_role("button", name="Mark as UBad")
    expect(mark_ubad).to_be_disabled()

    authenticated_page.get_by_role("button", name="Begin Replacement").click()
    expect(authenticated_page.locator("[data-stage='confirm']")).to_be_visible()


def _accept_dialog(dialog: Dialog) -> None:
    dialog.accept()
