"""Events navigation, legacy bookmarks, quick filters, pagination, and live polling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page, expect

from megaraid_dashboard.db import Event, get_engine, get_sessionmaker


def test_events_page_renders_with_authenticated_user(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    del snapshot_with_drives
    response = authenticated_page.goto(f"{live_server}/events")
    assert response is not None
    assert response.status == 200, f"expected 200, got {response.status}"
    content = authenticated_page.content().lower()
    assert "event" in content, "events page should render with events table or empty state"


OPERATOR_CATEGORIES = (
    "operator_action",
    "drive_mark_ubad",
    "drive_mark_ugood",
    "drive_spin_down",
    "drive_make_hot_spare",
    "controller_buzzer_silence",
    "controller_buzzer_disable",
    "controller_buzzer_enable",
)


def _add_events(database_url: str, *, start: int, count: int) -> None:
    engine = get_engine(database_url)
    try:
        with get_sessionmaker(engine)() as session, session.begin():
            for index in range(start, start + count):
                session.add(
                    Event(
                        occurred_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
                        severity="warning",
                        category=OPERATOR_CATEGORIES[index % len(OPERATOR_CATEGORIES)],
                        subject="Operator action",
                        summary=f"Action {index}: result=failed; controller busy",
                        operator_username="test-operator",
                    )
                )
            session.add(
                Event(
                    occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
                    severity="warning",
                    category="pd_state",
                    subject="drive 252:0",
                    summary="Unrelated drive event",
                )
            )
            session.add(
                Event(
                    occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
                    severity="info",
                    category="operator_action",
                    subject="Operator action",
                    summary="Info action: result=success",
                    operator_username="another-operator",
                )
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("entry", ["quick_filter", "legacy_bookmark"])
def test_operator_actions_preserve_filters_records_pagination_and_polling(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    entry: str,
) -> None:
    page = authenticated_page
    _add_events(snapshot_with_drives, start=0, count=57)
    page.clock.install()
    if entry == "legacy_bookmark":
        response = page.goto(f"{live_server}/audit")
        assert response is not None
        assert response.request.redirected_from is not None
        assert response.request.redirected_from.url == f"{live_server}/audit"
    else:
        page.goto(f"{live_server}/events")
        page.get_by_role("link", name="Operator actions", exact=True).click()

    nav = page.get_by_role("navigation", name="Primary navigation")
    expect(nav.get_by_role("link", name="Events", exact=True)).to_have_count(1)
    expect(nav.get_by_role("link", name="Audit", exact=True)).to_have_count(0)
    expect(nav.get_by_role("link", name="Events", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    expect(page).to_have_title("Events - MegaRAID Dashboard")
    expect(page.get_by_role("heading", name="Events", exact=True)).to_be_visible()
    quick_filter = page.get_by_role("link", name="Operator actions", exact=True)
    expect(quick_filter).to_have_attribute("aria-pressed", "true")
    assert parse_qs(urlsplit(page.url).query) == {"category": list(OPERATOR_CATEGORIES)}
    page.get_by_role("link", name="Warning", exact=True).click()
    filter_url = page.url
    expected_filters = {"category": list(OPERATOR_CATEGORIES), "severity": ["warning"]}
    assert parse_qs(urlsplit(filter_url).query) == expected_filters
    # Category toggles remain usable alongside the preset and severity filter.
    page.get_by_role("link", name="pd state", exact=True).click()
    expect(quick_filter).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#events-list")).to_contain_text("Unrelated drive event")
    quick_filter.click()
    assert page.url == filter_url
    page.reload()
    expect(quick_filter).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-event-id]")).to_have_count(50)

    with page.expect_response(lambda r: "before_id=" in r.url) as older:
        page.get_by_role("button", name="Load Older Events").click()
    query = parse_qs(urlsplit(older.value.url).query)
    for key, values in expected_filters.items():
        assert query[key] == values
    expect(page.locator("[data-event-id]")).to_have_count(57)
    expect(page.get_by_role("button", name="Load Older Events")).to_have_count(0)
    for index in range(57):
        row = page.locator("[data-event-id]").filter(has_text=f"Action {index}:")
        expect(row).to_contain_text(f"Action {index}: result=failed; controller busy")
        expect(row.locator(".event-actor")).to_have_text("by test-operator")
        timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)
        expect(row.locator("time")).to_have_attribute(
            "datetime", timestamp.isoformat().replace("+00:00", "Z")
        )
    expect(page.locator("#events-list")).not_to_contain_text("Unrelated drive event")
    expect(page.locator("#events-list")).not_to_contain_text("Info action")

    _add_events(snapshot_with_drives, start=57, count=1)
    # Exercise the actual HTMX every-30s poll without a wall-clock sleep.
    with page.expect_response(lambda r: "/partials/events?" in r.url and "since=" in r.url) as poll:
        page.clock.fast_forward(30_000)
    query = parse_qs(urlsplit(poll.value.url).query)
    for key, values in expected_filters.items():
        assert query[key] == values
    expect(page.locator("[data-event-id]")).to_have_count(58)
    expect(page.locator("#events-list").locator("li").first).to_contain_text("Action 57:")
    expect(page.locator("#events-list")).not_to_contain_text("Unrelated drive event")
    expect(page.locator("#events-list")).not_to_contain_text("Info action")
    assert page.url == filter_url
    expect(page.get_by_role("heading", name="Events", exact=True)).to_be_visible()

    page.get_by_role("link", name="All events", exact=True).click()
    assert page.url == f"{live_server}/events"
    expect(page.get_by_role("link", name="All events", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(quick_filter).to_have_attribute("aria-pressed", "false")
    expect(page.get_by_role("link", name="Warning", exact=True)).to_have_attribute(
        "aria-pressed", "false"
    )
    expect(page.locator("#events-list")).to_contain_text("Unrelated drive event")
    expect(page.locator("#events-list")).to_contain_text("Info action: result=success")
    page.get_by_role("button", name="Load Older Events").click()
    expect(page.locator("[data-event-id]")).to_have_count(62)


def test_empty_operator_filter_poll_keeps_events_title_and_reset(
    authenticated_page: Page, live_server: str, snapshot_with_drives: str
) -> None:
    page = authenticated_page
    page.clock.install()
    page.goto(f"{live_server}/audit")
    expect(page.get_by_role("heading", name="No events recorded yet.")).to_be_visible()
    filter_url = page.url
    _add_events(snapshot_with_drives, start=0, count=1)
    with page.expect_response(lambda r: "since=0" in r.url):
        page.clock.fast_forward(30_000)
    expect(page.locator("[data-event-id]")).to_have_count(2)
    expect(page.get_by_role("heading", name="Events", exact=True)).to_be_visible()
    expect(page).to_have_title("Events - MegaRAID Dashboard")
    expect(page.get_by_role("link", name="Operator actions", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(page.locator("#events-list")).not_to_contain_text("Unrelated drive event")
    assert page.url == filter_url
    page.get_by_role("link", name="All events", exact=True).click()
    expect(page.locator("[data-event-id]")).to_have_count(3)
    expect(page.locator("#events-list")).to_contain_text("Unrelated drive event")
