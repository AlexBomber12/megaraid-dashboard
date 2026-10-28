"""End-to-end coverage for the redesigned controller detail page."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import assert_chart_height_within, login_and_navigate


def test_controller_detail_sections_and_roc_chart(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    maintenance_mode: None,
) -> None:
    """Exercise controller sections and chart range tabs; buzzer actions have their own matrix."""
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
    assert_chart_height_within(authenticated_page, "#roc-history-chart", 280)


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
