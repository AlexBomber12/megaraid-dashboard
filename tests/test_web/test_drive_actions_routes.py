from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.storcli import StorcliCommandFailed, StorcliError, StorcliNotAvailable
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


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
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_drive_locate_start_returns_200_with_auth_and_csrf(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
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
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/2:0/locate/start", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "command_accepted": True,
        "led_state": "unverified",
        "action": "start",
        "enclosure": 2,
        "slot": 0,
        "result": {"Controllers": [{"Command Status": {"Status": "Success"}}]},
    }
    assert calls == [["/c0/e2/s0", "start", "locate", "J"]]


@pytest.mark.parametrize("action", ["start", "stop"])
def test_drive_locate_records_operator_action(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    action: str,
) -> None:
    threadpool_calls: list[str] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    async def fake_run_in_threadpool(
        func: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> object:
        threadpool_calls.append(getattr(func, "__name__", ""))
        return func(*args, **kwargs)

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr("megaraid_dashboard.web.routes.run_in_threadpool", fake_run_in_threadpool)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(f"/drives/2:0/locate/{action}", headers=headers)

        assert response.status_code == 200
        assert threadpool_calls == ["_record_locate_operator_action_sync"]
        session_factory = test_app.state.session_factory
        assert isinstance(session_factory, sessionmaker)
        with session_factory() as session:
            assert isinstance(session, Session)
            event = session.scalars(select(Event)).one()
            assert event.category == "operator_action"
            assert event.severity == "info"
            assert event.subject == "Operator action"
            assert event.summary == f"locate {action} drive 2:0 succeeded"
            assert event.operator_username == "admin"


@pytest.mark.parametrize("action", ["start", "stop"])
@pytest.mark.parametrize("status", ["Success", "Failure"])
def test_drive_locate_returns_500_when_operator_action_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    action: str,
    status: str,
) -> None:
    # Regression for PR-069: locate endpoints previously returned 200 and
    # silently swallowed the SQLAlchemyError, violating the operator-facing
    # contract that a 200 implies the action was recorded.
    calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        calls.append(args)
        return {
            "Controllers": [
                {"Command Status": {"Status": status, "Description": "Controller busy"}}
            ]
        }

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(f"/drives/2:0/locate/{action}", headers=headers)

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == action
    assert body["enclosure"] == 2
    assert body["slot"] == 0
    assert body["result"] == {
        "Controllers": [{"Command Status": {"Status": status, "Description": "Controller busy"}}]
    }
    assert calls == [["/c0/e2/s0", action, "locate", "J"]]

    assert "Audit could not be saved" in body["message"]
    if status == "Failure":
        assert "Controller busy" in body["message"]
    assert "command_accepted" not in body


def test_drive_locate_start_without_csrf_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without csrf")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post("/drives/2:0/locate/start")

    assert response.status_code == 403


def test_drive_locate_start_without_auth_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without auth")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as authed_client:
        headers = _csrf_request_headers(authed_client, csrf_headers)

    with TestClient(test_app) as client:
        response = client.post(
            "/drives/2:0/locate/start",
            headers=headers,
        )

    assert response.status_code == 401


def test_drive_locate_rejects_non_integer_enclosure(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called for invalid path values")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/abc:0/locate/start", headers=headers)

    assert response.status_code == 400


def test_drive_locate_rejects_unknown_action(
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post("/drives/2:0/locate/foo", headers=headers)

    assert response.status_code == 404


def _csrf_request_headers(
    client: TestClient,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> dict[str, str]:
    headers = csrf_headers(client)
    token = headers["X-CSRF-Token"]
    return {**headers, "Cookie": f"__Host-csrf={token}"}


@pytest.mark.parametrize("action", ["start", "stop"])
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            {"Status": "Failure", "Detailed Status": [{"ErrMsg": "Drive not supported"}]},
            "Drive not supported",
        ),
        ({"Status": "Failure", "Description": "Controller busy"}, "Controller busy"),
        (
            {"Status": "Failure", "Description": "Drive timed out: unsupported operation"},
            "Drive timed out: unsupported operation",
        ),
        ({}, "explicit Success or Failure"),
        ({"Status": "Pending"}, "explicit Success or Failure"),
        (None, "valid Command Status"),
    ],
)
def test_locate_rejects_unconfirmed_json_and_audits_failure(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    action: str,
    status: object,
    expected: str,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"Controllers": [{"Command Status": status}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            f"/drives/2:0/locate/{action}", headers=_csrf_request_headers(client, csrf_headers)
        )
        assert response.status_code == 502
        assert expected in response.json()["message"]
        with test_app.state.session_factory() as session:
            event = session.scalars(select(Event)).one()
            assert event.summary.startswith(f"locate {action} drive 2:0 failed:")
            assert "succeeded" not in event.summary
            assert event.operator_username == "admin"


@pytest.mark.parametrize("action", ["start", "stop"])
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            StorcliCommandFailed("process exited 1", err_msg="private process stderr"),
            "Check controller access",
        ),
        (StorcliCommandFailed("storcli timed out after 30 seconds"), "timed out"),
        (StorcliNotAvailable("private binary path"), "unavailable"),
    ],
)
@pytest.mark.parametrize("audit_fails", [False, True])
def test_locate_process_failures_are_audited_and_never_succeed(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    action: str,
    failure: StorcliError,
    expected: str,
    audit_fails: bool,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise failure

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    if audit_fails:
        monkeypatch.setattr("megaraid_dashboard.web.routes.record_operator_action", fail_audit)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            f"/drives/2:0/locate/{action}", headers=_csrf_request_headers(client, csrf_headers)
        )
        assert response.status_code == (500 if audit_fails else 502)
        message = response.json()["message"]
        assert ("Audit could not be saved" if audit_fails else expected) in message
        assert "private" not in message
        with test_app.state.session_factory() as session:
            events = session.scalars(select(Event)).all()
            if audit_fails:
                assert events == []
            else:
                assert len(events) == 1
                assert events[0].summary.startswith(f"locate {action} drive 2:0 failed:")
