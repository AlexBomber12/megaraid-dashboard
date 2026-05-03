from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
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
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER

_OUTGOING_SERIAL = "WD-OLD-1234"
_NEW_SERIAL = "WD-NEW-5678"


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
    monkeypatch.setenv("DESTRUCTIVE_MODE", "true")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_drive_replace_insert_dry_run_returns_argv_and_skips_runner(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for dry runs")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
                "dry_run": True,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "dry_run": True,
            "step": "insert",
            "enclosure": 2,
            "slot": 0,
            "serial_number": _NEW_SERIAL,
            "dg": 0,
            "array": 0,
            "row": 4,
            "argv": [
                "/c0/e2/s0",
                "insert",
                "dg=0",
                "array=0",
                "row=4",
                "J",
            ],
        }
        # No new audit event for dry-run; only the seeded one remains.
        events = _all_events(test_app)
        assert len(events) == 1
        assert "replace step missing" in events[0].summary


def test_drive_replace_insert_returns_404_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when snapshot is missing")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
            },
        )

        assert response.status_code == 404
        body = response.json()
        assert body["error"] == "no snapshot for slot"


def test_drive_replace_insert_returns_409_on_replacement_serial_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run on serial mismatch")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": "WD-WRONG",
                "dg": 0,
                "array": 0,
                "row": 4,
                "dry_run": True,
            },
        )

        assert response.status_code == 409
        body = response.json()
        assert body == {"error": "serial mismatch (replacement drive)"}
        assert _NEW_SERIAL not in response.text


def test_drive_replace_insert_returns_409_when_serial_matches_outgoing(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when supplied serial matches outgoing")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        # Slot now contains a drive with the same serial as the outgoing one
        # (operator working from notes, did not refresh after physical swap).
        _seed_drive(test_app, serial_number=_OUTGOING_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _OUTGOING_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
                "dry_run": True,
            },
        )

        assert response.status_code == 409
        body = response.json()
        assert "OUTGOING drive" in body["error"]


def test_drive_replace_insert_returns_409_when_no_prior_missing_audit(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run without a prior step-missing audit")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        # No audit events at all.
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
                "dry_run": True,
            },
        )

        assert response.status_code == 409
        body = response.json()
        assert "must complete replace step missing" in body["error"]
        assert body["last_audit"] is None


def test_drive_replace_insert_returns_409_when_intervening_action(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when latest audit is not step missing")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(
            test_app,
            outgoing_serial=_OUTGOING_SERIAL,
            occurred_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
        )
        # A later locate-start clobbers the gate.
        _insert_event(
            test_app,
            summary="locate start drive 2:0",
            occurred_at=datetime(2026, 5, 3, 11, 0, tzinfo=UTC),
        )
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
                "dry_run": True,
            },
        )

        assert response.status_code == 409
        body = response.json()
        assert "must complete replace step missing" in body["error"]


def test_drive_replace_insert_success_invokes_runner_and_audits(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    runner_calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        runner_calls.append(list(args))
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["step"] == "insert"
        assert body["argv"] == [
            "/c0/e2/s0",
            "insert",
            "dg=0",
            "array=0",
            "row=4",
            "J",
        ]
        assert body["result"] == {"Controllers": [{"Command Status": {"Status": "Success"}}]}
        assert runner_calls == [
            ["/c0/e2/s0", "insert", "dg=0", "array=0", "row=4", "J"],
        ]
        events = sorted(_all_events(test_app), key=lambda event: event.id)
        assert len(events) == 2
        insert_event = events[-1]
        assert insert_event.category == "operator_action"
        assert insert_event.severity == "info"
        assert insert_event.summary == (
            f"replace step insert drive 2:0 serial {_NEW_SERIAL} dg=0 array=0 row=4 succeeded"
        )
        assert insert_event.operator_username == "admin"


def test_drive_replace_insert_blocked_without_modes(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    monkeypatch.setenv("DESTRUCTIVE_MODE", "false")
    get_settings.cache_clear()

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when destructive mode is disabled")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
            },
        )

        assert response.status_code == 403
        body = response.json()
        assert "maintenance_mode" in body["error"]


def test_drive_replace_insert_records_audit_when_storcli_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    from megaraid_dashboard.storcli import StorcliCommandFailed

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliCommandFailed("storcli command failed: array busy", err_msg="array busy")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
            },
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli command failed"
        assert "array busy" in body["detail"]
        events = sorted(_all_events(test_app), key=lambda event: event.id)
        insert_event = events[-1]
        assert insert_event.summary.startswith(
            f"replace step insert drive 2:0 serial {_NEW_SERIAL} dg=0 array=0 row=4 failed"
        )
        assert "StorcliCommandFailed" in insert_event.summary


