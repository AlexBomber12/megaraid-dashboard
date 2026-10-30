"""Browser acceptance of the real locate-only policy, never the legacy bypass."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import login_and_navigate


@pytest.mark.parametrize("capability", ["none", "locate"])
@pytest.mark.parametrize("active", [False, True])
def test_locate_only_controls(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    capability,
    active,
):
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(
        update={
            "controller_read_only": False,
            "controller_action_capability": capability,
            "maintenance_mode": active,
            "destructive_mode": True,
        }
    )
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    start = page.locator('[data-locate-action="start"]')
    stop = page.locator('[data-locate-action="stop"]')
    if capability == "locate" and active:
        expect(start).to_be_enabled()
        with page.expect_response("**/locate/start") as response:
            start.click()
        assert response.value.status == 200
        expect(stop).to_be_enabled()
        with page.expect_response("**/locate/stop") as response:
            stop.click()
        assert response.value.status == 200
        assert fake_storcli.mutations == [
            ["/c0/e252/s0", "start", "locate", "J"],
            ["/c0/e252/s0", "stop", "locate", "J"],
        ]
    else:
        expect(start).to_be_disabled()
        if capability == "locate":
            expect(page.locator("#maintenance")).to_contain_text("Start a maintenance window")
            expect(stop).to_be_enabled()
            with page.expect_response("**/locate/stop") as response:
                stop.click()
            assert response.value.status == 200
            expect(start).to_be_disabled()
        else:
            expect(stop).to_be_disabled()
    for button in page.locator('[data-replace-action="open"], form[method=post] button').all():
        expect(button).to_be_disabled()
    expect(page.locator("main")).to_contain_text("Only Locate Start/Stop is supported")
    login_and_navigate(page, f"{live_server}/controller")
    for button in page.locator("main form[method=post] button").all():
        expect(button).to_be_disabled()
    expect(page.locator("main")).to_contain_text("Only Locate Start/Stop is supported")
    login_and_navigate(page, f"{live_server}/controller/foreign-config")
    for button in page.locator("main form[method=post] button").all():
        expect(button).to_be_disabled()


@pytest.mark.parametrize("fault", ["success", "unknown", "timeout", "audit"])
def test_stop_after_uncertain_start_and_window_expiry(
    authenticated_page,
    live_server,
    snapshot_with_drives,
    _live_server_handle,
    fake_storcli,
    fault,
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import event, select
    from sqlalchemy.exc import SQLAlchemyError

    from megaraid_dashboard.db import Event
    from megaraid_dashboard.db.dao import get_maintenance_state, set_maintenance_state

    app = _live_server_handle.app
    assert app.state.settings.controller_read_only is True  # Actual default, no legacy bypass.
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/drives/252:0")
    for action in ("start", "stop"):
        expect(page.locator(f'[data-locate-action="{action}"]')).to_be_disabled()
    assert fake_storcli.calls == []
    app.state.settings = app.state.settings.model_copy(
        update={
            "controller_read_only": False,
            "controller_action_capability": "locate",
            "maintenance_mode": True,
        }
    )
    with app.state.session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            started_by="browser-operator",
        )
    fake_storcli.configure(fault="success" if fault == "audit" else fault)

    def fail_audit(*args):
        if args[2].lstrip().lower().startswith("insert into events "):
            raise SQLAlchemyError("injected Start audit failure")

    page.reload()
    if fault == "audit":
        event.listen(app.state.engine, "after_cursor_execute", fail_audit)
    try:
        with page.expect_response("**/locate/start") as response:
            page.locator('[data-locate-action="start"]').click()
        assert response.value.status == (
            500 if fault == "audit" else 200 if fault == "success" else 502
        )
    finally:
        if fault == "audit":
            event.remove(app.state.engine, "after_cursor_execute", fail_audit)
    if fault != "success":
        expect(page.locator("[data-action-feedback]")).to_contain_text(
            "Do not repeat Start blindly"
        )
    with app.state.session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            started_by="browser-operator",
        )
    fake_storcli.configure(fault="success")
    page.reload()
    start = page.locator('[data-locate-action="start"]')
    stop = page.locator('[data-locate-action="stop"]')
    expect(start).to_be_disabled()
    expect(page.locator("#maintenance")).to_contain_text("Start a maintenance window")
    expect(stop).to_be_enabled()
    with page.expect_response("**/locate/stop") as response:
        stop.click()
    assert response.value.status == 200
    expect(page.locator("[data-action-feedback]")).to_contain_text("Controller accepted stop")
    expect(start).to_be_disabled()  # Stop feedback must not re-enable Start.
    expect(stop).to_be_enabled()
    page.locator("button[data-live-refresh]").click()
    expect(start).to_be_disabled()
    expect(stop).to_be_enabled()
    assert fake_storcli.calls == [
        ["/c0/e252/s0", "start", "locate", "J"],
        ["/c0/e252/s0", "stop", "locate", "J"],
    ]
    with app.state.session_factory() as session:
        assert get_maintenance_state(session, now=datetime.now(UTC)).active is False
        audit = session.scalars(
            select(Event).where(Event.summary == "locate stop drive 252:0 succeeded")
        ).one()
        assert audit.operator_username
    app.state.settings = app.state.settings.model_copy(
        update={"controller_action_capability": "none"}
    )
    page.reload()
    expect(start).to_be_disabled()
    expect(stop).to_be_disabled()
