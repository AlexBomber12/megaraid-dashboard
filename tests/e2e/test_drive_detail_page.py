"""End-to-end coverage for the redesigned drive detail page."""

from __future__ import annotations

from playwright.sync_api import Page, Request, expect
from sqlalchemy import select, update

from megaraid_dashboard.db import Event, PhysicalDriveSnapshot, get_engine, get_sessionmaker
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
    expect(breadcrumb.get_by_text("Slot 252:0")).to_be_visible()

    authenticated_page.get_by_label("Next drive").click()
    authenticated_page.wait_for_url("**/drives/252:1")
    authenticated_page.get_by_label("Previous drive").click()
    authenticated_page.wait_for_url("**/drives/252:0")

    expect(authenticated_page.locator("svg.error-sparkline")).to_be_visible()
    backplane_slots = authenticated_page.locator(".backplane-diagram .backplane-slot")
    expect(backplane_slots).to_have_count(8)
    expect(backplane_slots.filter(has_text="S0")).to_have_attribute("aria-current", "page")

    assert_chart_height_within(authenticated_page, "#temperature-history-chart", 280)

    native_dialogs: list[str] = []
    authenticated_page.on("dialog", lambda dialog: native_dialogs.append(dialog.message))
    with authenticated_page.expect_response(
        lambda response: "/drives/252:0/locate/start" in response.url and response.status == 200
    ):
        authenticated_page.get_by_role("button", name="Blink Locate LED").click()
    assert native_dialogs == []
    assert _operator_event_exists(snapshot_with_drives, "locate start drive 252:0")

    mark_ubad = authenticated_page.get_by_role("button", name="Mark as UBad")
    expect(mark_ubad).to_be_disabled()

    authenticated_page.get_by_role("button", name="Begin Replacement").click()
    expect(authenticated_page.locator("[data-stage='confirm']")).to_be_visible()
    stepper = authenticated_page.locator("[data-replace-stepper]")
    expect(stepper).to_be_visible()
    expect(stepper.locator("li").first).to_have_attribute("aria-current", "step")
    expect(
        authenticated_page.locator("[data-stage='confirm'] [data-replace-action='cancel']")
    ).to_be_focused()

    authenticated_page.get_by_role("button", name="Continue").click()
    expect(authenticated_page.locator("[data-stage='serial']")).to_be_visible()
    authenticated_page.locator("[data-stage='serial'] [data-replace-action='cancel']").click()
    expect(authenticated_page.locator("[data-stage='serial']")).to_be_hidden()
    expect(stepper).to_be_hidden()
    expect(authenticated_page.get_by_role("button", name="Begin Replacement")).to_be_visible()
    # The page stays responsive after the wizard closes.
    assert authenticated_page.evaluate("1 + 1") == 2


def test_destructive_drive_action_requires_typed_slot(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    """Mark as UBad is only submitted after the operator types the drive's enclosure:slot."""
    _set_drive_state(snapshot_with_drives, slot_id=7, state="UGood")
    login_and_navigate(authenticated_page, f"{live_server}/drives/252:7")
    posted: list[str] = []

    def record(request: Request) -> None:
        if request.method == "POST" and "/actions/mark-ubad" in request.url:
            posted.append(request.url)

    authenticated_page.on("request", record)
    dialog = authenticated_page.locator("dialog.confirm-dialog")
    confirm = dialog.get_by_role("button", name="Mark as UBad")
    phrase_input = dialog.locator("[data-cd-input]")

    authenticated_page.get_by_role("button", name="Spin Down").click()
    expect(dialog).to_be_visible()
    expect(phrase_input).to_be_hidden()
    expect(dialog.get_by_role("button", name="Spin Down")).to_be_enabled()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()

    authenticated_page.get_by_role("button", name="Mark as UBad").click()
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Mark as UBad on drive 252:7?")
    expect(phrase_input).to_be_visible()
    expect(confirm).to_be_disabled()
    phrase_input.fill("252:6")
    expect(confirm).to_be_disabled()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert posted == []

    authenticated_page.get_by_role("button", name="Mark as UBad").click()
    expect(phrase_input).to_have_value("")
    phrase_input.fill("252:7")
    expect(confirm).to_be_enabled()
    with authenticated_page.expect_response(lambda response: "/actions/mark-ubad" in response.url):
        confirm.click()
    assert posted == [f"{live_server}/drives/252:7/actions/mark-ubad"]
    # Maintenance mode is off, so the server refuses and the page shows the error.
    expect(authenticated_page.locator("[data-drive-action-error]")).to_be_visible()


def _set_drive_state(database_url: str, *, slot_id: int, state: str) -> None:
    engine = get_engine(database_url)
    try:
        session_factory = get_sessionmaker(engine)
        with session_factory() as session, session.begin():
            session.execute(
                update(PhysicalDriveSnapshot)
                .where(PhysicalDriveSnapshot.slot_id == slot_id)
                .values(state=state)
            )
    finally:
        engine.dispose()
