from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.storcli import StorcliCommandFailed
from megaraid_dashboard.web import routes
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER

_SUCCESS = {"Controllers": [{"Command Status": {"Status": "Success"}}]}


@pytest.fixture(autouse=True)
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", TEST_ADMIN_PASSWORD_HASH)
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("MAINTENANCE_MODE", "true")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("path", "state", "json_body", "expected_argv", "expected_category", "summary_fragment"),
    [
        (
            "/drives/2:0/mark-ubad",
            "UGood",
            None,
            ["/c0/e2/s0", "set", "bad"],
            "operator_action",
            "mark UBad drive 2:0 from state UGood",
        ),
        (
            "/drives/2:0/mark-ugood",
            "UBad",
            None,
            ["/c0/e2/s0", "set", "good"],
            "operator_action",
            "mark UGood drive 2:0 from state UBad",
        ),
        (
            "/drives/2:0/spin-down",
            "Onln",
            None,
            ["/c0/e2/s0", "spindown"],
            "operator_action",
            "remains spun down until reboot or explicit spinup",
        ),
        (
            "/drives/2:0/make-hot-spare",
            "UGood",
            {"dg_id": 0},
            ["/c0/e2/s0", "add", "hotsparedrive", "dgs=0"],
            "operator_action",
            "reversible by setting the spare drive bad",
        ),
    ],
)
def test_advanced_drive_happy_paths(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    state: str,
    json_body: dict[str, int] | None,
    expected_argv: list[str],
    expected_category: str,
    summary_fragment: str,
) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        assert use_sudo is False
        assert binary_path == "/usr/local/sbin/storcli64"
        calls.append(args)
        return _SUCCESS

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state=state)
        if json_body is not None:
            _seed_virtual_drive(test_app, vd_id=json_body["dg_id"] + 10)
            _seed_disk_group_member(test_app, dg_id=json_body["dg_id"])
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            path,
            headers=headers,
            json=json_body,
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/drives/2:0"
        assert calls == [expected_argv]
        event = _single_event(test_app)
        assert event.category == expected_category
        assert event.summary.endswith("succeeded")
        assert summary_fragment in event.summary
        assert event.operator_username == "admin"


@pytest.mark.parametrize(
    ("path", "state", "json_body"),
    [
        ("/drives/2:0/mark-ubad", "Onln", None),
        ("/drives/2:0/mark-ugood", "UGood", None),
        ("/drives/2:0/spin-down", "Failed", None),
        ("/drives/2:0/make-hot-spare", "Onln", {"dg_id": 0}),
    ],
)
def test_advanced_drive_wrong_state_returns_409(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    state: str,
    json_body: dict[str, int] | None,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for rejected state")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state=state)
        _seed_disk_group_member(test_app, dg_id=0)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(path, headers=headers, json=json_body)

    assert response.status_code == 409
    assert response.json()["state"] == state


@pytest.mark.parametrize(
    ("path", "state", "json_body"),
    [
        ("/drives/2:0/mark-ubad", "UGood", None),
        ("/drives/2:0/mark-ugood", "UBad", None),
        ("/drives/2:0/spin-down", "Onln", None),
        ("/drives/2:0/make-hot-spare", "UGood", {"dg_id": 0}),
    ],
)
def test_advanced_drive_without_csrf_returns_403(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    state: str,
    json_body: dict[str, int] | None,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without csrf")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state=state)
        _seed_disk_group_member(test_app, dg_id=0)
        response = client.post(path, json=json_body)

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("path", "state", "json_body"),
    [
        ("/drives/2:0/mark-ubad", "UGood", None),
        ("/drives/2:0/mark-ugood", "UBad", None),
        ("/drives/2:0/spin-down", "Onln", None),
        ("/drives/2:0/make-hot-spare", "UGood", {"dg_id": 0}),
    ],
)
def test_advanced_drive_without_auth_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    state: str,
    json_body: dict[str, int] | None,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without auth")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as authed_client:
        _seed_drive(test_app, state=state)
        _seed_disk_group_member(test_app, dg_id=0)
        headers = _csrf_request_headers(authed_client, csrf_headers)

    with TestClient(test_app) as client:
        response = client.post(path, headers=headers, json=json_body)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("path", "state", "json_body"),
    [
        ("/drives/2:0/mark-ubad", "UGood", None),
        ("/drives/2:0/mark-ugood", "UBad", None),
        ("/drives/2:0/spin-down", "Onln", None),
        ("/drives/2:0/make-hot-spare", "UGood", {"dg_id": 0}),
    ],
)
def test_advanced_drive_storcli_failure_returns_502(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    state: str,
    json_body: dict[str, int] | None,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliCommandFailed("storcli exited with code 1: command failed")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state=state)
        _seed_disk_group_member(test_app, dg_id=0)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(path, headers=headers, json=json_body)

        assert response.status_code == 502
        assert response.json()["error"] == "storcli command failed"
        assert "failed: StorcliCommandFailed" in _single_event(test_app).summary


