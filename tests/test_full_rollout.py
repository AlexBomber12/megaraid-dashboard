"""Full capability API checks with the real authorization helper and isolated storage."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.services.action_capabilities import action_disabled_reason
from megaraid_dashboard.storcli import policy
from megaraid_dashboard.web import routes
from tests.conftest import TEST_AUTH_HEADER
from tests.safety.harness import FakeStorcli, prepare
from tests.safety.scenarios import ACTIONS
from tests.test_storcli.test_policy import policy_data
from tests.test_web.test_drive_actions_routes import app_settings  # noqa: F401
from tests.test_web.test_drive_detail_actions import _insert_app_snapshot


@pytest.fixture
def full_app(monkeypatch, tmp_path, sample_snapshot):
    contract = tmp_path / "policy.json"
    contract.write_text(json.dumps(policy_data()))
    fake = FakeStorcli.create(tmp_path / "fake")
    monkeypatch.setenv("CONTROLLER_READ_ONLY", "false")
    monkeypatch.setenv("CONTROLLER_ACTION_CAPABILITY", "full-supported-actions")
    monkeypatch.setenv("CONTROLLER_COMMAND_POLICY", str(contract))
    monkeypatch.setenv("STORCLI_PATH", str(fake.path))
    monkeypatch.setenv("STORCLI_USE_SUDO", "false")
    monkeypatch.setattr(policy, "protected_path", lambda path: None)
    monkeypatch.setattr(policy, "verify_pinned_binary", lambda path: None)
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(app, sample_snapshot)
        from sqlalchemy import update

        from megaraid_dashboard.db.models import PhysicalDriveSnapshot

        with app.state.session_factory() as session, session.begin():
            session.execute(update(PhysicalDriveSnapshot).values(serial_number="WCA275678000"))
        client.get("/")
        token = client.cookies["__Host-csrf"]
        client.headers.update({"X-CSRF-Token": token, "Cookie": f"__Host-csrf={token}"})
        yield client, app, fake, contract
    get_settings.cache_clear()


@pytest.mark.parametrize("action", ACTIONS, ids=lambda action: action.name)
def test_full_capability_real_routes(full_app, action):
    client, app, fake, _contract = full_app
    body = prepare(action, SimpleNamespace(app=app), fake)
    response = client.post(action.path, json=body, follow_redirects=False)
    if action.name == "mark-ubad":
        assert response.status_code == 403
        assert fake.calls == []
    else:
        assert response.status_code in {200, 303}, response.text
        assert fake.mutations == [list(action.command)]
    for path in ("/drives/252:0", "/controller", "/controller/foreign-config"):
        assert client.get(path).status_code == 200


def test_full_missing_policy_and_invalid_binary(full_app, monkeypatch):
    client, app, fake, contract = full_app
    settings = app.state.settings
    contract.unlink()
    assert "verify the approved policy" in action_disabled_reason(settings, "other writes")
    assert client.post("/controller/buzzer/enable").status_code == 403
    assert client.get("/controller").status_code == 200
    assert fake.calls == []


def test_full_ui_refuses_missing_command_and_maintenance(full_app):
    client, app, fake, contract = full_app
    settings = app.state.settings
    app.state.settings = settings.model_copy(update={"maintenance_mode": False})
    assert "Start a maintenance window" in client.get("/drives/252:0").text
    app.state.settings = settings.model_copy(
        update={"maintenance_mode": True, "destructive_mode": False}
    )
    assert "enable destructive mode" in client.get("/drives/252:0").text
    data = policy_data()
    data["commands"] = [item for item in data["commands"] if "locate" not in item["command"]]
    contract.write_text(json.dumps(data))
    assert client.post("/drives/252:0/locate/stop").status_code == 403
    assert "syntax or topology" in client.get("/drives/252:0").text
    assert fake.calls == []


def test_full_topology_is_explicit(full_app):
    _client, app, _fake, _contract = full_app
    from starlette.requests import Request

    request = Request({"type": "http", "app": app})
    assert routes._compute_slot_topology(request=request, enclosure_id=252, slot_id=0).row == 0
    assert routes._compute_slot_topology(request=request, enclosure_id=252, slot_id=1) is None
    assert routes._compute_slot_topology(request=request, enclosure_id=253, slot_id=0) is None


@pytest.mark.parametrize("family", ["patrol-read", "consistency-check"])
def test_unknown_state_cannot_change_mode(full_app, family):
    client, app, fake, _contract = full_app
    action = next(item for item in ACTIONS if item.family == family and item.name.endswith("-mode"))
    body = prepare(action, SimpleNamespace(app=app), fake)
    fake.configure(operation_state="Unknown")
    response = client.post(action.path, json=body)
    assert response.status_code == 502
    assert "Refresh status" in response.text
    assert fake.mutations == []


def test_insert_uses_explicit_row_instead_of_slot_order(full_app):
    client, app, fake, contract = full_app
    data = policy_data()
    data["replacements"][0].update(array=3, row=6)
    for item in data["commands"]:
        item["command"] = item["command"].replace("array=0 row=0", "array=3 row=6")
    contract.write_text(json.dumps(data))
    action = next(item for item in ACTIONS if item.name == "replace-insert")
    body = prepare(action, SimpleNamespace(app=app), fake)
    response = client.post(action.path, json=body)
    assert response.status_code == 200, response.text
    assert fake.mutations == [["/c0/e252/s0", "insert", "dg=0", "array=3", "row=6", "J"]]
    assert "/c0/e252/s0 insert dg=0 array=3 row=6 J" in policy.load_policy(str(contract)).sudoers(
        ()
    )
