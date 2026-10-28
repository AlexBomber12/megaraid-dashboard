"""Rendered controls in uncommon drive/controller states, plus read-only interactions."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import update

from megaraid_dashboard.db import ControllerSnapshot, Event, PhysicalDriveSnapshot
from tests.e2e.conftest import _LiveServerHandle, login_and_navigate
from tests.safety.harness import FakeStorcli, database_observation
from tests.safety.scenarios import ACTIONS


@pytest.mark.parametrize(
    "state", ["Onln", "UGood", "UBad", "Offln", "Msng", "Failed", "Rbld", "Unknown"]
)
def test_drive_state_availability_and_refusal_explanations(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    state: str,
) -> None:
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        session.execute(
            update(PhysicalDriveSnapshot)
            .where(PhysicalDriveSnapshot.slot_id == 0)
            .values(state=state)
        )
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    expected = {
        "mark-ubad": False,
        "mark-ugood": state == "UBad",
        "spindown": state in {"UGood", "UBad"},
        "hotspare": state == "UGood",
    }
    for action in (a for a in ACTIONS if a.family == "advanced"):
        button = page.locator(f'form[action="{action.path}"] button')
        if expected[action.name]:
            expect(button).to_be_enabled()
        else:
            expect(button).to_be_disabled()
            label = button.inner_text()
            reasons = page.locator(".disabled-reasons")
            expect(reasons).to_contain_text(label)
            reason = reasons.locator("li").filter(has=page.locator("strong", has_text=label)).first
            expect(reason).to_contain_text("—")
            assert len(reason.inner_text().partition("—")[2].strip()) > 10
    known = {a.path for a in ACTIONS} | {"/maintenance/start", "/maintenance/stop"}
    rendered = page.locator("[hx-post], form[method=post]").evaluate_all(
        "els => els.map(el => el.getAttribute('hx-post') || el.getAttribute('action'))"
    )
    assert set(rendered) <= known
    # Configuration is allowed as a grouping; claims of safety/read-only are not.
    for control in page.locator("[hx-post], form[method=post]").all():
        labels = control.evaluate("""el => Array.from(
            el.closest('section').querySelectorAll('h2,h3,.text-label'))
            .map(label => label.textContent).join(' ')""")
        assert not re.search(r"\b(?:safe|read.only)\b", labels, re.IGNORECASE), labels
    assert fake_storcli.calls == []


@pytest.mark.parametrize("alarm", ["On", "Off", "Unknown"])
def test_buzzer_availability(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    alarm: str,
) -> None:
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        session.execute(update(ControllerSnapshot).values(alarm_state=alarm))
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    # A state toggle already in that state must not invite a redundant command.
    if alarm == "On":
        expect(page.locator('[data-buzzer-action="enable"] button')).to_be_disabled()
    elif alarm == "Off":
        expect(page.locator('[data-buzzer-action="disable"] button')).to_be_disabled()
    assert fake_storcli.calls == []


def test_read_only_controls_never_post_or_mutate(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    page = authenticated_page
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        now = datetime.now(UTC)
        for index in range(65):
            session.add(
                Event(
                    occurred_at=now - timedelta(minutes=index),
                    severity="warning",
                    category="drive_state",
                    subject="drive 252:0",
                    summary=f"test event {index}",
                )
            )
    before = database_observation(snapshot_with_drives)
    posts = []
    page.on(
        "request", lambda request: posts.append(request.url) if request.method == "POST" else None
    )
    login_and_navigate(page, f"{live_server}/")
    nav = page.locator('nav[aria-label="Primary navigation"]')
    for name in ("Controller", "Drives", "Events", "Overview"):
        nav.get_by_text(name, exact=True).click()
        expect(nav.get_by_text(name, exact=True)).to_have_attribute("aria-current", "page")
    page.goto(f"{live_server}/drives")
    table = page.locator("#physical-drives-table")
    for button in table.locator(".sort-button").all():
        initial = button.locator("..").get_attribute("aria-sort")
        next_direction = "descending" if initial == "ascending" else "ascending"
        button.click()
        expect(button.locator("..")).to_have_attribute("aria-sort", next_direction)
        button.click()
        expect(button.locator("..")).to_have_attribute(
            "aria-sort", "ascending" if next_direction == "descending" else "descending"
        )
    page.goto(f"{live_server}/drives/252:0")
    expect(page.get_by_label("Previous drive")).to_have_attribute("aria-disabled", "true")
    page.get_by_label("Next drive").click()
    page.wait_for_url("**/drives/252:1", wait_until="load")
    page.get_by_label("Previous drive").click()
    page.wait_for_url("**/drives/252:0", wait_until="load")
    for days in (7, 30, 365):
        with page.expect_response("**/charts?*") as response:
            page.get_by_role("tab", name=f"{days}d", exact=True).click()
        assert response.value.status == 200
    page.goto(f"{live_server}/controller")
    identity = page.locator("#hardware-identity")
    identity.locator("summary").click()
    expect(identity.locator("dl")).to_be_visible()
    identity.locator("summary").click()
    expect(identity.locator("dl")).to_be_hidden()
    for name in ("1h", "24h", "7d", "30d"):
        with page.expect_response("**/roc-history?*") as response:
            page.get_by_role("tab", name=name, exact=True).click()
        assert response.value.status == 200
        expect(page.get_by_role("tab", name=name, exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
    page.goto(f"{live_server}/events")
    page.get_by_role("button", name="Load Older Events").click()
    expect(page.locator("[data-event-id]")).to_have_count(65)
    chips = page.locator(
        'nav[aria-label="Event filters"] .filter-chips__group:not([aria-label="Quick filters"]) a'
    )
    # Every filter is a GET navigation; toggle on and off, preserving a working page.
    for index in range(chips.count()):
        chip = chips.nth(index)
        chip.click()
        expect(chips.nth(index)).to_have_attribute("aria-pressed", "true")
        chips.nth(index).click()
        expect(chips.nth(index)).to_have_attribute("aria-pressed", "false")
    page.get_by_role("button", name="Refresh now").click()
    assert posts == []
    assert fake_storcli.calls == []
    assert database_observation(snapshot_with_drives) == before


def test_hot_spare_without_disk_group_explains_refusal(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        session.execute(
            update(PhysicalDriveSnapshot)
            .where(PhysicalDriveSnapshot.slot_id == 0)
            .values(state="UGood", disk_group_id=None)
        )
    login_and_navigate(authenticated_page, f"{live_server}/drives/252:0")
    expect(authenticated_page.get_by_role("button", name="Hot Spare")).to_be_disabled()
    expect(authenticated_page.locator(".disabled-reasons")).to_contain_text("No disk group")
    assert fake_storcli.calls == []


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
