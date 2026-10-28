"""End-to-end coverage for the redesigned drive detail page."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, Route, expect
from sqlalchemy import select, update

from megaraid_dashboard.db import Event, PhysicalDriveSnapshot, get_engine, get_sessionmaker
from tests.e2e.conftest import _LiveServerHandle, assert_chart_height_within, login_and_navigate
from tests.safety.harness import FakeStorcli, database_observation, prepare
from tests.safety.scenarios import ACTIONS, Action


def test_drive_detail_navigation_charts_actions_and_replace(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
    maintenance_mode: None,
) -> None:
    """Exercise drive detail navigation, charts, locate action, and replacement wizard entry."""
    login_and_navigate(authenticated_page, f"{live_server}/drives/252:0")

    breadcrumb = authenticated_page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb.get_by_text("Overview")).to_be_visible()
    expect(breadcrumb.get_by_text("Drives")).to_be_visible()
    expect(breadcrumb.get_by_text("Slot 252:0")).to_be_visible()

    authenticated_page.get_by_label("Next drive").click()
    authenticated_page.wait_for_url("**/drives/252:1", wait_until="load")
    authenticated_page.get_by_label("Previous drive").click()
    authenticated_page.wait_for_url("**/drives/252:0", wait_until="load")

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
    expect(authenticated_page.get_by_role("button", name="Blink command accepted")).to_be_disabled()
    expect(authenticated_page.get_by_role("button", name="Stop Locate")).to_be_enabled()
    expect(authenticated_page.locator("[data-action-feedback]")).to_contain_text(
        "Controller accepted start"
    )
    expect(authenticated_page.locator("[data-locate-status]")).to_contain_text(
        "LED state unverified"
    )
    assert native_dialogs == []
    assert _operator_event_exists(snapshot_with_drives, "locate start drive 252:0")

    authenticated_page.reload()
    expect(authenticated_page.get_by_role("button", name="Blink command accepted")).to_be_disabled()
    expect(authenticated_page.get_by_role("button", name="Stop Locate")).to_be_enabled()
    expect(authenticated_page.locator("[data-locate-status]")).to_contain_text(
        "LED state unverified"
    )

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


@pytest.mark.parametrize(
    "action", [a for a in ACTIONS if a.family == "advanced"], ids=lambda a: a.name
)
@pytest.mark.parametrize("outcome", ["success", "failure", "maintenance"])
def test_advanced_action_browser_matrix(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    action: Action,
    outcome: str,
) -> None:
    page = authenticated_page
    prepare(action, _live_server_handle, fake_storcli)
    if outcome == "maintenance":
        app = _live_server_handle.app
        app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": False})
    fake_storcli.configure(fault="failure" if outcome == "failure" else "success")
    login_and_navigate(page, f"{live_server}/drives/252:0")
    before = database_observation(snapshot_with_drives)
    button = page.locator(f'form[action="{action.path}"] button')
    if action.name == "mark-ubad":
        expect(button).to_be_disabled()
        expect(
            page.get_by_text("Unavailable: StorCLI 1.23.02 does not document set bad.")
        ).to_be_visible()
        assert fake_storcli.calls == []
        assert database_observation(snapshot_with_drives) == before
        return
    expect(button).to_be_enabled()
    button.click()
    dialog = page.locator("dialog.confirm-dialog")
    expect(dialog).to_be_visible()
    # Cancel is an actual dialog action and must leave both command trace and DB unchanged.
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert fake_storcli.calls == []
    assert database_observation(snapshot_with_drives) == before
    button.click()
    phrase = dialog.locator("[data-cd-input]")
    if phrase.is_visible():
        expect(dialog.locator("[data-cd-confirm]")).to_be_disabled()
        phrase.fill("WRONG")
        expect(dialog.locator("[data-cd-confirm]")).to_be_disabled()
        phrase.fill("WCA275678000")
    with page.expect_response(
        lambda r: r.url.endswith(action.path) and r.request.method == "POST"
    ) as response:
        dialog.locator("[data-cd-confirm]").click()
    assert response.value.status == {"success": 303, "failure": 502, "maintenance": 403}[outcome]
    assert fake_storcli.mutations == ([] if outcome == "maintenance" else [list(action.command)])
    if outcome != "success":
        expect(page.locator("[data-drive-action-error]")).to_contain_text(
            "storcli command failed" if outcome == "failure" else "maintenance_mode"
        )
        expect(button).to_be_enabled()
    else:
        page.wait_for_load_state("networkidle")
        expect(button).to_be_enabled()
    after = database_observation(snapshot_with_drives)
    assert after[1] == before[1]
    if outcome != "maintenance":
        assert len(after[0]) == len(before[0]) + 1
        assert ("succeeded" if outcome == "success" else "failed") in after[0][-1][1]
    if outcome == "success":
        page.goto(f"{live_server}/events")
        expect(page.locator(".timeline__message")).to_contain_text("succeeded")
        page.goto(f"{live_server}/drives/252:0")
    page.reload()
    expect(button).to_be_enabled()


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


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (502, '{"message":"Controller busy. Check the drive and retry."}'),
        (500, '{"message":"Audit could not be saved. Try Stop Locate."}'),
        (403, "CSRF token missing"),
        (200, "{}"),
        (200, "null"),
        (200, '{"message":{}}'),
        (502, '{"message":"Unsupported <img src=x onerror=alert(1)>. Retry."}'),
    ],
)
def test_locate_feedback_updates_buttons_immediately_and_keeps_stop_available(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    status: int,
    body: str,
    maintenance_mode: None,
) -> None:
    """Exercise HTMX responses with browser-only mocks; no hardware commands run."""
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    page.evaluate("window.locatePageMarker = 'same page'")
    start = page.locator('[data-locate-action="start"]')
    stop = page.locator('[data-locate-action="stop"]')
    feedback = page.locator("[data-action-feedback]")
    expect(start).to_be_enabled()
    expect(stop).to_be_enabled()

    def accept(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"command_accepted":true,"led_state":"unverified"}',
        )

    page.route("**/locate/start", accept)
    start.click()
    expect(start).to_be_disabled()
    expect(start).to_contain_text("Blink command accepted")
    expect(stop).to_be_enabled()
    expect(feedback).to_contain_text("Controller accepted start")
    assert "Sent" not in feedback.inner_text()
    assert "LED On" not in page.locator(".drive-actions").inner_text()

    page.route(
        "**/locate/stop",
        lambda route: route.fulfill(status=status, content_type="application/json", body=body),
    )
    stop.click()
    expect(feedback).to_have_class("drive-actions__feedback drive-actions__feedback--error")
    expect(start).to_be_enabled()
    expect(stop).to_be_enabled()
    expect(page.locator(".drive-actions")).to_have_attribute("data-locate-state", "unknown")
    expect(page.locator("[data-locate-status]")).to_have_text("LED state unverified.")
    if status == 403:
        expect(feedback).to_have_text(
            "CSRF token missing or mismatched. Reload the HTTPS page and retry."
        )
        assert "controller access" not in feedback.inner_text().lower()
    elif '"message"' in body and isinstance(json.loads(body)["message"], str):
        expect(feedback).to_have_text(json.loads(body)["message"])
    else:
        expect(feedback).to_contain_text("Check controller access")
    expect(feedback.locator("img")).to_have_count(0)

    page.unroute("**/locate/stop")
    page.route("**/locate/stop", accept)
    stop.click()
    expect(feedback).to_contain_text("Controller accepted stop")
    expect(start).to_be_enabled()
    expect(start).to_contain_text("Blink Locate LED")
    expect(stop).to_be_enabled()
    expect(page.locator("[data-locate-status]")).to_contain_text("Last accepted command: stop")
    assert page.evaluate("window.locatePageMarker") == "same page"


@pytest.mark.parametrize("token", [None, "mismatched-token"])
@pytest.mark.parametrize("action", ["start", "stop"])
def test_locate_csrf_refusal_instructs_https_reload_and_keeps_stop_available(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    token: str | None,
    action: str,
    maintenance_mode: None,
) -> None:
    """Use the real CSRF middleware; rejected requests never reach StorCLI."""
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")

    def change_csrf_header(route: Route) -> None:
        headers = dict(route.request.headers)
        headers.pop("x-csrf-token", None)
        if token is not None:
            headers["x-csrf-token"] = token
        route.continue_(headers=headers)

    page.route(f"**/locate/{action}", change_csrf_header)
    with page.expect_response(
        lambda response: response.url.endswith(f"/locate/{action}")
    ) as pending:
        page.locator(f'[data-locate-action="{action}"]').click()
    response = pending.value
    assert response.status == 403
    assert response.text() == "csrf token missing or mismatched"

    feedback = page.locator("[data-action-feedback]")
    expect(feedback).to_be_visible()
    expect(feedback).to_have_text(
        "CSRF token missing or mismatched. Reload the HTTPS page and retry."
    )
    expect(feedback).to_have_class("drive-actions__feedback drive-actions__feedback--error")
    assert "controller access" not in feedback.inner_text().lower()
    expect(page.locator('[data-locate-action="stop"]')).to_be_enabled()
    expect(page.locator('[data-locate-action="start"]')).to_be_enabled()
    expect(page.locator("[data-locate-status]")).to_have_text("LED state unverified.")
    expect(page.locator(".drive-actions")).to_have_attribute("data-locate-state", "unknown")
    assert not _operator_event_exists(snapshot_with_drives, f"locate {action} drive 252:0")


@pytest.mark.parametrize("name", ["spindown", "buzzer-silence", "foreign-import", "locate-start"])
def test_mutation_has_immediate_busy_feedback(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    name: str,
) -> None:
    page = authenticated_page
    action = next(a for a in ACTIONS if a.name == name)
    body = prepare(action, _live_server_handle, fake_storcli)
    fake_storcli.configure(delay_seconds=0.2)
    if action.family == "foreign":
        login_and_navigate(page, f"{live_server}/controller/foreign-config")
        disclosure = page.locator("#foreign-import")
        disclosure.locator("summary").click()
        disclosure.locator('[name="confirmation"]').fill(body["confirmation"])
        disclosure.locator('[name="dry_run"]').uncheck()
    else:
        login_and_navigate(
            page,
            f"{live_server}/controller"
            if action.family == "buzzer"
            else f"{live_server}/drives/252:0",
        )
    button = (
        page.locator('[data-locate-action="start"]')
        if action.family == "locate"
        else page.locator(f'form[action="{action.path}"] button')
    )
    # A fetch can navigate before Playwright's click returns. Observe the actual
    # submit/send event synchronously, then read the observation after navigation.
    # This does not replace the request, response, cookie, or CSRF behavior.
    page.evaluate(
        """path => {
      sessionStorage.removeItem('safety-in-flight');
      function observe(event) {
        const el = event.detail?.elt || event.target;
        const form = el.closest('form');
        const endpoint = el.getAttribute('hx-post') || form?.getAttribute('action');
        if (endpoint !== path) return;
        const button = el.matches('button') ? el : el.querySelector('button');
        const busy = Boolean(button?.disabled || el.getAttribute('aria-busy') === 'true' ||
          el.classList.contains('htmx-request') || form?.getAttribute('aria-busy') === 'true' ||
          form?.classList.contains('htmx-request'));
        sessionStorage.setItem('safety-in-flight', String(busy));
      }
      document.addEventListener('submit', observe);
      document.addEventListener('htmx:beforeSend', observe);
    }""",
        action.path,
    )
    with page.expect_response(lambda r: r.url.endswith(action.path) and r.request.method == "POST"):
        button.click()
        if action.family == "advanced":
            page.locator("[data-cd-confirm]").click()
    assert fake_storcli.mutations == [list(action.command)]
    assert page.evaluate("sessionStorage.getItem('safety-in-flight')") == "true", (
        "Mutating control must show in-flight state immediately"
    )


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


def test_undocumented_legacy_drive_action_is_unavailable(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    _set_drive_state(snapshot_with_drives, slot_id=7, state="UGood")
    login_and_navigate(authenticated_page, f"{live_server}/drives/252:7")
    expect(authenticated_page.get_by_role("button", name="Mark as UBad")).to_be_disabled()
    expect(
        authenticated_page.get_by_text("Unavailable: StorCLI 1.23.02 does not document set bad.")
    ).to_be_visible()


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
