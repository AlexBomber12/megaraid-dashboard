"""Foreign import and clear: execute only the fake, including browser dry-run proof."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import _LiveServerHandle, login_and_navigate
from tests.safety.harness import FakeStorcli, database_observation, prepare
from tests.safety.scenarios import ACTIONS, Action


@pytest.mark.parametrize(
    "action", [a for a in ACTIONS if a.family == "foreign"], ids=lambda a: a.name
)
@pytest.mark.parametrize(
    "outcome", ["success", "failure", "dry-run", "confirmation", "maintenance"]
)
def test_foreign_action_browser(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    action: Action,
    outcome: str,
) -> None:
    page = authenticated_page
    body = prepare(action, _live_server_handle, fake_storcli)
    if outcome == "maintenance":
        app = _live_server_handle.app
        app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": False})
    fake_storcli.configure(fault="failure" if outcome == "failure" else "success")
    login_and_navigate(page, f"{live_server}/controller/foreign-config")
    name = action.name.split("-")[-1]
    disclosure = page.locator(f"#foreign-{name}")
    disclosure.locator("summary").click()
    form = disclosure.locator("form")
    before = database_observation(snapshot_with_drives)
    fake_storcli.clear()
    # Closing a disclosure without confirming must not post anything.
    disclosure.locator("summary").click()
    assert fake_storcli.calls == []
    disclosure.locator("summary").click()
    form.locator('[name="confirmation"]').fill(
        "WRONG" if outcome == "confirmation" else body["confirmation"]
    )
    if outcome != "dry-run":
        form.locator('[name="dry_run"]').uncheck()
    if outcome == "confirmation" and name == "clear":
        form.locator("button").click()
        assert form.locator('[name="confirmation"]').evaluate("el => !el.validity.valid")
        assert fake_storcli.calls == []
        assert database_observation(snapshot_with_drives) == before
        return
    with page.expect_response(
        lambda r: r.url.endswith(action.path) and r.request.method == "POST"
    ) as response:
        form.locator("button").click()
    expected = {
        "success": 200,
        "failure": 502,
        "dry-run": 200,
        "confirmation": 409,
        "maintenance": 403,
    }[outcome]
    assert response.value.status == expected
    assert fake_storcli.mutations == (
        [list(action.command)] if outcome in {"success", "failure"} else []
    )
    result = page.locator(f"#foreign-config-{name}-result")
    if outcome == "dry-run":
        assert all("show" in call for call in fake_storcli.calls)
        assert database_observation(snapshot_with_drives) == before
        expect(result).to_contain_text('"dry_run":true')
    elif outcome == "success":
        expect(result).to_contain_text("Success")
        assert len(database_observation(snapshot_with_drives)[0]) == len(before[0]) + 1
    else:
        expect(result, "HTMX refusals must explain why the operation did not run").to_contain_text(
            {
                "failure": "storcli command failed",
                "confirmation": "confirmation mismatch",
                "maintenance": "maintenance_mode",
            }[outcome],
            timeout=1500,
        )
    expect(form.locator("button")).to_be_enabled()
    assert database_observation(snapshot_with_drives)[1] == before[1]
    page.reload()
    expect(page.get_by_role("heading", name="Foreign Configuration", exact=True)).to_be_visible()


def test_absent_foreign_configuration_hides_mutating_forms(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
) -> None:
    fake_storcli.configure(
        foreign={
            "Total foreign DG Count": 0,
            "Total foreign drive Count": 0,
            "FOREIGN DG LIST": [],
            "FOREIGN PD LIST": [],
        }
    )
    login_and_navigate(authenticated_page, f"{live_server}/controller/foreign-config")
    expect(
        authenticated_page.get_by_role("heading", name="No Foreign Configuration")
    ).to_be_visible()
    expect(authenticated_page.locator("[data-foreign-config-form]")).to_have_count(0)
    authenticated_page.get_by_role("link", name="← Back to Controller").click()
    expect(authenticated_page.locator(".controller-detail-page")).to_be_visible()
    assert fake_storcli.mutations == []
