from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
)
from megaraid_dashboard.storcli import StorcliCommandFailed
from megaraid_dashboard.web.routes import (
    ForeignConfigImportRequest,
    _extract_serial_from_audit,
    _truncate_audit_detail,
)
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER

_DEFAULT_SERIAL = "WD-TEST-1234"
_NEW_SERIAL = "WD-NEW-5678"
_OUTGOING_SERIAL = "WD-OLD-1234"


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
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_truncate_audit_detail_collapses_and_truncates_long_text() -> None:
    """Cover the ``> max length`` branch in ``_truncate_audit_detail``.

    Long storcli error strings flow into audit-message construction, so the
    truncation path must keep that summary bounded.
    """
    raw = "x" * 300 + "    spaced\n  out\n"
    result = _truncate_audit_detail(raw)
    assert result.endswith("…")
    # The implementation reserves one slot for the ellipsis, so the final
    # collapsed-and-truncated string fits within the documented budget.
    assert len(result) == 200


def test_truncate_audit_detail_passthrough_short_text() -> None:
    assert _truncate_audit_detail("  short    message ") == "short message"


def test_extract_serial_returns_none_when_no_serial_keyword() -> None:
    """Cover the ``return None`` fallback in ``_extract_serial_from_audit``."""
    assert _extract_serial_from_audit("locate start drive 2:0") is None


def test_extract_serial_returns_none_when_keyword_is_trailing() -> None:
    """``serial`` as the last token has no value to return — must yield None."""
    assert _extract_serial_from_audit("rebuild complete drive 2:0 serial") is None


def test_foreign_config_import_request_rejects_blank_confirmation() -> None:
    """Cover the whitespace-only branch of
    ``ForeignConfigImportRequest.confirmation_must_not_be_blank``.

    ``min_length=1`` lets a whitespace-only string through the Field, so the
    custom validator is the gate that rejects it.
    """
    with pytest.raises(ValidationError) as exc_info:
        ForeignConfigImportRequest(confirmation="   ")
    assert "confirmation must not be blank" in str(exc_info.value)


@pytest.mark.parametrize("step", ["offline", "missing"])
def test_drive_replace_step_returns_400_when_build_command_raises(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    step: str,
) -> None:
    """Cover the ``except ValueError`` after ``build_set_offline/missing_command``.

    The initial ``validate_enclosure_slot`` gate catches out-of-range values,
    so the inner builder exception is reachable only via a builder that has
    been independently patched to raise — this test pins that branch.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when builder raises")

    def raise_value_error(*_args: object, **_kwargs: object) -> Any:
        raise ValueError("synthetic builder failure")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    if step == "offline":
        monkeypatch.setattr(
            "megaraid_dashboard.web.routes.build_set_offline_command",
            raise_value_error,
        )
    else:
        monkeypatch.setattr(
            "megaraid_dashboard.web.routes.build_set_missing_command",
            raise_value_error,
        )

    test_app = create_app()
    expected_state = "Onln" if step == "offline" else "Offln"
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state=expected_state)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            f"/drives/2:0/replace/{step}",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL, "dry_run": True},
        )

        assert response.status_code == 400
        assert response.json() == {"error": "synthetic builder failure"}
        _assert_no_audit_event(test_app)


@pytest.mark.parametrize("step", ["offline", "missing"])
def test_drive_replace_step_returns_400_when_body_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    step: str,
) -> None:
    """Cover the ``except ValueError`` branch in ``_parse_replace_request_body``."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for malformed JSON")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(
            test_app,
            serial_number=_DEFAULT_SERIAL,
            state="Onln" if step == "offline" else "Offln",
        )
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            f"/drives/2:0/replace/{step}",
            headers={**headers, "Content-Type": "application/json"},
            content="not valid json",
        )

        assert response.status_code == 400
        assert response.json() == {"error": "request body must be valid JSON"}
        _assert_no_audit_event(test_app)


def test_drive_replace_topology_rejects_out_of_range_enclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover ``validate_enclosure_slot`` ValueError branch in
    ``drive_replace_topology``.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("topology endpoint does not call storcli")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/999:0/replace/topology")

        assert response.status_code == 400
        assert "enclosure" in response.json()["error"]


def test_drive_replace_topology_returns_404_when_snapshot_has_no_physical_drives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the ``if not physical_slots: return None`` branch of
    ``_compute_slot_topology``.

    A controller snapshot exists but recorded zero physical drives — the
    collector can produce this transiently around enumeration faults — so the
    derivation must refuse rather than guess a destructive ``dg=`` argument.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("topology endpoint does not call storcli")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_controller_only(test_app)
        response = client.get("/drives/2:0/replace/topology")

        assert response.status_code == 404
        body = response.json()
        assert body["error"] == "no snapshot for slot"


