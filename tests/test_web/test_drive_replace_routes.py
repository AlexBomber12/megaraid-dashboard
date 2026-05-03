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
)
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER

_DEFAULT_SERIAL = "WD-TEST-1234"


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


def test_drive_replace_offline_dry_run_returns_argv_and_skips_runner(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    runner_calls: list[list[str]] = []

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        runner_calls.append(list(_args[0]) if _args else [])
        raise AssertionError("storcli should not be called for dry runs")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL, "dry_run": True},
        )

        assert response.status_code == 200
        assert response.json() == {
            "dry_run": True,
            "step": "offline",
            "enclosure": 2,
            "slot": 0,
            "serial_number": _DEFAULT_SERIAL,
            "argv": ["/c0/e2/s0", "set", "offline", "J"],
        }
        assert runner_calls == []
        _assert_no_audit_event(test_app)


@pytest.mark.parametrize("query_value", ["true", "1", "yes", "TRUE"])
def test_drive_replace_offline_dry_run_query_param_skips_runner(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    query_value: str,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called when dry_run query is true")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            f"/drives/2:0/replace/offline?dry_run={query_value}",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["dry_run"] is True
        assert body["argv"] == ["/c0/e2/s0", "set", "offline", "J"]
        _assert_no_audit_event(test_app)


def test_drive_replace_offline_dry_run_query_param_false_executes_runner(
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
            return _drive_show_payload(state="Onln")
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline?dry_run=false",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 200
        assert "result" in response.json()
        assert runner_calls == [
            ["/c0/e2/s0", "show", "all", "J"],
            ["/c0/e2/s0", "set", "offline", "J"],
        ]


def test_drive_replace_offline_dry_run_query_param_invalid_returns_400(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for invalid dry_run query")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline?dry_run=banana",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "dry_run query parameter must be a boolean"


def test_drive_replace_missing_dry_run_query_param_skips_runner(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called when dry_run query is true")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Offln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing?dry_run=true",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["dry_run"] is True
        assert body["argv"] == ["/c0/e2/s0", "set", "missing", "J"]
        _assert_no_audit_event(test_app)


def test_drive_replace_offline_success_invokes_runner_and_audits(
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
        assert use_sudo is False
        assert binary_path == "/usr/local/sbin/storcli64"
        runner_calls.append(list(args))
        if list(args) == ["/c0/e2/s0", "show", "all", "J"]:
            return _drive_show_payload(state="Onln")
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 200
        assert response.json() == {
            "step": "offline",
            "enclosure": 2,
            "slot": 0,
            "serial_number": _DEFAULT_SERIAL,
            "argv": ["/c0/e2/s0", "set", "offline", "J"],
            "result": {"Controllers": [{"Command Status": {"Status": "Success"}}]},
        }
        assert runner_calls == [
            ["/c0/e2/s0", "show", "all", "J"],
            ["/c0/e2/s0", "set", "offline", "J"],
        ]
        event = _read_single_event(test_app)
        assert event.category == "operator_action"
        assert event.severity == "info"
        assert event.summary == (
            f"replace step offline drive 2:0 serial {_DEFAULT_SERIAL} succeeded"
        )
        assert event.operator_username == "admin"


def test_drive_replace_missing_success_invokes_runner_and_audits(
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
            return _drive_show_payload(state="Offln")
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Offln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 200
        assert runner_calls == [
            ["/c0/e2/s0", "show", "all", "J"],
            ["/c0/e2/s0", "set", "missing", "J"],
        ]
        event = _read_single_event(test_app)
        assert event.summary == (
            f"replace step missing drive 2:0 serial {_DEFAULT_SERIAL} succeeded"
        )


def test_drive_replace_missing_uses_live_state_when_persisted_is_stale(
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
            return _drive_show_payload(state="Offln")
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        # Persisted snapshot still shows Onln because the collector has not yet
        # written a fresh snapshot after the operator ran replace/offline.
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 200
        assert runner_calls == [
            ["/c0/e2/s0", "show", "all", "J"],
            ["/c0/e2/s0", "set", "missing", "J"],
        ]
        event = _read_single_event(test_app)
        assert event.summary == (
            f"replace step missing drive 2:0 serial {_DEFAULT_SERIAL} succeeded"
        )


def test_drive_replace_missing_rejects_when_live_state_is_not_offline(
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
            return _drive_show_payload(state="Onln")
        raise AssertionError("set missing must not run when live state is not Offln")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        # Persisted snapshot says Offln, but the live controller state is Onln.
        # The live state must win: the request is rejected.
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Offln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["state"] == "Onln"
        assert body["step"] == "missing"
        assert "cannot missing" in body["error"]
        assert runner_calls == [["/c0/e2/s0", "show", "all", "J"]]
        _assert_no_audit_event(test_app)


def test_drive_replace_offline_returns_404_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called when snapshot is missing")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "no snapshot for slot"
    assert body["enclosure"] == 2
    assert body["slot"] == 0


def test_drive_replace_offline_returns_409_on_serial_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called on serial mismatch")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": "WD-WRONG"},
        )

        assert response.status_code == 409
        assert response.json() == {
            "error": "serial mismatch",
            "expected": _DEFAULT_SERIAL,
            "supplied": "WD-WRONG",
        }
        _assert_no_audit_event(test_app)


def test_drive_replace_offline_dry_run_returns_409_on_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called on invalid transition")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Rbld")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL, "dry_run": True},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["state"] == "Rbld"
        assert body["step"] == "offline"
        assert "cannot offline" in body["error"]
        _assert_no_audit_event(test_app)


def test_drive_replace_offline_rejects_when_live_state_is_invalid(
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
            return _drive_show_payload(state="Rbld")
        raise AssertionError("set offline must not run when live state forbids it")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        # Persisted snapshot says Onln but the live disk reports Rbld.
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["state"] == "Rbld"
        assert body["step"] == "offline"
        assert "cannot offline" in body["error"]
        assert runner_calls == [["/c0/e2/s0", "show", "all", "J"]]
        _assert_no_audit_event(test_app)


def test_drive_replace_offline_rejects_when_live_serial_mismatches(
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
            return _drive_show_payload(state="Onln", serial_number="OTHER-DRIVE-SN")
        raise AssertionError("set offline must not run on live serial mismatch")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        # Persisted snapshot still names the original drive but the slot now
        # contains a different physical disk. Typed confirmation must reject.
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "live serial mismatch"
        assert body["expected"] == _DEFAULT_SERIAL
        assert body["live"] == "OTHER-DRIVE-SN"
        assert runner_calls == [["/c0/e2/s0", "show", "all", "J"]]
        _assert_no_audit_event(test_app)


def test_drive_replace_missing_rejects_when_live_serial_mismatches(
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
            return _drive_show_payload(state="Offln", serial_number="OTHER-DRIVE-SN")
        raise AssertionError("set missing must not run on live serial mismatch")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Offln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "live serial mismatch"
        assert body["expected"] == _DEFAULT_SERIAL
        assert body["live"] == "OTHER-DRIVE-SN"
        assert runner_calls == [["/c0/e2/s0", "show", "all", "J"]]
        _assert_no_audit_event(test_app)


def test_drive_replace_missing_dry_run_rejects_non_offline_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("dry_run must not invoke storcli")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL, "dry_run": True},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["state"] == "Onln"
        assert body["step"] == "missing"
        assert "cannot missing" in body["error"]
        _assert_no_audit_event(test_app)


def test_drive_replace_uses_latest_snapshot_serial(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("dry_run should not invoke storcli")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        base_time = datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
        _seed_drive(
            test_app,
            serial_number="OLD-SERIAL",
            state="Onln",
            captured_at=base_time,
        )
        _seed_drive(
            test_app,
            serial_number="NEW-SERIAL",
            state="Onln",
            captured_at=base_time + timedelta(minutes=5),
        )
        headers = _csrf_request_headers(client, csrf_headers)
        response_old = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": "OLD-SERIAL", "dry_run": True},
        )
        response_new = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": "NEW-SERIAL", "dry_run": True},
        )

        assert response_old.status_code == 409
        assert response_old.json() == {
            "error": "serial mismatch",
            "expected": "NEW-SERIAL",
            "supplied": "OLD-SERIAL",
        }
        assert response_new.status_code == 200


def test_drive_replace_offline_without_csrf_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without csrf")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        response = client.post(
            "/drives/2:0/replace/offline",
            json={"serial_number": _DEFAULT_SERIAL},
        )

    assert response.status_code == 403


def test_drive_replace_offline_without_auth_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without auth")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as authed_client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(authed_client, csrf_headers)

    with TestClient(test_app) as client:
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

    assert response.status_code == 401


def test_drive_replace_offline_rejects_non_integer_path(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for invalid path values")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/abc:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("path", "expected_field"),
    [
        ("/drives/999:0/replace/offline", "enclosure"),
        ("/drives/-1:0/replace/offline", "enclosure"),
        ("/drives/2:999/replace/offline", "slot"),
        ("/drives/2:-1/replace/offline", "slot"),
        ("/drives/999:0/replace/missing", "enclosure"),
        ("/drives/2:999/replace/missing", "slot"),
    ],
)
def test_drive_replace_rejects_out_of_range_es_before_snapshot_lookup(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    expected_field: str,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for out-of-range es")

    def fail_load_latest_drive(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("snapshot lookup must not run for out-of-range es")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes._load_latest_drive_for_slot",
        fail_load_latest_drive,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            path,
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 400
        assert expected_field in response.json()["error"]
        _assert_no_audit_event(test_app)


def test_drive_replace_offline_rejects_missing_body_fields(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for malformed bodies")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/2:0/replace/offline", headers=headers, json={})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid request body"


def test_drive_replace_offline_audit_failure_returns_500(
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
            return _drive_show_payload(state="Onln")
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
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "audit persistence failed"
        assert body["step"] == "offline"
        assert body["argv"] == ["/c0/e2/s0", "set", "offline", "J"]


def test_drive_replace_offline_records_audit_when_storcli_fails(
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
            return _drive_show_payload(state="Onln")
        raise StorcliCommandFailed("storcli command failed: drive busy", err_msg="drive busy")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli command failed"
        assert body["step"] == "offline"
        assert body["argv"] == ["/c0/e2/s0", "set", "offline", "J"]
        assert "drive busy" in body["detail"]

        event = _read_single_event(test_app)
        assert event.category == "operator_action"
        assert event.summary.startswith(
            f"replace step offline drive 2:0 serial {_DEFAULT_SERIAL} failed"
        )
        assert "StorcliCommandFailed" in event.summary
        assert "drive busy" in event.summary


def test_drive_replace_missing_records_audit_when_storcli_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    from megaraid_dashboard.storcli import StorcliNotAvailable

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        if list(args) == ["/c0/e2/s0", "show", "all", "J"]:
            return _drive_show_payload(state="Offln")
        raise StorcliNotAvailable("storcli sudo access is not available: permission denied")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Offln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli command failed"
        assert "permission denied" in body["detail"]

        event = _read_single_event(test_app)
        assert event.summary.startswith(
            f"replace step missing drive 2:0 serial {_DEFAULT_SERIAL} failed"
        )
        assert "StorcliNotAvailable" in event.summary
        assert "permission denied" in event.summary


def test_drive_replace_offline_returns_502_when_live_precheck_storcli_fails(
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
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli precheck failed"
        assert body["step"] == "offline"
        assert body["enclosure"] == 2
        assert body["slot"] == 0
        assert body["serial_number"] == _DEFAULT_SERIAL
        assert "permission denied" in body["detail"]
        assert runner_calls == [["/c0/e2/s0", "show", "all", "J"]]
        _assert_no_audit_event(test_app)


def test_drive_replace_missing_returns_502_when_live_precheck_parse_fails(
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
        # Malformed JSON payload that does not contain a Drive entry.
        return {"Controllers": [{"Command Status": {"Status": "Success"}, "Response Data": {}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Offln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli precheck failed"
        assert body["step"] == "missing"
        assert "schema" in body["detail"] or "Drive" in body["detail"]
        assert runner_calls == [["/c0/e2/s0", "show", "all", "J"]]
        _assert_no_audit_event(test_app)


def test_drive_replace_offline_returns_500_when_storcli_and_audit_both_fail(
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
            return _drive_show_payload(state="Onln")
        raise StorcliCommandFailed("storcli timed out", err_msg=None)

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "audit persistence failed"
        assert body["step"] == "offline"
        assert body["argv"] == ["/c0/e2/s0", "set", "offline", "J"]
        assert body["storcli_error"] == "storcli timed out"
        assert "result" not in body


@pytest.mark.parametrize(
    ("maintenance", "destructive"),
    [("false", "false"), ("true", "false"), ("false", "true")],
)
def test_drive_replace_offline_blocked_without_modes(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    maintenance: str,
    destructive: str,
) -> None:
    monkeypatch.setenv("MAINTENANCE_MODE", maintenance)
    monkeypatch.setenv("DESTRUCTIVE_MODE", destructive)
    get_settings.cache_clear()

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when destructive mode is disabled")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 403
        body = response.json()
        assert "maintenance_mode" in body["error"]
        assert "destructive_mode" in body["error"]
        _assert_no_audit_event(test_app)


def test_drive_replace_missing_blocked_without_modes(
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
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Offln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/missing",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL},
        )

        assert response.status_code == 403
        _assert_no_audit_event(test_app)


def test_drive_replace_dry_run_allowed_without_modes(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    monkeypatch.setenv("DESTRUCTIVE_MODE", "false")
    get_settings.cache_clear()

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for dry_run")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, serial_number=_DEFAULT_SERIAL, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/drives/2:0/replace/offline",
            headers=headers,
            json={"serial_number": _DEFAULT_SERIAL, "dry_run": True},
        )

        assert response.status_code == 200
        assert response.json()["dry_run"] is True
        _assert_no_audit_event(test_app)


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


def _drive_show_payload(*, state: str, serial_number: str = _DEFAULT_SERIAL) -> dict[str, Any]:
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


def _read_single_event(test_app: FastAPI) -> Event:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return session.scalars(select(Event)).one()


def _assert_no_audit_event(test_app: FastAPI) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        assert session.scalars(select(Event)).all() == []