@pytest.mark.parametrize(
    ("path", "state", "json_body"),
    [
        ("/drives/2:0/mark-ubad", "UGood", None),
        ("/drives/2:0/mark-ugood", "UBad", None),
        ("/drives/2:0/spin-down", "Onln", None),
        ("/drives/2:0/make-hot-spare", "UGood", {"dg_id": 0}),
    ],
)
def test_advanced_drive_audit_failure_returns_500(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    state: str,
    json_body: dict[str, int] | None,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return _SUCCESS

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state=state)
        _seed_disk_group_member(test_app, dg_id=0)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(path, headers=headers, json=json_body)

    assert response.status_code == 500
    assert response.json()["error"] == "audit persistence failed"


def test_make_hot_spare_missing_dg_id_returns_422(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="UGood")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/2:0/make-hot-spare", headers=headers, json={})

    assert response.status_code == 422


@pytest.mark.parametrize("dg_id", ["0", True])
def test_make_hot_spare_rejects_coerced_dg_id_types(
    csrf_headers: Callable[[TestClient], dict[str, str]],
    dg_id: object,
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="UGood")
        _seed_disk_group_member(test_app, dg_id=0)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/make-hot-spare",
            headers=headers,
            json={"dg_id": dg_id},
        )

    assert response.status_code == 422


def test_advanced_drive_action_is_visible_in_audit_view(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return _SUCCESS

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        action_response = client.post(
            "/drives/2:0/spin-down",
            headers=headers,
            follow_redirects=False,
        )
        audit_response = client.get("/audit", follow_redirects=True)

    assert action_response.status_code == 303
    assert audit_response.status_code == 200
    assert "spin down drive" in audit_response.text
    assert 'href="/drives/2:0">2:0</a> from state Onln' in audit_response.text
    assert "by admin" in audit_response.text
    assert 'class="event-actor"' in audit_response.text


def test_make_hot_spare_nonexistent_dg_returns_409(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for nonexistent DG")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="UGood")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/make-hot-spare",
            headers=headers,
            json={"dg_id": 0},
        )

    assert response.status_code == 409
    assert response.json()["dg_id"] == 0


def test_advanced_drive_invalid_path_returns_400(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for invalid path")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/not-int:0/mark-ubad", headers=headers)

    assert response.status_code == 400


def test_advanced_drive_no_snapshot_returns_404(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without a snapshot")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/2:0/mark-ubad", headers=headers)

    assert response.status_code == 404


def test_advanced_drive_without_maintenance_mode_returns_403(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without maintenance_mode")

    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="UGood")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/2:0/mark-ubad", headers=headers)

    assert response.status_code == 403
    assert response.json() == {
        "error": "drive changes require maintenance_mode",
        "maintenance_mode": False,
    }


def test_advanced_drive_audit_failure_after_storcli_failure_returns_500(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliCommandFailed("storcli exited with code 1: command failed")

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="UGood")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/2:0/mark-ubad", headers=headers)

    assert response.status_code == 500
    assert response.json()["storcli_error"] == "storcli exited with code 1: command failed"


async def test_advanced_drive_helper_rejects_missing_dg_id(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="UGood")
        request = _request_for_app(test_app)
        response = await routes._run_advanced_drive_action(
            enclosure="2",
            slot="0",
            request=request,
            action="make_hot_spare",
            dg_id=None,
        )
        del client, csrf_headers

    assert response.status_code == 422


def test_advanced_drive_private_helpers_reject_unknown_action() -> None:
    assert routes._advanced_drive_action_allowed("bad", "UGood", True) is False
    assert (
        routes._advanced_drive_action_rejection("bad", "UGood", None) == "Cannot run drive action"
    )
    with pytest.raises(ValueError, match="unknown advanced drive action"):
        routes._advanced_drive_action_argv("bad", 2, 0, None)
    with pytest.raises(ValueError, match="dg_id is required"):
        routes._advanced_drive_action_argv("make_hot_spare", 2, 0, None)
    with pytest.raises(ValueError, match="unknown advanced drive action"):
        routes._advanced_drive_action_category("bad")
    with pytest.raises(ValueError, match="unknown advanced drive action"):
        routes._advanced_drive_action_audit_message(
            action="bad",
            enclosure_id=2,
            slot_id=0,
            state="UGood",
            dg_id=None,
        )


def test_latest_snapshot_has_disk_group_returns_false_without_snapshot() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER):
        assert (
            routes._latest_snapshot_has_disk_group(request=_request_for_app(test_app), dg_id=0)
            is False
        )


