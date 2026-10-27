"""Contract matrix using a real local server, database and fake subprocess.

Unlike browser acceptance tests these may echo CSRF in an HTTP client. Every
request still passes the actual authentication/CSRF middleware and route layer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import SQLAlchemyError

from megaraid_dashboard.db import Event, PhysicalDriveSnapshot
from tests.e2e.conftest import E2E_ADMIN_PASSWORD, E2E_ADMIN_USERNAME, _LiveServerHandle
from tests.safety.harness import FakeStorcli, database_observation, prepare
from tests.safety.scenarios import ACTIONS, Action

pytestmark = pytest.mark.integration


@pytest.fixture
def client(live_server: str) -> Iterator[httpx.Client]:
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
@pytest.mark.parametrize(
    "fault",
    ["success", "failure", "missing", "unknown", "nonzero", "timeout", "unavailable", "audit"],
)
def test_mutation_result_matrix(
    action: Action,
    fault: str,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    body = prepare(action, _live_server_handle, fake_storcli)
    before = database_observation(snapshot_with_drives)
    fake_storcli.configure(fault=fault if fault not in {"audit", "unavailable"} else "success")
    if fault == "unavailable":
        fake_storcli.unavailable()

    def fail_audit_after_insert(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        many: bool,
    ) -> None:
        if statement.lstrip().lower().startswith("insert into events "):
            raise SQLAlchemyError("injected audit storage failure after insert")

    engine = _live_server_handle.app.state.engine
    if fault == "audit":
        event.listen(engine, "after_cursor_execute", fail_audit_after_insert)
    started_at = datetime.now(UTC)
    try:
        response = client.post(action.path, json=body)
    finally:
        if fault == "audit":
            event.remove(engine, "after_cursor_execute", fail_audit_after_insert)
    events, drives = database_observation(snapshot_with_drives)
    assert drives == before[1], "commands must not fabricate collector snapshot updates"
    expected = (
        500
        if fault == "audit"
        else 502
        if action.name == "mark-ubad"
        else (303 if action.family in {"advanced", "buzzer"} else 200)
        if fault == "success"
        else 502
    )
    # Assert response after side-effect checks so failures retain actionable trace evidence.
    if fault == "unavailable":
        assert fake_storcli.calls == []
    else:
        assert fake_storcli.mutations == (
            [] if action.name == "mark-ubad" else [list(action.command)]
        )
    if fault == "audit":
        assert events == before[0]
        assert "audit" in response.text.lower()
    else:
        assert len(events) == len(before[0]) + 1
        with _live_server_handle.app.state.session_factory() as session:
            audit = session.scalar(select(Event).order_by(Event.id.desc()).limit(1))
            assert audit is not None
            assert audit.operator_username == E2E_ADMIN_USERNAME
            assert started_at <= audit.occurred_at <= datetime.now(UTC)
        assert (
            "succeeded" if fault == "success" and action.name != "mark-ubad" else "fail"
        ) in events[-1][1].lower()
    assert response.status_code == expected, response.text
    if fault not in {"success", "audit"}:
        assert "fail" in response.text.lower()


@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: a.name)
@pytest.mark.parametrize("gate", ["maintenance", "authentication", "csrf"])
def test_rejected_request_cannot_mutate(
    action: Action,
    gate: str,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    body = prepare(action, _live_server_handle, fake_storcli)
    if gate == "maintenance":
        app = _live_server_handle.app
        app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": False})
    elif gate == "authentication":
        client.auth = None
    else:
        del client.headers["X-CSRF-Token"]
    before = database_observation(snapshot_with_drives)
    response = client.post(action.path, json=body)
    assert fake_storcli.mutations == []
    assert database_observation(snapshot_with_drives)[1] == before[1]
    if gate != "maintenance":
        assert fake_storcli.calls == []
        assert database_observation(snapshot_with_drives) == before
    assert response.status_code == (401 if gate == "authentication" else 403), response.text
    if gate == "maintenance":
        assert "maintenance" in response.text.lower()
    elif gate == "csrf":
        assert "csrf" in response.text.lower()
    else:
        assert response.headers["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize(
    "action", [a for a in ACTIONS if a.family in {"replace", "foreign"}], ids=lambda a: a.name
)
@pytest.mark.parametrize("gate", ["dry-run", "destructive", "confirmation", "state"])
def test_destructive_gates_and_dry_run_trace(
    action: Action,
    gate: str,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    body = prepare(action, _live_server_handle, fake_storcli)
    if gate == "dry-run":
        body["dry_run"] = True
    elif gate == "destructive":
        app = _live_server_handle.app
        app.state.settings = app.state.settings.model_copy(update={"destructive_mode": False})
    elif gate == "confirmation":
        body["serial_number" if action.family == "replace" else "confirmation"] = "WRONG"
    else:
        if action.family == "replace":
            fake_storcli.configure(drive_state="Rbld")
        else:
            with _live_server_handle.app.state.session_factory() as session, session.begin():
                session.execute(update(PhysicalDriveSnapshot).values(state="Rbld"))
    before = database_observation(snapshot_with_drives)
    response = client.post(action.path, json=body)
    assert fake_storcli.mutations == [], fake_storcli.calls
    assert all("show" in args for args in fake_storcli.calls)
    after = database_observation(snapshot_with_drives)
    assert after[1] == before[1]
    if gate == "dry-run":
        assert response.status_code == 200, response.text
        assert response.json()["argv"] == list(action.command)
        assert after == before
        if action.family == "replace":
            assert fake_storcli.calls == []
    else:
        assert response.status_code == (403 if gate == "destructive" else 409), response.text
        assert response.json().get("error")


@pytest.mark.parametrize(
    "action", [a for a in ACTIONS if a.family in {"advanced", "replace"}], ids=lambda a: a.name
)
def test_live_serial_change_refuses_drive_mutation(
    action: Action,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    body = prepare(action, _live_server_handle, fake_storcli)
    fake_storcli.configure(serial="REPLACED-SINCE-SNAPSHOT")
    response = client.post(action.path, json=body)
    assert fake_storcli.mutations == []
    assert response.status_code == 409
    assert "serial" in response.text.lower() or "identity" in response.text.lower()


@pytest.mark.parametrize(
    ("action", "gate"),
    [
        (action, gate)
        for action in ACTIONS
        if action.family == "advanced"
        for gate in ("snapshot-state", "live-state", "missing-dg", "destructive")
        if (gate != "missing-dg" or action.name == "hotspare")
        and (gate != "destructive" or action.name == "mark-ubad")
    ],
    ids=lambda value: value.name if isinstance(value, Action) else value,
)
def test_advanced_action_state_gates(
    action: Action,
    gate: str,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    body = prepare(action, _live_server_handle, fake_storcli)
    if gate == "snapshot-state":
        with _live_server_handle.app.state.session_factory() as session, session.begin():
            session.execute(update(PhysicalDriveSnapshot).values(state="Rbld"))
    elif gate == "live-state":
        fake_storcli.configure(drive_state="Rbld")
    elif gate == "missing-dg":
        body["dg_id"] = 63
    else:
        app = _live_server_handle.app
        app.state.settings = app.state.settings.model_copy(update={"destructive_mode": False})
    before = database_observation(snapshot_with_drives)
    response = client.post(action.path, json=body)
    assert fake_storcli.mutations == []
    assert database_observation(snapshot_with_drives)[1] == before[1]
    assert response.status_code == (403 if gate == "destructive" else 409)
    assert response.json().get("error")


@pytest.mark.parametrize(
    "action",
    [
        a
        for a in ACTIONS
        if a.family in {"patrol-read", "consistency-check"} and not a.name.endswith("-mode")
    ],
    ids=lambda a: a.name,
)
def test_controller_operation_state_conflict(
    action: Action,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    body = prepare(action, _live_server_handle, fake_storcli)
    fake_storcli.configure(operation_state="Stopped" if action.name.endswith("stop") else "Active")
    response = client.post(action.path, json=body)
    assert fake_storcli.mutations == []
    assert response.status_code == 409
    assert "running" in response.text


@pytest.mark.parametrize("path", ["/maintenance/start", "/maintenance/stop"])
def test_maintenance_audit_failure_rolls_back_state(
    path: str,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if path.endswith("stop"):
        assert (
            client.post(
                "/maintenance/start", json={"duration_minutes": 15, "reason": "setup"}
            ).status_code
            == 200
        )
    state = client.get("/maintenance").json()
    before = database_observation(snapshot_with_drives)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("injected audit failure")

    monkeypatch.setattr("megaraid_dashboard.web.routes.record_operator_action", fail)
    # Uvicorn closes the connection after this deliberately unhandled exception.
    # Request close explicitly so the subsequent state GET opens a fresh connection.
    response = client.post(
        path,
        json={"duration_minutes": 15, "reason": "isolated test"},
        headers={"Connection": "close"},
    )
    assert response.status_code == 500
    after = client.get("/maintenance").json()
    assert after["active"] == state["active"]
    assert after["expires_at"] == state["expires_at"]
    assert database_observation(snapshot_with_drives) == before
    assert fake_storcli.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"duration_minutes": 0, "reason": "test"},
        {"duration_minutes": 1441, "reason": "test"},
        {"duration_minutes": 15, "reason": " "},
    ],
)
def test_invalid_maintenance_request_cannot_change_state(
    body: dict[str, Any],
    client: httpx.Client,
    fresh_db: str,
    fake_storcli: FakeStorcli,
) -> None:
    before = database_observation(fresh_db)
    response = client.post("/maintenance/start", json=body)
    assert response.status_code == 422
    assert client.get("/maintenance").json()["active"] is False
    assert database_observation(fresh_db) == before
    assert fake_storcli.calls == []


@pytest.mark.parametrize(
    "name",
    [
        "mark-ubad",
        "replace-offline",
        "foreign-import",
        "patrol-read-start",
        "consistency-check-start",
    ],
)
@pytest.mark.parametrize("fault", ["failure", "missing", "unknown", "nonzero", "timeout"])
def test_precheck_failure_cannot_reach_mutation(
    name: str,
    fault: str,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    action = next(action for action in ACTIONS if action.name == name)
    body = prepare(action, _live_server_handle, fake_storcli)
    fake_storcli.configure(read_fault=fault)
    before = database_observation(snapshot_with_drives)
    response = client.post(action.path, json=body)
    assert fake_storcli.mutations == []
    assert all("show" in call for call in fake_storcli.calls)
    assert database_observation(snapshot_with_drives)[1] == before[1]
    assert response.status_code == 502
    assert response.json().get("error")


async def test_harness_blocks_real_executable_sudo_network_and_database(
    fake_storcli: FakeStorcli,
    tmp_path: Path,
) -> None:
    import asyncio
    import socket

    from megaraid_dashboard.db import get_engine

    # Guards reject before attempting either program. Neither executable is invoked.
    for program in ("/usr/local/sbin/storcli64", "sudo"):
        with pytest.raises(AssertionError, match="temporary fake"):
            await asyncio.create_subprocess_exec(program)
    with socket.socket() as sock, pytest.raises(AssertionError, match="External networking"):
        sock.connect(("192.0.2.1", 443))
    forbidden = tmp_path.parent.parent / "forbidden-safety-test.db"
    engine = get_engine(f"sqlite:///{forbidden}")
    try:
        with pytest.raises(AssertionError), engine.connect():
            pass
        assert not forbidden.exists(), "Guard must reject before opening a database file"
    finally:
        engine.dispose()
    assert fake_storcli.calls == []


@pytest.mark.parametrize("path", ["/maintenance/start", "/maintenance/stop"])
@pytest.mark.parametrize("gate", ["authentication", "csrf"])
def test_maintenance_security_gate_cannot_write(
    path: str,
    gate: str,
    client: httpx.Client,
    fresh_db: str,
    fake_storcli: FakeStorcli,
) -> None:
    before = database_observation(fresh_db)
    if gate == "authentication":
        client.auth = None
    else:
        del client.headers["X-CSRF-Token"]
    response = client.post(path, json={"duration_minutes": 15, "reason": "must be refused"})
    assert response.status_code == (401 if gate == "authentication" else 403)
    assert database_observation(fresh_db) == before
    assert fake_storcli.calls == []


@pytest.mark.parametrize("fault", ["failure", "nonzero", "timeout"])
def test_insert_can_retry_after_audited_read_only_precheck_outage(
    fault: str,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    action = next(action for action in ACTIONS if action.name == "replace-insert")
    body = prepare(action, _live_server_handle, fake_storcli)
    fake_storcli.configure(read_fault=fault)
    first = client.post(action.path, json=body)
    assert first.status_code == 502
    assert fake_storcli.mutations == []
    with _live_server_handle.app.state.session_factory() as session:
        audit = session.scalar(select(Event).order_by(Event.id.desc()).limit(1))
        assert audit is not None
        assert audit.after_json == {"read_only_precheck": "replace_insert"}
    fake_storcli.configure(read_fault="success")
    fake_storcli.clear()
    retry = client.post(action.path, json=body)
    assert retry.status_code == 200
    assert fake_storcli.calls == [["/c0/e252/s0", "show", "all", "J"], list(action.command)]
    assert "succeeded" in database_observation(snapshot_with_drives)[0][-1][1]


@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: a.name)
@pytest.mark.parametrize("dry_run", [False, True])
def test_rollout_capability_refuses_even_authorized_maintenance(
    action: Action,
    dry_run: bool,
    client: httpx.Client,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
) -> None:
    body = prepare(action, _live_server_handle, fake_storcli)
    body["dry_run"] = dry_run
    app = _live_server_handle.app
    app.state.settings = app.state.settings.model_copy(
        update={
            "controller_read_only": True,
            "maintenance_mode": True,
            "destructive_mode": True,
        }
    )
    before = database_observation(snapshot_with_drives)
    fake_storcli.clear()
    response = client.post(action.path, json=body)
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "controller_read_only"
    assert fake_storcli.calls == []
    assert database_observation(snapshot_with_drives) == before
