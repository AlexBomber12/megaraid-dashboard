"""Independent full-capability regressions; real routes and fake hardware only."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from starlette.requests import Request

from megaraid_dashboard.db import Event
from megaraid_dashboard.storcli import parser, policy, runner
from megaraid_dashboard.storcli.exceptions import StorcliNotAvailable, StorcliParseError
from megaraid_dashboard.web import routes
from tests.safety.harness import prepare
from tests.safety.scenarios import ACTIONS
from tests.test_full_rollout import full_app as full_app
from tests.test_storcli.test_parser import drive_show_payload
from tests.test_storcli.test_policy import approved as approved
from tests.test_web.test_drive_actions_routes import app_settings  # noqa: F401


def test_clear_refuses_configuration_not_reviewed_by_the_operator(full_app):
    client, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == "foreign-clear")
    body = prepare(action, SimpleNamespace(app=app), fake)
    body["digest"] = "old-page-digest"
    response = client.post(action.path, json=body)
    assert response.status_code == 409
    assert "changed" in response.text
    assert fake.mutations == []


def test_clear_requires_the_reviewed_digest(full_app):
    client, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == "foreign-clear")
    body = prepare(action, SimpleNamespace(app=app), fake)
    body.pop("digest", None)
    response = client.post(action.path, json=body)
    assert response.status_code == 409
    assert "digest" in response.text.lower()
    assert fake.calls == []


@pytest.mark.parametrize("name", ["foreign-import", "foreign-clear"])
def test_equal_geometry_does_not_authorize_changed_foreign_membership(full_app, name):
    client, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == name)
    body = prepare(action, SimpleNamespace(app=app), fake)
    state = json.loads((fake.path.parent / "state.json").read_text())
    state["foreign"]["FOREIGN PD LIST"][0]["DID"] = 999
    fake.configure(foreign=state["foreign"])
    response = client.post(action.path, json=body)
    assert response.status_code == 409
    assert fake.mutations == []


@pytest.mark.parametrize("name", ["replace-offline", "replace-missing"])
def test_replacement_success_without_state_change_is_uncertain(full_app, name):
    client, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == name)
    body = prepare(action, SimpleNamespace(app=app), fake)
    fake.configure(fault="success-no-effect")
    response = client.post(action.path, json=body)
    assert response.status_code == 502
    assert "uncertain" in response.text.lower()
    assert fake.mutations == [list(action.command)]
    with app.state.session_factory() as session:
        event = session.scalar(select(Event).order_by(Event.id.desc()))
        assert "uncertain" in event.summary
        assert not event.summary.endswith("succeeded")


@pytest.mark.parametrize(
    "name", ["mark-ugood", "spindown", "hotspare", "replace-offline", "replace-insert"]
)
def test_live_identity_proof_must_belong_to_the_requested_slot(full_app, name):
    client, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == name)
    body = prepare(action, SimpleNamespace(app=app), fake)
    fake.configure(drive_response_path="/c0/e252/s1")
    response = client.post(action.path, json=body)
    assert response.status_code == 502
    assert fake.mutations == []


def test_stopping_maintenance_during_prechecks_cancels_the_write(full_app, monkeypatch):
    client, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == "foreign-import")
    body = prepare(action, SimpleNamespace(app=app), fake)
    original = routes._query_live_foreign_config

    async def read_then_cancel(**kwargs):
        result = await original(**kwargs)
        app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": False})
        return result

    monkeypatch.setattr(routes, "_query_live_foreign_config", read_then_cancel)
    response = client.post(action.path, json=body)
    assert response.status_code == 502
    assert "maintenance" in response.text.lower()
    assert fake.mutations == []


def test_locate_does_not_revoke_a_verified_missing_step(full_app):
    client, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == "replace-missing")
    body = prepare(action, SimpleNamespace(app=app), fake)
    assert client.post(action.path, json=body).status_code == 200
    assert client.post("/drives/252:0/locate/stop").status_code == 200
    fake.configure(serial="NEW-DRIVE", drive_state="UGood")
    response = client.post(
        "/drives/252:0/replace/insert",
        json={"serial_number": "NEW-DRIVE", "dry_run": False},
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("name", ["mark-ugood", "buzzer-silence"])
def test_success_redirect_keeps_the_reverse_proxy_prefix(full_app, name):
    client, app, fake, _ = full_app
    app.root_path = "/raid"
    action = next(item for item in ACTIONS if item.name == name)
    body = prepare(action, SimpleNamespace(app=app), fake)
    response = client.post(action.path, json=body, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == (
        "/raid/drives/252:0" if name == "mark-ugood" else "/raid/"
    )


@pytest.mark.parametrize("controller", [False, 0.0, 1, None, "0"])
def test_live_drive_rejects_ambiguous_controller_identity(controller):
    payload = drive_show_payload(state="Onln", serial_number="SN")
    payload["Controllers"][0]["Command Status"]["Controller"] = controller
    with pytest.raises(StorcliParseError, match="requested slot"):
        parser.parse_drive_show(payload, requested_drive="/c0/e2/s0")


@pytest.mark.parametrize("change", ["controllers", "rows", "eid"])
def test_live_drive_requires_one_exact_controller_and_slot(change):
    payload = drive_show_payload(state="Onln", serial_number="SN")
    controller = payload["Controllers"][0]
    controller["Command Status"]["Controller"] = 0
    if change == "controllers":
        payload["Controllers"].append(controller)
    elif change == "rows":
        controller["Response Data"]["Drive /c0/e2/s1"] = [{"EID:Slt": "2:1"}]
    else:
        controller["Response Data"]["Drive /c0/e2/s0"][0]["EID:Slt"] = "2:1"
    with pytest.raises(StorcliParseError, match="requested slot"):
        parser.parse_drive_show(payload, requested_drive="/c0/e2/s0")


def test_foreign_confirmation_refuses_non_finite_metadata():
    with pytest.raises(StorcliParseError, match="fingerprinted"):
        parser._foreign_metadata_digest({"unknown": float("nan")}, "summary")


@pytest.mark.parametrize("change", ["changed", "deleted", "binary"])
async def test_wrapper_rechecks_authorization_after_long_preflight(approved, monkeypatch, change):
    binary_changed = False

    def verify_binary(path):
        if binary_changed:
            raise ValueError("binary replaced")

    monkeypatch.setattr(policy, "verify_pinned_binary", verify_binary)

    async def preflight(*args, **kwargs):
        nonlocal binary_changed
        if change == "deleted":
            Path(approved).unlink()
        elif change == "changed":
            data = json.loads(Path(approved).read_text())
            data["controller_serial"] = "CHANGED"
            Path(approved).write_text(json.dumps(data))
        else:
            binary_changed = True

    monkeypatch.setattr(runner, "check_storcli_prewrite", preflight)
    with pytest.raises(StorcliNotAvailable, match="before execution"):
        await runner.run_storcli(
            ["/c0", "set", "alarm=on"],
            use_sudo=False,
            binary_path="never-executed",
            authorization_path=approved,
        )


async def test_wrapper_unreadable_policy_never_starts_preflight(approved):
    Path(approved).unlink()
    with pytest.raises(StorcliNotAvailable, match="cannot be verified"):
        await runner.run_storcli(
            ["/c0", "set", "alarm=on"],
            use_sudo=False,
            binary_path="never-executed",
            authorization_path=approved,
        )


@pytest.mark.parametrize(
    "change,recovery,allowed",
    [
        ({"controller_read_only": True}, False, False),
        ({"controller_action_capability": "none"}, False, False),
        ({"destructive_mode": False}, False, False),
        ({"maintenance_mode": False}, False, False),
        ({"maintenance_mode": False}, True, True),
        ({}, False, True),
    ],
)
def test_execution_guard_rechecks_current_modes(full_app, change, recovery, allowed):
    _, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == "foreign-import")
    prepare(action, SimpleNamespace(app=app), fake)
    approved_settings = app.state.settings
    request = Request(
        {
            "type": "http",
            "app": app,
            "path": "/drives/252:0/locate/stop" if recovery else action.path,
            "scheme": "https",
            "server": ("test", 443),
            "headers": [],
            "route": SimpleNamespace(name="drive_locate_stop" if recovery else "foreign_import"),
        }
    )
    guard = routes._authorization_kwargs(approved_settings, request=request)["before_write"]
    app.state.settings = approved_settings.model_copy(update=change)
    if allowed:
        guard()
    else:
        with pytest.raises(StorcliNotAvailable, match="revoked"):
            guard()
    assert fake.calls == []


@pytest.mark.parametrize("failure", ["read", "serial"])
def test_lost_postcondition_after_success_never_authorizes_a_swap(full_app, monkeypatch, failure):
    client, app, fake, _ = full_app
    action = next(item for item in ACTIONS if item.name == "replace-offline")
    body = prepare(action, SimpleNamespace(app=app), fake)
    original = routes._query_live_drive_show
    reads = 0

    async def lose_proof_after_command(**kwargs):
        nonlocal reads
        reads += 1
        observed = await original(**kwargs)
        if reads == 2:
            if failure == "read":
                raise StorcliParseError("postcondition unavailable")
            return observed.model_copy(update={"serial_number": "CHANGED"})
        return observed

    monkeypatch.setattr(routes, "_query_live_drive_show", lose_proof_after_command)
    response = client.post(action.path, json=body)
    assert response.status_code == 502
    assert "uncertain" in response.text
    assert "Do not remove" in response.text
    assert fake.mutations == [list(action.command)]
    assert 'data-resume-stage=""' in client.get("/drives/252:0").text
