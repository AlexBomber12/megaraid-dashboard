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
    assert "Maintenance required" in client.get("/drives/252:0").text
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


@pytest.mark.parametrize("step", ["offline", "missing", "insert"])
@pytest.mark.parametrize("entry", ["replace-offline", "replace-missing", "replace-insert"])
def test_replacement_requires_every_step_before_mutation(full_app, step, entry):
    client, app, fake, contract = full_app
    data = policy_data()
    verb = f"set {step}" if step != "insert" else "insert dg="
    data["commands"] = [item for item in data["commands"] if verb not in item["command"]]
    contract.write_text(json.dumps(data))
    action = next(item for item in ACTIONS if item.name == entry)
    body = prepare(action, SimpleNamespace(app=app), fake)
    html = client.get("/drives/252:0").text
    assert f"{step.title()} command or topology is not approved" in html
    response = client.post(action.path, json=body)
    assert response.status_code == 403
    assert f"{step.title()} command or topology is not approved" in response.text
    assert fake.calls == []


@pytest.mark.parametrize(
    "name,keep,approved,refused",
    [
        ("consistency-check-start", "/c0/vall start cc J", {}, {"vd_id": 0}),
        ("consistency-check-start", "/c0/v0 start cc J", {"vd_id": 0}, {}),
        (
            "patrol-read-mode",
            "/c0 set patrolread=on mode=auto J",
            {"mode": "auto"},
            {"mode": "disable"},
        ),
        (
            "patrol-read-mode",
            "/c0 set patrolread=on mode=manual J",
            {"mode": "manual"},
            {"mode": "auto"},
        ),
        (
            "consistency-check-mode",
            "/c0 set cc=conc J",
            {"mode": "concurrent"},
            {"mode": "sequential"},
        ),
    ],
)
def test_only_selected_alternative_requires_approval(full_app, name, keep, approved, refused):
    client, app, fake, contract = full_app
    action = next(item for item in ACTIONS if item.name == name)
    prepare(action, SimpleNamespace(app=app), fake)
    data = policy_data()
    candidates = policy.action_commands(policy.CommandPolicy.model_validate(data), action.path)
    data["commands"] = [
        item
        for item in data["commands"]
        if item["command"] not in candidates or item["command"] == keep
    ]
    contract.write_text(json.dumps(data))
    from sqlalchemy import select

    from megaraid_dashboard.db.models import ControllerSnapshot

    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.raw_json = {
            **(snapshot.raw_json or {}),
            "patrol_read": {"mode": "manual", "state": "stopped"},
            "consistency_check": {"mode": "concurrent", "state": "stopped"},
        }
    assert client.get("/controller").status_code == 200
    assert client.post(action.path, json=refused).status_code == 403
    assert fake.calls == []
    assert client.post(action.path, json=approved).status_code == 200
    assert fake.mutations == [keep.split()]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/controller/patrol-read/mode", {"mode": "wrong"}),
        ("/controller/consistency-check/mode", {"mode": "wrong"}),
        ("/controller/consistency-check/start", {"vd_id": -1}),
    ],
)
def test_invalid_selected_request_is_refused_without_probes(full_app, path, body):
    client, _app, fake, _contract = full_app
    response = client.post(path, json=body)
    assert response.status_code == 400
    assert "Invalid action request" in response.text
    assert fake.calls == []


