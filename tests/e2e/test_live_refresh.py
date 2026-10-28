"""Regression coverage for drive identity, asynchronous swaps, and keyboard focus."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, Route, expect
from sqlalchemy import update

from megaraid_dashboard.db import PhysicalDriveSnapshot, get_engine, get_sessionmaker
from tests.e2e.conftest import login_and_navigate

OLD_SERIAL = "WCA275678000"
NEW_SERIAL = "REPLACEMENT-000"


def _replace_disk(database_url: str) -> None:
    engine = get_engine(database_url)
    try:
        with get_sessionmaker(engine)() as session, session.begin():
            session.execute(
                update(PhysicalDriveSnapshot)
                .where(PhysicalDriveSnapshot.slot_id == 0)
                .values(serial_number=NEW_SERIAL, model="Replacement model", state="UGood")
            )
    finally:
        engine.dispose()


def _hold_refresh(page: Page) -> None:
    """Hold a real refresh response until the operator has interacted with the page."""
    page.evaluate(
        """() => {
          const originalFetch = window.fetch;
          window.fetch = async function (...args) {
            const response = await originalFetch(...args);
            if (args[0] === window.location.href) {
              window.fetch = originalFetch;
              await new Promise(resolve => { window.releaseRefresh = resolve; });
            }
            return response;
          };
          window.pendingRefresh = window.liveRefresh.refresh();
        }"""
    )
    page.wait_for_function("typeof window.releaseRefresh === 'function'")


def _release_refresh(page: Page) -> None:
    page.evaluate("async () => { window.releaseRefresh(); await window.pendingRefresh; }")


def test_disk_change_reloads_identity_and_initializes_new_wizard(
    authenticated_page: Page, live_server: str, snapshot_with_drives: str
) -> None:
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    _replace_disk(snapshot_with_drives)

    with page.expect_navigation(wait_until="domcontentloaded"):
        page.evaluate("void window.liveRefresh.refresh()")

    expect(page.locator(".page-header")).to_contain_text(NEW_SERIAL)
    expect(page.locator(".page-header")).not_to_contain_text(OLD_SERIAL)
    wizard = page.locator("[data-replace-wizard]")
    expect(wizard).to_have_attribute("data-serial", NEW_SERIAL)
    page.get_by_role("button", name="Begin Replacement").click()
    expect(wizard.locator("[data-stage='confirm']")).to_contain_text("Replacement model")
    expect(wizard.locator("[data-stage='confirm']")).to_contain_text("UGOOD")
    page.get_by_role("button", name="Continue", exact=True).click()
    wizard.locator("[data-replace-input='serial']").fill(OLD_SERIAL)
    expect(wizard.locator("[data-replace-action='run-step1']")).to_be_disabled()
    wizard.locator("[data-replace-input='serial']").fill(NEW_SERIAL)
    expect(wizard.locator("[data-replace-action='run-step1']")).to_be_enabled()


def test_disk_change_blocks_old_serial_without_resetting_active_wizard(
    authenticated_page: Page, live_server: str, snapshot_with_drives: str
) -> None:
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    page.get_by_role("button", name="Begin Replacement").click()
    page.get_by_role("button", name="Continue", exact=True).click()
    serial = page.locator("[data-replace-input='serial']")
    serial.fill(OLD_SERIAL)
    run = page.locator("[data-replace-action='run-step1']")
    expect(run).to_be_enabled()
    posted: list[str] = []
    page.on(
        "request", lambda request: posted.append(request.url) if request.method == "POST" else None
    )
    _replace_disk(snapshot_with_drives)
    page.evaluate("window.liveRefresh.refresh()")

    expect(page.locator("body")).to_have_attribute("data-live-stale", "identity")
    expect(page.locator("[data-stage='serial']")).to_be_visible()
    expect(serial).to_have_value(OLD_SERIAL)
    expect(serial).to_be_focused()
    expect(run).to_be_disabled()
    expect(page.locator("[data-replace-identity-warning]")).to_be_visible()
    # A queued click must also pass the guard, even when the button is now disabled.
    run.dispatch_event("click")
    assert posted == []

    with page.expect_navigation(wait_until="domcontentloaded"):
        page.locator("[data-stage='serial'] [data-replace-action='cancel']").click()
    expect(page.locator("[data-replace-wizard]")).to_have_attribute("data-serial", NEW_SERIAL)


def test_disk_change_preserves_physical_swap_and_accepts_replacement_serial(
    authenticated_page: Page, live_server: str, snapshot_with_drives: str
) -> None:
    page = authenticated_page
    posted: list[tuple[str, dict[str, object]]] = []

    def replace_response(route: Route) -> None:
        if route.request.method == "POST":
            posted.append((route.request.url.rsplit("/", 1)[-1], route.request.post_data_json))
            route.fulfill(json={"ok": True})
        else:
            route.fulfill(json={"dg": 0, "array": 0, "row": 0})

    page.route("**/drives/252:0/replace/*", replace_response)
    page.route(
        "**/drives/252:0/replace/rebuild-status",
        lambda route: route.fulfill(content_type="text/html", body="Rebuild 42%"),
    )
    locate_requests: list[str] = []

    def locate_response(route: Route) -> None:
        locate_requests.append(route.request.url)
        route.fulfill(json={"ok": True})

    page.route("**/drives/252:0/locate/start", locate_response)
    login_and_navigate(page, f"{live_server}/drives/252:0")
    page.get_by_role("button", name="Begin Replacement").click()
    page.get_by_role("button", name="Continue", exact=True).click()
    page.locator("[data-replace-input='serial']").fill(OLD_SERIAL)
    page.locator("[data-replace-action='run-step1']").click()
    expect(page.locator("[data-stage='physical-swap']")).to_be_visible()

    _replace_disk(snapshot_with_drives)
    page.evaluate("window.liveRefresh.refresh()")
    expect(page.locator("[data-stage='physical-swap']")).to_be_visible()
    expect(page.locator("[data-replace-stepper] li").nth(1)).to_have_attribute(
        "aria-current", "step"
    )
    expect(page.locator("[data-replace-identity-warning]")).to_be_visible()
    # Slot actions are stale, but read-only rebuild polling must remain available.
    page.evaluate(
        "window.htmx.trigger(document.querySelector('[data-locate-action=start]'), 'click')"
    )
    page.get_by_role("button", name="New Drive Inserted").click()
    new_serial = page.locator("[data-replace-input='new-serial']")
    run = page.locator("[data-replace-action='run-step3']")
    new_serial.fill("ANOTHER-DISK")
    expect(run).to_be_disabled()
    new_serial.fill(NEW_SERIAL)
    expect(run).to_be_enabled()
    page.locator("[data-replace-input='dry-run-step3']").uncheck()
    run.click()
    expect(page.locator("[data-replace-output]")).to_contain_text("insert response")
    expect(page.locator("[data-stage='rebuild']")).to_be_visible()
    expect(page.locator("[data-rebuild-progress]")).to_have_text("Rebuild 42%")
    assert locate_requests == []
    assert posted == [
        ("offline", {"serial_number": OLD_SERIAL, "dry_run": True}),
        ("missing", {"serial_number": OLD_SERIAL, "dry_run": True}),
        ("insert", {"serial_number": NEW_SERIAL, "dry_run": False}),
    ]


@pytest.mark.parametrize("disk_changed", [False, True])
def test_confirmation_opened_during_fetch_keeps_connected_form_and_blocks_stale_submit(
    authenticated_page: Page, live_server: str, snapshot_with_drives: str, disk_changed: bool
) -> None:
    page = authenticated_page
    # UGood enables the action under test; the app remains read-only.
    _replace_disk(snapshot_with_drives)
    login_and_navigate(page, f"{live_server}/drives/252:0")
    if disk_changed:
        engine = get_engine(snapshot_with_drives)
        try:
            with get_sessionmaker(engine)() as session, session.begin():
                session.execute(
                    update(PhysicalDriveSnapshot)
                    .where(PhysicalDriveSnapshot.slot_id == 0)
                    .values(serial_number="SECOND-REPLACEMENT")
                )
        finally:
            engine.dispose()
    page.evaluate("document.getElementById('drive-live-details').dataset.original = 'yes'")
    _hold_refresh(page)
    page.get_by_role("button", name="Spin Down", exact=True).click()
    dialog = page.locator("dialog.confirm-dialog")
    dialog.locator("[data-cd-confirm]").focus()
    _release_refresh(page)

    expect(page.locator("#drive-live-details")).to_have_attribute("data-original", "yes")
    expect(dialog).to_be_visible()
    expect(dialog.locator("[data-cd-confirm]")).to_be_focused()
    assert page.locator("[data-drive-advanced-action]").first.evaluate("form => form.isConnected")
    posted: list[str] = []
    page.on(
        "request", lambda request: posted.append(request.url) if request.method == "POST" else None
    )
    if disk_changed:
        expect(page.locator("body")).to_have_attribute("data-live-stale", "identity")
        # Wait for the confirmed replay to finish before inspecting the request list.
        page.evaluate(
            """async () => {
              const dialog = document.querySelector('dialog');
              await new Promise(resolve => {
                dialog.addEventListener('close', () => setTimeout(resolve, 0), { once: true });
                dialog.querySelector('[data-cd-confirm]').click();
              });
            }"""
        )
        assert posted == []
        expect(page.locator("[data-live-status]")).to_contain_text("reload before acting")
    else:
        expect(page.locator("[data-live-status]")).to_have_attribute("data-state", "paused")
        with page.expect_response(lambda response: "/actions/spindown" in response.url):
            dialog.get_by_role("button", name="Spin Down", exact=True).click()
        assert posted == [f"{live_server}/drives/252:0/actions/spindown"]
        expect(page.locator("[data-drive-action-error]")).to_be_visible()


def test_pause_is_rechecked_between_region_updates(
    authenticated_page: Page, live_server: str, snapshot_with_drives: str
) -> None:
    del snapshot_with_drives
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    page.evaluate(
        """() => {
          document.getElementById('drive-live-details').dataset.original = 'yes';
          document.addEventListener('live:updated', event => {
            if (event.target.id === 'drive-live-status') {
              window.confirmAction({ confirmTitle: 'Pause during swap' });
            }
          });
        }"""
    )
    page.evaluate("window.liveRefresh.refresh()")
    expect(page.locator("dialog.confirm-dialog")).to_be_visible()
    expect(page.locator("#drive-live-details")).to_have_attribute("data-original", "yes")
    expect(page.locator("[data-live-status]")).to_have_attribute("data-state", "paused")


@pytest.mark.parametrize(
    "control",
    [
        "<button>Action</button>",
        '<a href="#drive-live-details">Drive link</a>',
        "<details><summary>More information</summary>Details</details>",
        '<input type="checkbox" checked>',
        '<input value="draft">',
        '<div contenteditable="true">Draft note</div>',
        '<div tabindex="0">Custom control</div>',
    ],
)
@pytest.mark.parametrize("in_flight", [False, True])
def test_refresh_keeps_keyboard_focus_and_control_state(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    control: str,
    in_flight: bool,
) -> None:
    del snapshot_with_drives
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    if in_flight:
        _hold_refresh(page)
    page.evaluate(
        """html => {
          const region = document.getElementById('drive-live-details');
          region.insertAdjacentHTML('afterbegin', html);
          region.dataset.original = 'yes';
          window.focusedControl = region.querySelector(
            'button, a, summary, input, [contenteditable], [tabindex]'
          );
          // Give the new control focus, then verify keyboard navigation returns to it.
          window.focusedControl.focus();
        }""",
        control,
    )
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.activeElement === window.focusedControl")
    before = page.evaluate("window.focusedControl.outerHTML")
    if in_flight:
        _release_refresh(page)
    else:
        page.evaluate("window.liveRefresh.refresh()")
    expect(page.locator("#drive-live-details")).to_have_attribute("data-original", "yes")
    expect(page.locator("[data-live-status]")).to_have_attribute("data-state", "paused")
    assert page.evaluate("document.activeElement === window.focusedControl")
    assert page.evaluate("window.focusedControl.outerHTML") == before

    # Once focus leaves the control, the same refresh path can replace regions again.
    page.evaluate("document.activeElement.blur()")
    page.evaluate("window.liveRefresh.refresh()")
    expect(page.locator("#drive-live-details")).not_to_have_attribute("data-original", "yes")
    expect(page.locator("[data-live-status]")).to_have_attribute("data-state", "live")


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
