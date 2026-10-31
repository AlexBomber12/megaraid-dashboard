"""Render real action states without claiming a mode or maintenance grant we do not have."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from megaraid_dashboard.db import ControllerSnapshot, PhysicalDriveSnapshot, VirtualDriveSnapshot
from megaraid_dashboard.db.dao import set_maintenance_state
from tests.test_full_rollout import full_app as full_app
from tests.test_web.test_drive_actions_routes import app_settings  # noqa: F401


@pytest.mark.parametrize("path", ["/controller", "/drives/252:0"])
@pytest.mark.parametrize("read_only", [True, False])
def test_gate_explains_shared_locks_once(full_app, path, read_only):
    client, app, fake, _contract = full_app
    app.state.settings = app.state.settings.model_copy(
        update={"maintenance_mode": False, "controller_read_only": read_only}
    )
    html = client.get(path).text
    assert html.count('class="action-panel"') == 1
    assert 'id="maintenance" data-live-region' in html
    assert "Start a maintenance window before this action." not in html
    assert "Alerts are suppressed" not in html
    if read_only:
        assert html.count("Controller actions disabled: read-only rollout.") == 1
        assert 'class="action-gate__form"' not in html
    else:
        assert 'class="action-gate__form"' in html
    assert fake.mutations == []


@pytest.mark.parametrize("path", ["/", "/drives", "/events"])
def test_monitoring_pages_do_not_repeat_action_locks(full_app, path):
    client, app, fake, _contract = full_app
    app.state.settings = app.state.settings.model_copy(update={"controller_read_only": True})
    assert "read-only rollout" not in client.get(path).text
    assert fake.mutations == []


@pytest.mark.parametrize("persisted,expired", [(False, False), (True, False), (True, True)])
def test_gate_honors_effective_maintenance_and_expiry(full_app, persisted, expired):
    client, app, fake, _contract = full_app
    app.state.settings = app.state.settings.model_copy(update={"maintenance_mode": True})
    if persisted:
        with app.state.session_factory() as session, session.begin():
            set_maintenance_state(
                session,
                active=True,
                expires_at=datetime.now(UTC) + timedelta(minutes=-1 if expired else 30),
                started_by="reviewer",
            )
    html = client.get("/controller").text
    assert (">Unlocked</span>" in html) is not expired
    assert (">Locked</span>" in html) is expired
    assert ('class="action-gate__form"' in html) is (not persisted or expired)
    assert fake.mutations == []


def test_unknown_operation_modes_are_not_selected(full_app):
    client, app, fake, _contract = full_app
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.raw_json = {
            **(snapshot.raw_json or {}),
            "consistency_check": {"mode": "Unknown", "state": "Unknown"},
            "patrol_read": {"mode": "Unknown", "state": "Unknown"},
        }
    html = client.get("/controller").text
    operations = html.split('data-controller-section="live-operations"')[1].split(
        'data-controller-section="buzzer-control"'
    )[0]
    assert 'aria-pressed="true"' not in operations
    assert "Unknown mode." in operations
    assert "State unknown" in operations
    assert fake.mutations == []


def test_rebuild_has_status_without_guessed_controls(full_app):
    client, app, fake, _contract = full_app
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        snapshot.raw_json = {}
        session.execute(update(PhysicalDriveSnapshot).values(state="Rbld"))
    html = client.get("/controller").text
    operations = html.split('data-controller-section="live-operations"')[1].split(
        'data-controller-section="buzzer-control"'
    )[0]
    assert "Running on drive" in operations
    assert "Automatic rebuild." in operations
    assert "State unknown" not in operations
    assert "<form" not in operations
    assert "<button" not in operations
    assert fake.mutations == []


@pytest.mark.parametrize("state,selected", [("Unknown", []), ("Silenced", ["Enabled"])])
def test_buzzer_does_not_confuse_unknown_or_silenced_with_off(full_app, state, selected):
    client, app, fake, _contract = full_app
    with app.state.session_factory() as session, session.begin():
        session.execute(update(ControllerSnapshot).values(alarm_state=state))
    html = client.get("/controller").text
    assert re.findall(r'<button[^>]*aria-pressed="true"[^>]*>([^<]+)</button>', html) == selected
    assert fake.mutations == []


@pytest.mark.parametrize("cc,patrol", [("Concurrent", "Auto"), ("Sequential", "Manual")])
def test_known_modes_use_selected_buttons_and_keep_observed_interval(full_app, cc, patrol):
    client, app, fake, _contract = full_app
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        session.add(
            VirtualDriveSnapshot(
                snapshot_id=snapshot.id,
                vd_id=1,
                name="second",
                raid_level="RAID5",
                size_bytes=1000000,
                state="Optl",
                access="RW",
                cache="RWBD",
            )
        )
        snapshot.raw_json = {
            **(snapshot.raw_json or {}),
            "consistency_check": {"mode": cc, "state": "Stopped"},
            "patrol_read": {"mode": patrol, "state": "Stopped"},
            "Patrol Read Reoccurrence": "72",
        }
    html = client.get("/controller").text
    operations = html.split('data-controller-section="live-operations"')[1].split(
        'data-controller-section="buzzer-control"'
    )[0]
    assert re.findall(r'<button[^>]*aria-pressed="true"[^>]*>([^<]+)</button>', operations) == [
        cc,
        patrol,
    ]
    assert f"{cc} mode." not in operations
    assert f"{patrol} mode." not in operations
    assert "Interval 72h." in operations
    assert fake.mutations == []


@pytest.mark.parametrize("vd_count", [0, 1, 2])
@pytest.mark.parametrize("state", ["Stopped", "Active", "Unknown"])
def test_cc_mode_controls_hidden_only_for_one_saved_virtual_drive(full_app, vd_count, state):
    client, app, fake, _contract = full_app
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        session.execute(
            delete(VirtualDriveSnapshot).where(VirtualDriveSnapshot.snapshot_id == snapshot.id)
        )
        for vd_id in range(vd_count):
            session.add(
                VirtualDriveSnapshot(
                    snapshot_id=snapshot.id,
                    vd_id=vd_id,
                    name=f"VD {vd_id}",
                    raid_level="RAID5",
                    size_bytes=1000000,
                    state="Optl",
                    access="RW",
                    cache="RWBD",
                )
            )
        snapshot.raw_json = {
            **(snapshot.raw_json or {}),
            "consistency_check": {"mode": "Concurrent", "state": state},
            "patrol_read": {"mode": "Auto", "state": "Stopped"},
        }
    html = client.get("/controller").text
    assert ('aria-label="Consistency Check mode"' in html) is (vd_count != 1)
    assert 'aria-label="Patrol Read mode"' in html
    assert 'action="/controller/consistency-check/start"' in html
    assert 'action="/controller/consistency-check/stop"' in html
    if vd_count == 1:
        assert 'action="/controller/consistency-check/mode"' not in html
        assert "Concurrent mode." not in html
    assert fake.calls == []