@pytest.mark.parametrize("fault", ["none", "controller", "unknown-rebuild", "active-rebuild"])
def test_foreign_import_recovery_without_local_vd(full_app, fault):
    client, app, fake, _contract = full_app
    action = next(item for item in ACTIONS if item.name == "foreign-import")
    body = prepare(action, SimpleNamespace(app=app), fake)
    from sqlalchemy import delete

    from megaraid_dashboard.db.models import VirtualDriveSnapshot

    with app.state.session_factory() as session, session.begin():
        session.execute(delete(VirtualDriveSnapshot))
    config = json.loads((fake.path.parent / "state.json").read_text())
    controller = config["controller"]
    controller.update({"Virtual Drives": 0, "VD LIST": [], "Drive Groups": 0, "TOPOLOGY": []})
    for drive in controller["PD LIST"]:
        drive.update(State="UGood", DG="-")
    if fault == "controller":
        controller["Basics"]["Serial Number"] = "DIFFERENT-CONTROLLER"
    fake.configure(
        controller=controller,
        rebuild_state={"unknown-rebuild": "Unknown", "active-rebuild": "In progress"}.get(
            fault, "Not in progress"
        ),
    )
    response = client.post(action.path, json=body)
    if fault == "none":
        assert response.status_code == 200, response.text
        assert fake.mutations == [list(action.command)]
    else:
        assert response.status_code == 502, response.text
        assert fake.mutations == []
        assert (
            "controller" in response.text.lower()
            if fault == "controller"
            else "rebuild" in response.text.lower()
        )
        assert "Refresh" in response.text or "verify inventory" in response.text


@pytest.mark.parametrize(
    "name,allowed",
    [
        ("locate-start", True),
        ("locate-stop", True),
        ("buzzer-silence", True),
        ("buzzer-disable", True),
        ("buzzer-enable", True),
        ("patrol-read-start", True),
        ("patrol-read-stop", True),
        ("patrol-read-mode", True),
        ("mark-ugood", True),
        ("spindown", True),
        ("foreign-import", True),
        ("foreign-clear", True),
        ("consistency-check-start", False),
        ("consistency-check-stop", False),
        ("consistency-check-mode", False),
        ("hotspare", False),
        ("replace-offline", False),
        ("replace-missing", False),
        ("replace-insert", False),
    ],
)
def test_action_specific_local_topology_requirements(full_app, name, allowed):
    client, app, fake, _contract = full_app
    action = next(item for item in ACTIONS if item.name == name)
    body = prepare(action, SimpleNamespace(app=app), fake)
    config = json.loads((fake.path.parent / "state.json").read_text())
    config["controller"].update(
        {"Virtual Drives": 0, "VD LIST": [], "Drive Groups": 0, "TOPOLOGY": []}
    )
    fake.configure(controller=config["controller"])
    response = client.post(action.path, json=body, follow_redirects=False)
    assert response.status_code == (
        (303 if action.family in {"buzzer", "advanced"} else 200) if allowed else 502
    ), response.text
    assert fake.mutations == ([list(action.command)] if allowed else [])
    if not allowed:
        assert "topology" in response.text
        assert "Refresh" in response.text