def test_drive_replace_insert_rejects_out_of_range_enclosure(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover ``validate_enclosure_slot`` ValueError in ``drive_replace_insert``."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("insert must not call storcli for invalid path")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/999:0/replace/insert",
            headers=headers,
            json={"serial_number": _NEW_SERIAL, "dry_run": True},
        )

        assert response.status_code == 400
        assert "enclosure" in response.json()["error"]


def test_drive_replace_insert_rejects_invalid_dry_run_query(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover the ``isinstance(query_dry_run, JSONResponse)`` early-return branch
    inside ``drive_replace_insert``.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("insert must not call storcli for bad query")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert?dry_run=banana",
            headers=headers,
            json={"serial_number": _NEW_SERIAL},
        )

        assert response.status_code == 400
        assert response.json() == {"error": "dry_run query parameter must be a boolean"}


def test_drive_replace_insert_returns_400_when_body_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover ``except ValueError`` branch in ``_parse_insert_request_body``."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("insert must not call storcli for malformed JSON")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers={**headers, "Content-Type": "application/json"},
            content="not valid json",
        )

        assert response.status_code == 400
        assert response.json() == {"error": "request body must be valid JSON"}


def test_drive_replace_insert_returns_409_when_topology_cannot_be_derived(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover the ``topology is None`` branch in ``drive_replace_insert``.

    Topology derivation may legitimately fail (e.g. ambiguous multi-DG layout
    with no recorded membership for the slot); the insert must refuse rather
    than guess a ``dg=`` argument that could rebuild onto a wrong array.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("insert must not call storcli when topology is None")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes._compute_slot_topology",
        lambda **_kwargs: None,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={"serial_number": _NEW_SERIAL, "dry_run": True},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "unable to derive insert topology for slot"
        assert body["enclosure"] == 2
        assert body["slot"] == 0


def test_drive_replace_insert_returns_400_when_build_command_raises(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover ``except ValueError`` after ``build_insert_replacement_command``."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("insert must not call storcli when builder raises")

    def raise_value_error(*_args: object, **_kwargs: object) -> Any:
        raise ValueError("synthetic insert builder failure")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.build_insert_replacement_command",
        raise_value_error,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_NEW_SERIAL, state="UGood")
        _seed_replace_missing_audit(test_app, outgoing_serial=_OUTGOING_SERIAL)
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/insert",
            headers=headers,
            json={"serial_number": _NEW_SERIAL, "dry_run": True},
        )

        assert response.status_code == 400
        assert response.json() == {"error": "synthetic insert builder failure"}


def test_drive_replace_insert_returns_500_when_storcli_and_audit_both_fail(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover the ``storcli_error is not None`` branch inside the insert audit
    persistence-failure path: result is None (storcli failed) and ``extras``
    is enriched with the storcli error before the unified 500 response.
    """

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        if list(args) == ["/c0/e2/s0", "show", "all", "J"]:
            return _drive_show_payload(state="UGood", serial_number=_NEW_SERIAL)
        raise StorcliCommandFailed("storcli insert failed", err_msg="bay locked")

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
        assert body["step"] == "insert"
        assert "storcli_error" in body
        assert "storcli insert failed" in body["storcli_error"]
        assert "result" not in body


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
    disk_group_id: int | None = 0,
) -> None:
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


def _seed_controller_only(test_app: FastAPI) -> None:
    """Seed a ControllerSnapshot with zero PhysicalDriveSnapshots.

    Mirrors the transient state the collector can produce around an
    enumeration fault and is the only path that exercises the
    ``if not physical_slots`` branch in ``_compute_slot_topology``.
    """
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        session.add(
            ControllerSnapshot(
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
        )
        session.commit()


def _seed_replace_missing_audit(
    test_app: FastAPI,
    *,
    outgoing_serial: str,
    enclosure_id: int = 2,
    slot_id: int = 0,
) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        session.add(
            Event(
                occurred_at=datetime.now(UTC) - timedelta(minutes=5),
                severity="info",
                category="operator_action",
                subject="Operator action",
                summary=(
                    f"replace step missing drive {enclosure_id}:{slot_id} "
                    f"serial {outgoing_serial} succeeded"
                ),
                operator_username="admin",
            )
        )
        session.commit()


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


def _assert_no_audit_event(test_app: FastAPI) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        assert session.scalars(select(Event)).all() == []