def test_drive_replace_insert_audit_failure_returns_500(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
            },
        )

        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "audit persistence failed"
        assert body["argv"] == [
            "/c0/e2/s0",
            "insert",
            "dg=0",
            "array=0",
            "row=4",
            "J",
        ]


def test_drive_replace_insert_without_csrf_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without csrf")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        response = client.post(
            "/drives/2:0/replace/insert",
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
            },
        )

    assert response.status_code == 403


def test_drive_replace_insert_without_auth_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without auth")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as authed_client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(authed_client, csrf_headers)

    with TestClient(test_app) as client:
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
            },
        )

    assert response.status_code == 401


def test_drive_replace_insert_rejects_non_integer_path(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/abc:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 0,
                "array": 0,
                "row": 4,
            },
        )

    assert response.status_code == 400


def test_drive_replace_insert_rejects_invalid_body(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={"serial_number": _NEW_SERIAL},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid request body"


def test_drive_replace_insert_rejects_out_of_range_dg(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dg": 64,
                "array": 0,
                "row": 4,
                "dry_run": True,
            },
        )

    assert response.status_code == 400
    assert "dg" in response.json()["error"]


def test_drive_replace_topology_returns_derivation_for_seeded_slot(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_virtual_drive(test_app, vd_id=0)
        del csrf_headers  # GET request requires no CSRF token
        response = client.get("/drives/2:0/replace/topology")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "enclosure": 2,
            "slot": 0,
            "dg": 0,
            "array": 0,
            "row": 0,
        }


def test_drive_replace_topology_returns_404_when_no_snapshot() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/2:0/replace/topology")

        assert response.status_code == 404
        assert response.json()["error"] == "no snapshot for slot"


def test_drive_replace_topology_returns_404_when_slot_absent() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        response = client.get("/drives/3:9/replace/topology")

        assert response.status_code == 404


def test_drive_replace_topology_rejects_invalid_path() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/abc:0/replace/topology")

        assert response.status_code == 400


def _csrf_request_headers(
    client: TestClient,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> dict[str, str]:
    headers = csrf_headers(client)
    token = headers["X-CSRF-Token"]
    return {**headers, "Cookie": f"__Host-csrf={token}"}


def _seed_drive(
    test_app: FastAPI,
    *,
    serial_number: str,
    state: str,
    enclosure_id: int = 2,
    slot_id: int = 0,
    captured_at: datetime | None = None,
) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    timestamp = captured_at or datetime.now(UTC)
    with session_factory() as session:
        assert isinstance(session, Session)
        controller = ControllerSnapshot(
            captured_at=timestamp,
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
                enclosure_id=enclosure_id,
                slot_id=slot_id,
                device_id=14,
                model="WDC WD30EFRX-68EUZN0",
                serial_number=serial_number,
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
        controller = session.scalars(
            select(ControllerSnapshot).order_by(ControllerSnapshot.captured_at.desc()).limit(1)
        ).one()
        session.add(
            VirtualDriveSnapshot(
                snapshot_id=controller.id,
                vd_id=vd_id,
                name="vd0",
                raid_level="RAID6",
                size_bytes=18_000_000_000_000,
                state="Optl",
                access="RW",
                cache="NRWBD",
            )
        )
        session.commit()


def _seed_replace_missing_audit(
    test_app: FastAPI,
    *,
    outgoing_serial: str,
    enclosure_id: int = 2,
    slot_id: int = 0,
    occurred_at: datetime | None = None,
) -> None:
    when = occurred_at or (datetime.now(UTC) - timedelta(minutes=5))
    _insert_event(
        test_app,
        summary=(
            f"replace step missing drive {enclosure_id}:{slot_id} "
            f"serial {outgoing_serial} succeeded"
        ),
        occurred_at=when,
    )


def _insert_event(
    test_app: FastAPI,
    *,
    summary: str,
    occurred_at: datetime,
    category: str = "operator_action",
    severity: str = "info",
) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        session.add(
            Event(
                occurred_at=occurred_at,
                severity=severity,
                category=category,
                subject="Operator action",
                summary=summary,
                operator_username="admin",
            )
        )
        session.commit()


def _all_events(test_app: FastAPI) -> list[Event]:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return list(session.scalars(select(Event)).all())