def test_latest_snapshot_has_disk_group_uses_pd_disk_group_not_vd_id() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER):
        _seed_drive(test_app, state="UGood")
        _seed_virtual_drive(test_app, vd_id=7)
        _seed_disk_group_member(test_app, dg_id=3)

        assert routes._latest_snapshot_has_disk_group(
            request=_request_for_app(test_app),
            dg_id=3,
        )
        assert not routes._latest_snapshot_has_disk_group(
            request=_request_for_app(test_app),
            dg_id=7,
        )


def _csrf_request_headers(
    client: TestClient,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> dict[str, str]:
    headers = csrf_headers(client)
    token = headers["X-CSRF-Token"]
    return {**headers, "Cookie": f"__Host-csrf={token}"}


def _request_for_app(test_app: FastAPI) -> Request:
    return Request({"type": "http", "app": test_app, "headers": [], "user_username": "admin"})


def _single_event(test_app: FastAPI) -> Event:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return session.scalars(select(Event)).one()


def _seed_drive(test_app: FastAPI, *, state: str) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        controller = ControllerSnapshot(
            captured_at=datetime.now(UTC),
            model_name="LSI 9270CV-8i",
            serial_number="ctrl-serial",
            firmware_version="23.34.0-0019",
            bios_version="6.36.00.0",
            driver_version="07.727",
            alarm_state="off",
            cv_present=True,
            bbu_present=False,
            roc_temperature_celsius=55,
        )
        controller.physical_drives = [
            PhysicalDriveSnapshot(
                enclosure_id=2,
                slot_id=0,
                device_id=14,
                model="WDC WD30EFRX-68EUZN0",
                serial_number="WD-ADV-0001",
                firmware_version="82.00A82",
                size_bytes=3_000_000_000_000,
                interface="SATA",
                media_type="HDD",
                state=state,
                temperature_celsius=40,
                media_errors=0,
                other_errors=0,
                predictive_failures=0,
                smart_alert=False,
                sas_address="0x4433221100000000",
            )
        ]
        session.add(controller)
        session.commit()


def _seed_virtual_drive(test_app: FastAPI, *, vd_id: int) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        controller = session.scalars(select(ControllerSnapshot)).one()
        session.add(
            VirtualDriveSnapshot(
                snapshot_id=controller.id,
                vd_id=vd_id,
                name=f"vd{vd_id}",
                raid_level="RAID6",
                size_bytes=18_000_000_000_000,
                state="Optl",
                access="RW",
                cache="NRWBD",
            )
        )
        session.commit()


def _seed_disk_group_member(test_app: FastAPI, *, dg_id: int) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        controller = session.scalars(select(ControllerSnapshot)).one()
        controller.physical_drives.append(
            PhysicalDriveSnapshot(
                enclosure_id=2,
                slot_id=1,
                device_id=15,
                model="WDC WD30EFRX-68EUZN0",
                serial_number="WD-ADV-0002",
                firmware_version="82.00A82",
                size_bytes=3_000_000_000_000,
                interface="SATA",
                media_type="HDD",
                state="Onln",
                disk_group_id=dg_id,
                temperature_celsius=39,
                media_errors=0,
                other_errors=0,
                predictive_failures=0,
                smart_alert=False,
                sas_address="0x4433221100000001",
            )
        )
        session.commit()
