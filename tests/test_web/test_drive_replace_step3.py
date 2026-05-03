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
                "dry_run": True,
            },
        )

        assert response.status_code == 200
        body = response.json()
        # Topology is server-derived; the only seeded drive is the target slot,
        # so it sits at row=0 of the (sole) array member. ``dg`` defaults to 0
        # because no virtual drive was seeded.
        assert body == {
            "dry_run": True,
            "step": "insert",
            "enclosure": 2,
            "slot": 0,
            "serial_number": _NEW_SERIAL,
            "dg": 0,
            "array": 0,
            "row": 0,
            "argv": [
                "/c0/e2/s0",
                "insert",
                "dg=0",
                "array=0",
                "row=0",
                "J",
            ],
        }
        # No new audit event for dry-run; only the seeded one remains.
        events = _all_events(test_app)
        assert len(events) == 1
        assert "replace step missing" in events[0].summary


def test_drive_replace_insert_ignores_client_supplied_topology(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """A crafted request that supplies dg/array/row must not influence the
    storcli command — the server always derives topology from its own snapshot.
    """

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
                # Crafted values that disagree with the server-derived topology.
                "dg": 31,
                "array": 17,
                "row": 99,
                "dry_run": True,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["dg"] == 0
        assert body["array"] == 0
        assert body["row"] == 0
        assert body["argv"] == [
            "/c0/e2/s0",
            "insert",
            "dg=0",
            "array=0",
            "row=0",
            "J",
        ]


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
            json={"serial_number": _NEW_SERIAL},
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
        if list(args) == ["/c0/e2/s0", "show", "all", "J"]:
            return _drive_show_payload(state="UGood", serial_number=_NEW_SERIAL)
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
            json={"serial_number": _NEW_SERIAL},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["step"] == "insert"
        assert body["argv"] == [
            "/c0/e2/s0",
            "insert",
            "dg=0",
            "array=0",
            "row=0",
            "J",
        ]
        assert body["result"] == {"Controllers": [{"Command Status": {"Status": "Success"}}]}
        assert runner_calls == [
            ["/c0/e2/s0", "show", "all", "J"],
            ["/c0/e2/s0", "insert", "dg=0", "array=0", "row=0", "J"],
        ]
        events = sorted(_all_events(test_app), key=lambda event: event.id)
        assert len(events) == 2
        insert_event = events[-1]
        assert insert_event.category == "operator_action"
        assert insert_event.severity == "info"
        assert insert_event.summary == (
            f"replace step insert drive 2:0 serial {_NEW_SERIAL} dg=0 array=0 row=0 succeeded"
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
            json={"serial_number": _NEW_SERIAL},
        )

        assert response.status_code == 403
        body = response.json()
        assert "maintenance_mode" in body["error"]


def test_drive_replace_insert_records_audit_when_storcli_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    from megaraid_dashboard.storcli import StorcliCommandFailed

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        if list(args) == ["/c0/e2/s0", "show", "all", "J"]:
            return _drive_show_payload(state="UGood", serial_number=_NEW_SERIAL)
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
            json={"serial_number": _NEW_SERIAL},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli command failed"
        assert "array busy" in body["detail"]
        events = sorted(_all_events(test_app), key=lambda event: event.id)
        insert_event = events[-1]
        assert insert_event.summary.startswith(
            f"replace step insert drive 2:0 serial {_NEW_SERIAL} dg=0 array=0 row=0 failed"
        )
        assert "StorcliCommandFailed" in insert_event.summary


def test_drive_replace_insert_audit_failure_returns_500(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        if list(args) == ["/c0/e2/s0", "show", "all", "J"]:
            return _drive_show_payload(state="UGood", serial_number=_NEW_SERIAL)
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
            json={"serial_number": _NEW_SERIAL},
        )

        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "audit persistence failed"
        assert body["argv"] == [
            "/c0/e2/s0",
            "insert",
            "dg=0",
            "array=0",
            "row=0",
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
            json={"serial_number": _NEW_SERIAL},
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
            json={"serial_number": _NEW_SERIAL},
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
            json={"serial_number": _NEW_SERIAL},
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
            json={},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid request body"


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


def test_drive_replace_topology_skips_hot_spare_when_computing_row() -> None:
    """A hot spare slot before the target must not bump the row index.

    Without this filter, a 4-member array with a Global Hot Spare at slot 4
    and a fresh drive at slot 5 would compute row=5 (global ordinal) instead
    of row=4 (the failed member's row in the array).
    """
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drives(
            test_app,
            drives=[
                # Four array members.
                (2, 0, "WD-A0", "Onln"),
                (2, 1, "WD-A1", "Onln"),
                (2, 2, "WD-A2", "Onln"),
                (2, 3, "WD-A3", "Onln"),
                # Global hot spare sits in slot 4.
                (2, 4, "WD-GHS", "GHS"),
                # The target slot: the new replacement drive has just been
                # inserted physically, so it shows as UGood.
                (2, 5, _NEW_SERIAL, "UGood"),
                # More array members after the target.
                (2, 6, "WD-A5", "Onln"),
                (2, 7, "WD-A6", "Onln"),
            ],
        )
        response = client.get("/drives/2:5/replace/topology")

        assert response.status_code == 200
        body = response.json()
        # Members ordered by slot: 0, 1, 2, 3, 5(target), 6, 7 → row=4.
        assert body["row"] == 4


def test_drive_replace_insert_skips_substring_slot_audit(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """An audit for ``drive 2:10`` must not satisfy the gate for ``drive 2:1``."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when no real prior step missing audit exists")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(
            test_app,
            serial_number=_NEW_SERIAL,
            state="UGood",
            enclosure_id=2,
            slot_id=1,
        )
        # Audit for slot 2:10 — must not gate slot 2:1.
        _insert_event(
            test_app,
            summary=(f"replace step missing drive 2:10 serial {_OUTGOING_SERIAL} succeeded"),
            occurred_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
        )
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:1/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dry_run": True,
            },
        )

        assert response.status_code == 409
        body = response.json()
        assert "must complete replace step missing" in body["error"]
        assert body["last_audit"] is None


def test_drive_replace_insert_matches_slot_when_audit_ends_at_slot(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """A locate audit ending at ``drive 2:1`` (no trailing space) clobbers the gate."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when latest audit is locate")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(
            test_app,
            serial_number=_NEW_SERIAL,
            state="UGood",
            enclosure_id=2,
            slot_id=1,
        )
        _insert_event(
            test_app,
            summary=(f"replace step missing drive 2:1 serial {_OUTGOING_SERIAL} succeeded"),
            occurred_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
        )
        # Later locate audit for slot 2:1 — summary ends with the slot token.
        _insert_event(
            test_app,
            summary="locate start drive 2:1",
            occurred_at=datetime(2026, 5, 3, 11, 0, tzinfo=UTC),
        )
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:1/replace/insert",
            headers=headers,
            json={
                "serial_number": _NEW_SERIAL,
                "dry_run": True,
            },
        )

        assert response.status_code == 409
        body = response.json()
        assert "must complete replace step missing" in body["error"]
        assert body["last_audit"] == "locate start drive 2:1"


def test_drive_replace_topology_scopes_row_to_target_disk_group() -> None:
    """On a multi-DG host, ``row`` must count only peers in the target's DG.

    With two arrays (DG=0 at slots 2:0..2:2 and DG=1 at slots 2:5..2:7),
    target slot 2:6 sits at row 1 of DG=1, not row 4 of the global member
    list. Without DG-scoped row computation, drives from unrelated arrays
    inflate the row index and misdirect the rebuild.
    """
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        # Earlier snapshot recording the failed slot's prior DG=1 membership.
        _seed_drives(
            test_app,
            drives=[(2, 6, "WD-DG1-1-OLD", "Failed", 1)],
            captured_at=datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
        )
        # Latest snapshot: post-swap, target is UGood with no DG.
        _seed_drives(
            test_app,
            drives=[
                (2, 0, "WD-DG0-0", "Onln", 0),
                (2, 1, "WD-DG0-1", "Onln", 0),
                (2, 2, "WD-DG0-2", "Onln", 0),
                (2, 5, "WD-DG1-0", "Onln", 1),
                (2, 6, _NEW_SERIAL, "UGood", None),
                (2, 7, "WD-DG1-2", "Onln", 1),
            ],
            captured_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        )
        response = client.get("/drives/2:6/replace/topology")
    assert response.status_code == 200
    body = response.json()
    # DG=1 members ordered by slot: 2:5, 2:6, 2:7 → target row=1.
    assert body == {
        "enclosure": 2,
        "slot": 6,
        "dg": 1,
        "array": 0,
        "row": 1,
    }


def test_drive_replace_topology_uses_disk_group_when_dg_and_vd_id_diverge() -> None:
    """DG and VD IDs can diverge after VD delete/recreate workflows.

    A snapshot may report DG=2 with a VD whose ``vd_id=0``. The ``dg``
    argument must follow the physical drive's disk-group membership, not
    the VD's identifier — otherwise ``insert dg=0 ...`` would target a
    disk group that doesn't include the failed slot.
    """
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drives(
            test_app,
            drives=[
                (2, 0, _NEW_SERIAL, "Onln", 2),
                (2, 1, "WD-PEER", "Onln", 2),
            ],
        )
        # VD reuses id 0 even though the drives belong to DG=2.
        _seed_virtual_drive(test_app, vd_id=0)
        response = client.get("/drives/2:0/replace/topology")
        assert response.status_code == 200
        body = response.json()
        assert body["dg"] == 2
        assert body["row"] == 0


def test_drive_replace_topology_refuses_when_target_dg_unknown_in_multi_dg() -> None:
    """If the target slot has no DG history and the snapshot has multiple
    DGs, refuse to derive rather than guess. Returning a destructive ``dg=``
    argument blindly here is what turned Step 3 into a mis-target risk.
    """
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drives(
            test_app,
            drives=[
                (2, 0, "WD-DG0-0", "Onln", 0),
                (2, 1, "WD-DG0-1", "Onln", 0),
                (2, 5, "WD-DG1-0", "Onln", 1),
                (2, 6, _NEW_SERIAL, "UGood", None),
            ],
        )
        response = client.get("/drives/2:6/replace/topology")
        assert response.status_code == 404


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


def test_drive_replace_insert_rejects_when_live_serial_mismatches(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Snapshot still names the new drive but the slot was swapped again
    after the last poll. The live precheck must catch the divergence and
    refuse to issue the destructive insert command.
    """
    runner_calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        runner_calls.append(list(args))
        if list(args) == ["/c0/e2/s0", "show", "all", "J"]:
            return _drive_show_payload(state="UGood", serial_number="WD-OTHER-9999")
        raise AssertionError("insert must not run on live serial mismatch")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

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

        assert response.status_code == 409
        body = response.json()
        assert body == {"error": "live serial mismatch (replacement drive)"}
        # Live serial must not leak in the response.
        assert "WD-OTHER-9999" not in response.text
        assert runner_calls == [["/c0/e2/s0", "show", "all", "J"]]
        # Only the seeded step-missing audit; no insert audit recorded.
        events = _all_events(test_app)
        assert len(events) == 1
        assert "replace step missing" in events[0].summary


def test_drive_replace_insert_returns_502_when_live_precheck_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    from megaraid_dashboard.storcli import StorcliNotAvailable

    runner_calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        runner_calls.append(list(args))
        raise StorcliNotAvailable("storcli sudo access is not available: permission denied")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

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

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli precheck failed"
        assert body["step"] == "insert"
        assert body["enclosure"] == 2
        assert body["slot"] == 0
        assert body["serial_number"] == _NEW_SERIAL
        assert "permission denied" in body["detail"]
        # Only the precheck call was attempted; the destructive insert must not run.
        assert runner_calls == [["/c0/e2/s0", "show", "all", "J"]]
        events = _all_events(test_app)
        assert len(events) == 1
        assert "replace step missing" in events[0].summary


def _drive_show_payload(*, state: str, serial_number: str) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "Drive /c0/e2/s0": [
                        {
                            "EID:Slt": "2:0",
                            "DID": 14,
                            "State": state,
                            "DG": 0,
                            "Size": "2.728 TB",
                            "Intf": "SATA",
                            "Med": "HDD",
                            "Model": "WDC WD30EFRX-68EUZN0",
                        }
                    ],
                    "Drive /c0/e2/s0 - Detailed Information": {
                        "Drive /c0/e2/s0 State": {"Media Error Count": 0},
                        "Drive /c0/e2/s0 Device attributes": {"SN": serial_number},
                        "Drive /c0/e2/s0 Policies/Settings": {},
                    },
                },
            }
        ]
    }


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
    disk_group_id: int | None = 0,
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
                disk_group_id=disk_group_id,
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


_MEMBER_STATES: frozenset[str] = frozenset({"Onln", "Offln", "Rbld", "Failed"})

_DriveTuple = tuple[int, int, str, str] | tuple[int, int, str, str, int | None]


def _default_dg_for_state(state: str) -> int | None:
    """Default DG membership for legacy ``_seed_drives`` callers.

    Member-like states (``Onln``/``Offln``/``Rbld``/``Failed``) historically
    share a single DG=0; spares and unconfigured drives are not in any DG.
    Multi-DG layouts must use the explicit-tuple form of ``_seed_drives``.
    """
    return 0 if state in _MEMBER_STATES else None


def _seed_drives(
    test_app: FastAPI,
    *,
    drives: list[_DriveTuple],
    captured_at: datetime | None = None,
) -> None:
    """Seed a single controller snapshot containing multiple physical drives.

    Each tuple is ``(enclosure_id, slot_id, serial_number, state)`` or
    ``(enclosure_id, slot_id, serial_number, state, disk_group_id)``. When
    omitted, ``disk_group_id`` is inferred from state (member states default
    to ``0``; spares/unconfigured drives default to ``None``).
    """
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
        physical_drives: list[PhysicalDriveSnapshot] = []
        for index, drive in enumerate(drives):
            if len(drive) == 5:
                enclosure_id, slot_id, serial_number, state, disk_group_id = drive
            else:
                enclosure_id, slot_id, serial_number, state = drive
                disk_group_id = _default_dg_for_state(state)
            physical_drives.append(
                PhysicalDriveSnapshot(
                    enclosure_id=enclosure_id,
                    slot_id=slot_id,
                    device_id=10 + index,
                    model="WDC WD30EFRX-68EUZN0",
                    serial_number=serial_number,
                    firmware_version="82.00A82",
                    size_bytes=3_000_000_000_000,
                    interface="SATA",
                    media_type="HDD",
                    state=state,
                    disk_group_id=disk_group_id,
                    temperature_celsius=40,
                    media_errors=0,
                    other_errors=0,
                    predictive_failures=0,
                    smart_alert=False,
                    sas_address=f"0x4433221100000{index:03d}",
                )
            )
        controller.physical_drives = physical_drives
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
