"""Real rollout gate with the existing disposable fake StorCLI boundary."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from megaraid_dashboard.db import Event
from megaraid_dashboard.db.dao import set_maintenance_state
from tests.e2e.conftest import E2E_ADMIN_PASSWORD, E2E_ADMIN_USERNAME
from tests.safety.scenarios import ACTIONS

pytestmark = pytest.mark.integration


@pytest.fixture
def client(live_server: str, fresh_db: str) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=live_server,
        verify=False,
        trust_env=False,
        auth=(E2E_ADMIN_USERNAME, E2E_ADMIN_PASSWORD),
    ) as client:
        assert client.get("/").status_code == 200
        client.headers["X-CSRF-Token"] = client.cookies["__Host-csrf"]
        yield client


@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: a.name)
@pytest.mark.parametrize("selection", ["default", "absent", "locate"])
def test_post_inventory_is_closed(
    action,
    selection,
    client,
    _live_server_handle,
    fake_storcli,
):
    app = _live_server_handle.app
    if selection != "default":
        app.state.settings = app.state.settings.model_copy(
            update={
                "controller_read_only": False,
                "controller_action_capability": "locate" if selection == "locate" else "none",
                "maintenance_mode": True,
                "destructive_mode": True,
            }
        )
    response = client.post(action.path, json={})
    if selection == "locate" and action.family == "locate":
        assert response.status_code == 200, response.text
        assert fake_storcli.calls == [list(action.command)]
        assert response.json()["led_state"] == "unverified"
    else:
        assert response.status_code == 403, response.text
        assert fake_storcli.calls == []
        expected = (
            "controller_read_only" if selection == "default" else "controller_action_unavailable"
        )
        assert response.json()["detail"]["error"] == expected


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("action", ["start", "stop"])
@pytest.mark.parametrize(
    "fault", ["success", "failure", "missing", "unknown", "nonzero", "timeout", "audit"]
)
def test_locate_result_and_window_matrix(
    active,
    action,
    fault,
    client,
    _live_server_handle,
    fake_storcli,
):
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(
        update={
            "controller_read_only": False,
            "controller_action_capability": "locate",
        }
    )
    with app.state.session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=active,
            expires_at=datetime.now(UTC) + timedelta(minutes=5) if active else None,
            started_by=E2E_ADMIN_USERNAME,
        )
    fake_storcli.configure(fault="success" if fault == "audit" else fault)

    def fail_audit(*args):
        if args[2].lstrip().lower().startswith("insert into events "):
            raise SQLAlchemyError("injected audit failure")

    if fault == "audit":
        event.listen(app.state.engine, "after_cursor_execute", fail_audit)
    try:
        response = client.post(f"/drives/252:7/locate/{action}")
    finally:
        if fault == "audit":
            event.remove(app.state.engine, "after_cursor_execute", fail_audit)
    assert fake_storcli.calls == ([["/c0/e252/s7", action, "locate", "J"]] if active else [])
    assert response.status_code == (
        500 if fault == "audit" else 403 if not active else 200 if fault == "success" else 502
    )
    with app.state.session_factory() as session:
        audit = session.scalars(select(Event).where(Event.category == "operator_action")).all()
        assert len(audit) == (0 if fault == "audit" else 1)
        if audit:
            assert audit[0].operator_username == E2E_ADMIN_USERNAME
            assert f"locate {action} drive 252:7" in audit[0].summary
    if active and fault == "success":
        assert response.json()["command_accepted"] is True
        assert response.json()["led_state"] == "unverified"
    if active and fault == "timeout":
        assert "LED state is unverified" in response.json()["message"]


def test_maintenance_controls_require_capability(client, _live_server_handle, fake_storcli):
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(update={"controller_read_only": False})
    for path in ("/maintenance/start", "/maintenance/stop"):
        assert client.post(path, json={}).status_code == 403
    app.state.settings = app.state.settings.model_copy(
        update={"controller_action_capability": "locate"}
    )
    assert (
        client.post(
            "/maintenance/start", json={"duration_minutes": 1, "reason": "offline fixture"}
        ).status_code
        == 200
    )
    assert client.post("/maintenance/stop").status_code == 200
    assert fake_storcli.calls == []
