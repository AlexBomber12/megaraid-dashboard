"""State-dependent Patrol Read and Consistency Check controls must be reachable."""

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select

from megaraid_dashboard.db import ControllerSnapshot
from tests.e2e.conftest import _LiveServerHandle, login_and_navigate
from tests.safety.harness import FakeStorcli, database_observation, prepare
from tests.safety.scenarios import ACTIONS, Action


@pytest.mark.parametrize(
    "action",
    [a for a in ACTIONS if a.family in {"patrol-read", "consistency-check"}],
    ids=lambda a: a.name,
)
@pytest.mark.parametrize("fault", ["success", "failure"])
def test_controller_operation_browser_matrix(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    action: Action,
    fault: str,
) -> None:
    page = authenticated_page
    prepare(action, _live_server_handle, fake_storcli)
    fake_storcli.configure(fault=fault)
    # Persist deterministic operation state, without substituting a view model or DOM.
    with _live_server_handle.app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        assert snapshot is not None
        snapshot.raw_json = {
            **snapshot.raw_json,
            action.family.replace("-", "_"): {
                "mode": "manual",
                "state": "active" if action.name.endswith("stop") else "stopped",
            },
        }
    login_and_navigate(page, f"{live_server}/controller")
    button = page.locator(f'form[action="{action.path}"] button')
    expect(button, "Operation controls must render from controller state").to_be_visible(
        timeout=1500
    )
    expect(button).to_be_enabled()
    before = database_observation(snapshot_with_drives)
    with page.expect_response(
        lambda r: r.url.endswith(action.path) and r.request.method == "POST"
    ) as response:
        button.click()
    assert response.value.status == (200 if fault == "success" else 502)
    assert fake_storcli.mutations == [list(action.command)]
    if fault == "failure":
        expect(page.locator("[data-controller-action-error]:visible")).to_contain_text("fail")
    else:
        page.wait_for_load_state("networkidle")
    after = database_observation(snapshot_with_drives)
    assert after[1] == before[1]
    assert len(after[0]) == len(before[0]) + 1
    assert ("succeeded" if fault == "success" else "failed") in after[0][-1][1]


pytestmark = pytest.mark.usefixtures("legacy_controller_handlers")