def test_hotspare_requires_group_but_not_a_local_vd(full_app):
    client, app, fake, _contract = full_app
    action = next(item for item in ACTIONS if item.name == "hotspare")
    body = prepare(action, SimpleNamespace(app=app), fake)
    config = json.loads((fake.path.parent / "state.json").read_text())
    config["controller"].update({"Virtual Drives": 0, "VD LIST": []})
    fake.configure(controller=config["controller"])
    response = client.post(action.path, json=body, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert fake.mutations == [list(action.command)]


@pytest.mark.parametrize("all_vds", [False, True])
def test_cc_checks_the_scope_of_the_selected_target(full_app, all_vds):
    client, app, fake, _contract = full_app
    action = next(item for item in ACTIONS if item.name == "consistency-check-start")
    prepare(action, SimpleNamespace(app=app), fake)
    config = json.loads((fake.path.parent / "state.json").read_text())
    config["controller"]["Virtual Drives"] = 2
    config["controller"]["VD LIST"].append({"DG/VD": "0/1"})
    fake.configure(controller=config["controller"])
    response = client.post(action.path, json={} if all_vds else {"vd_id": 0})
    assert response.status_code == (502 if all_vds else 200), response.text
    assert fake.mutations == ([] if all_vds else [["/c0/v0", "start", "cc", "J"]])


def test_stop_recovery_survives_incomplete_replacement_and_no_vd(full_app):
    client, app, fake, contract = full_app
    data = policy_data()
    data["commands"] = [item for item in data["commands"] if "set missing" not in item["command"]]
    contract.write_text(json.dumps(data))
    app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": False})
    config = json.loads((fake.path.parent / "state.json").read_text())
    config["controller"].update(
        {"Virtual Drives": 0, "VD LIST": [], "Drive Groups": 0, "TOPOLOGY": []}
    )
    fake.configure(controller=config["controller"])
    response = client.post("/drives/252:0/locate/stop")
    assert response.status_code == 200, response.text
    assert fake.mutations == [["/c0/e252/s0", "stop", "locate", "J"]]


@pytest.mark.parametrize("change", ["controller", "drives", "foreign"])
def test_foreign_rechecks_safety_evidence_after_rebuild_reads(full_app, change):
    import copy

    client, app, fake, _contract = full_app
    action = next(item for item in ACTIONS if item.name == "foreign-import")
    body = prepare(action, SimpleNamespace(app=app), fake)
    config = json.loads((fake.path.parent / "state.json").read_text())
    controller = copy.deepcopy(config["controller"])
    if change == "controller":
        controller["Basics"]["Serial Number"] = "CHANGED-DURING-READS"
        updates = {"controller": controller}
    elif change == "drives":
        controller["PD LIST"][0]["DID"] = 999
        updates = {"controller": controller}
    else:
        updates = {"foreign": {"Total foreign DG Count": 1, "Total foreign drive Count": 1}}
    fake.configure(after_rebuild=updates)
    response = client.post(action.path, json=body)
    assert response.status_code == 502
    assert "changed" in response.text
    assert "Refresh" in response.text
    assert fake.mutations == []


def test_foreign_metadata_change_with_the_same_confirmation_summary_is_refused(full_app):
    import copy

    client, app, fake, _contract = full_app
    action = next(item for item in ACTIONS if item.name == "foreign-import")
    body = prepare(action, SimpleNamespace(app=app), fake)
    config = json.loads((fake.path.parent / "state.json").read_text())
    foreign = copy.deepcopy(config["foreign"])
    foreign["FOREIGN PD LIST"][0]["DID"] = 999
    fake.configure(after_rebuild={"foreign": foreign})
    response = client.post(action.path, json=body)
    assert response.status_code == 502
    assert "foreign configuration changed" in response.text
    assert fake.mutations == []


@pytest.mark.parametrize("name", ["foreign-import", "foreign-clear"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("unknown_rebuild", [False, True])
def test_foreign_live_safety_without_any_stored_snapshot(full_app, name, dry_run, unknown_rebuild):
    from sqlalchemy import delete

    from megaraid_dashboard.db.models import (
        CacheVaultSnapshot,
        ControllerSnapshot,
        PhysicalDriveSnapshot,
        VirtualDriveSnapshot,
    )

    client, app, fake, _contract = full_app
    action = next(item for item in ACTIONS if item.name == name)
    body = prepare(action, SimpleNamespace(app=app), fake)
    body["dry_run"] = dry_run
    with app.state.session_factory() as session, session.begin():
        for model in (
            CacheVaultSnapshot,
            PhysicalDriveSnapshot,
            VirtualDriveSnapshot,
            ControllerSnapshot,
        ):
            session.execute(delete(model))
    config = json.loads((fake.path.parent / "state.json").read_text())
    config["controller"].update(
        {"Virtual Drives": 0, "VD LIST": [], "Drive Groups": 0, "TOPOLOGY": []}
    )
    fake.configure(
        controller=config["controller"],
        rebuild_state="Unknown" if unknown_rebuild else "Not in progress",
    )
    response = client.post(action.path, json=body)
    assert response.status_code == (502 if unknown_rebuild else 200), response.text
    assert fake.mutations == ([] if unknown_rebuild or dry_run else [list(action.command)])
    if unknown_rebuild:
        assert "rebuild" in response.text
        assert "Refresh" in response.text
    elif dry_run:
        assert response.json()["dry_run"] is True
