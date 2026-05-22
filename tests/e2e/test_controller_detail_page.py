"""End-to-end coverage for the redesigned controller detail page."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from sqlalchemy import select

from megaraid_dashboard.db import Event, get_engine, get_sessionmaker
from tests.e2e.conftest import login_and_navigate


def _event_category_exists(database_url: str, category: str) -> bool:
    engine = get_engine(database_url)
    try:
        session_factory = get_sessionmaker(engine)
        with session_factory() as session:
            return (
                session.scalar(select(Event.id).where(Event.category == category).limit(1))
                is not None
            )
    finally:
        engine.dispose()


def test_controller_detail_sections_buzzer_and_roc_chart(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    maintenance_mode: None,
) -> None:
    """Exercise controller sections, buzzer POST forms, audit events, and chart range tabs."""
    del maintenance_mode
    login_and_navigate(authenticated_page, f"{live_server}/controller")

    for section in (
        "health-snapshot",
        "live-operations",
        "cachevault",
        "roc-history",
        "raid-config",
        "scheduled-tasks",
        "hardware-identity",
        "buzzer-control",
        "foreign-config",
    ):
        expect(authenticated_page.locator(f"[data-controller-section='{section}']")).to_be_visible()

    expect(
        authenticated_page.locator("[data-controller-section='hardware-identity'] dl > div")
    ).to_have_count(18)

    with authenticated_page.expect_navigation(url="**/"):
        authenticated_page.locator("[data-buzzer-action='silence']").get_by_role(
            "button",
            name="Silence",
        ).click()
    assert _event_category_exists(snapshot_with_drives, "controller_buzzer_silence")

    login_and_navigate(authenticated_page, f"{live_server}/controller")
    with authenticated_page.expect_navigation(url="**/"):
        authenticated_page.locator("[data-buzzer-action='disable']").get_by_role(
            "button",
            name="Disable",
        ).click()
    assert _event_category_exists(snapshot_with_drives, "controller_buzzer_disable")

    login_and_navigate(authenticated_page, f"{live_server}/controller")
    with authenticated_page.expect_response(
        lambda response: (
            "/controller/roc-history" in response.url
            and "range_hours=168" in response.url
            and response.status == 200
        )
    ):
        authenticated_page.get_by_role("tab", name="7d").click()
    expect(authenticated_page.get_by_role("tab", name="7d")).to_have_attribute(
        "aria-selected",
        "true",
    )
