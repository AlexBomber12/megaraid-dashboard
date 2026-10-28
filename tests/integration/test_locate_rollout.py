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
    permitted = active or action == "stop"
    assert fake_storcli.calls == ([["/c0/e252/s7", action, "locate", "J"]] if permitted else [])
    assert response.status_code == (
        500 if fault == "audit" else 403 if not permitted else 200 if fault == "success" else 502
    )
    with app.state.session_factory() as session:
        audit = session.scalars(select(Event).where(Event.category == "operator_action")).all()
        assert len(audit) == (0 if fault == "audit" else 1)
        if audit:
            assert audit[0].operator_username == E2E_ADMIN_USERNAME
            assert f"locate {action} drive 252:7" in audit[0].summary
    if permitted and fault == "success":
        assert response.json()["command_accepted"] is True
        assert response.json()["led_state"] == "unverified"
    if permitted and fault == "timeout":
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


@pytest.mark.parametrize(
    "fault", ["success", "failure", "missing", "unknown", "nonzero", "timeout", "audit"]
)
def test_stop_recovers_after_start_and_persisted_window_expiry(
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
            "maintenance_mode": True,
        }
    )
    with app.state.session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            started_by=E2E_ADMIN_USERNAME,
        )
    fake_storcli.configure(fault="success" if fault == "audit" else fault)

    def fail_audit(*args):
        if args[2].lstrip().lower().startswith("insert into events "):
            raise SQLAlchemyError("injected Start audit failure")

    if fault == "audit":
        event.listen(app.state.engine, "after_cursor_execute", fail_audit)
    try:
        start = client.post("/drives/252:0/locate/start")
    finally:
        if fault == "audit":
            event.remove(app.state.engine, "after_cursor_execute", fail_audit)
    assert start.status_code == (500 if fault == "audit" else 200 if fault == "success" else 502)
    if fault != "success":
        assert "Do not repeat Start blindly" in start.json()["message"]
        assert "without maintenance" in start.json()["message"]
        assert "retry" not in start.json()["message"].lower()
    with app.state.session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            started_by=E2E_ADMIN_USERNAME,
        )
    assert client.get("/maintenance").json()["active"] is False
    denied = client.post("/drives/252:0/locate/start")
    assert denied.status_code == 403
    assert fake_storcli.calls == [["/c0/e252/s0", "start", "locate", "J"]]
    fake_storcli.configure(fault="success")
    stop = client.post("/drives/252:0/locate/stop")
    assert stop.status_code == 200, stop.text
    assert stop.json()["command_accepted"] is True
    assert stop.json()["led_state"] == "unverified"
    assert fake_storcli.calls == [
        ["/c0/e252/s0", "start", "locate", "J"],
        ["/c0/e252/s0", "stop", "locate", "J"],
    ]
    assert client.get("/maintenance").json()["active"] is False
    with app.state.session_factory() as session:
        audits = session.scalars(
            select(Event).where(Event.category == "operator_action").order_by(Event.id)
        ).all()
        assert len(audits) == (2 if fault == "audit" else 3)
        assert audits[-1].summary == "locate stop drive 252:0 succeeded"
        assert audits[-1].operator_username == E2E_ADMIN_USERNAME


@pytest.mark.parametrize("action", ["start", "stop"])
@pytest.mark.parametrize("gate", ["read-only", "capability", "authentication", "csrf"])
def test_expired_window_preserves_outer_locate_gates(
    action,
    gate,
    client,
    _live_server_handle,
    fake_storcli,
):
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(
        update={
            "controller_read_only": gate == "read-only",
            "controller_action_capability": "none" if gate == "capability" else "locate",
            "maintenance_mode": True,
        }
    )
    with app.state.session_factory() as session, session.begin():
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            started_by=E2E_ADMIN_USERNAME,
        )
    if gate == "authentication":
        client.auth = None
    elif gate == "csrf":
        del client.headers["X-CSRF-Token"]
    response = client.post(f"/drives/252:0/locate/{action}")
    assert response.status_code == (401 if gate == "authentication" else 403)
    assert fake_storcli.calls == []
    with app.state.session_factory() as session:
        assert session.scalars(select(Event).where(Event.category == "operator_action")).all() == []
