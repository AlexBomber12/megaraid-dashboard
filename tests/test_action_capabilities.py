"""Shared UI/API policy contract; no legacy handler bypass."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from megaraid_dashboard.config import Settings
from megaraid_dashboard.services.action_capabilities import action_disabled_reason


@pytest.mark.parametrize("family", ["locate", "maintenance", "other writes", "future action"])
@pytest.mark.parametrize("capability", ["none", "locate"])
@pytest.mark.parametrize("read_only", [True, False])
def test_disabled_reason(read_only, capability, family):
    settings = SimpleNamespace(
        controller_read_only=read_only, controller_action_capability=capability
    )
    reason = action_disabled_reason(settings, family)
    if read_only:
        assert reason == "Controller actions disabled: read-only rollout."
    elif capability == "locate" and family in {"locate", "maintenance"}:
        assert reason is None
    else:
        assert "unavailable" in reason
        assert "CONTROLLER_ACTION_CAPABILITY=" in reason


def test_defaults_and_invalid_selection(monkeypatch):
    from tests.test_config import set_required_env

    set_required_env(monkeypatch)
    monkeypatch.delenv("CONTROLLER_READ_ONLY", raising=False)
    monkeypatch.delenv("CONTROLLER_ACTION_CAPABILITY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.controller_read_only is True
    assert settings.controller_action_capability == "none"
    for value in ("all", "buzzer", "locate,foreign", ""):
        with pytest.raises(ValidationError, match="controller_action_capability"):
            Settings(_env_file=None, controller_action_capability=value)


# Use the existing isolated application environment, without the legacy gate fixture.
from tests.test_web.test_drive_actions_routes import app_settings  # noqa: E402, F401


@pytest.mark.parametrize("capability", ["none", "locate"])
def test_real_route_capability_gate(monkeypatch, csrf_headers, capability):
    from fastapi.testclient import TestClient

    from megaraid_dashboard.app import create_app
    from tests.conftest import TEST_AUTH_HEADER
    from tests.safety.scenarios import ACTIONS

    monkeypatch.setenv("CONTROLLER_READ_ONLY", "false")
    monkeypatch.setenv("CONTROLLER_ACTION_CAPABILITY", capability)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Disallowed action reached StorCLI")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", forbidden)
    app = create_app()
    with TestClient(app, headers=TEST_AUTH_HEADER) as client:
        token = csrf_headers(client)["X-CSRF-Token"]
        headers = {"X-CSRF-Token": token, "Cookie": f"__Host-csrf={token}"}
        for action in ACTIONS:
            if action.family == "locate" and capability == "locate":
                continue
            response = client.post(action.path, headers=headers, json={})
            assert response.status_code == 403
            assert response.json()["detail"]["error"] == "controller_action_unavailable"
        for path in ("/maintenance/start", "/maintenance/stop"):
            response = client.post(
                path, headers=headers, json={"duration_minutes": 1, "reason": "offline fixture"}
            )
            assert response.status_code == (200 if capability == "locate" else 403)


@pytest.mark.parametrize("active", [False, True])
def test_locate_ui_maintenance_reason(monkeypatch, sample_snapshot, active):
    from fastapi.testclient import TestClient

    from megaraid_dashboard.app import create_app
    from tests.conftest import TEST_AUTH_HEADER
    from tests.test_web.test_drive_detail_actions import _insert_app_snapshot

    monkeypatch.setenv("CONTROLLER_READ_ONLY", "false")
    monkeypatch.setenv("CONTROLLER_ACTION_CAPABILITY", "locate")
    monkeypatch.setenv("MAINTENANCE_MODE", str(active))
    app = create_app()
    with TestClient(app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(app, sample_snapshot)
        response = client.get("/drives/252:0")
        assert response.status_code == 200
        assert ("Locate requires an active maintenance window." in response.text) == (not active)
        assert "Only Locate Start/Stop is supported" in response.text
