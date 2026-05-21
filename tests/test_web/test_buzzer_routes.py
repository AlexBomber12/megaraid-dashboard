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
from megaraid_dashboard.storcli import StorcliCommandFailed
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
    ("path", "expected_argv", "expected_category", "summary_fragment"),
    [
        (
            "/controller/buzzer/silence",
            ["/c0", "set", "alarm=silence"],
            "controller_buzzer_silence",
            "Buzzer silenced by operator admin",
        ),
        (
            "/controller/buzzer/disable",
            ["/c0", "set", "alarm=off"],
            "controller_buzzer_disable",
            "Buzzer disabled by operator admin",
        ),
        (
            "/controller/buzzer/enable",
            ["/c0", "set", "alarm=on"],
            "controller_buzzer_enable",
            "Buzzer enabled by operator admin",
        ),
    ],
)
def test_buzzer_happy_paths(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
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
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(path, headers=headers, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/controller"
        assert calls == [expected_argv]
        event = _single_event(test_app)
        assert event.category == expected_category
        assert event.severity == "info"
        assert event.subject == "Operator action"
        assert summary_fragment in event.summary
        assert event.summary.endswith("succeeded")
        assert event.operator_username == "admin"


def test_silence_without_csrf_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without csrf")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post("/controller/buzzer/silence")

    assert response.status_code == 403


def test_silence_without_auth_returns_401(
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
        response = client.post("/controller/buzzer/silence", headers=headers)

    assert response.status_code == 401


def test_silence_storcli_failure_returns_502(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        raise StorcliCommandFailed("storcli exited with code 1: alarm command failed")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/buzzer/silence",
            headers=headers,
            follow_redirects=False,
        )

        assert response.status_code == 502
        assert response.json() == {
            "error": "storcli command failed",
            "action": "silence",
            "argv": ["/c0", "set", "alarm=silence"],
            "detail": "storcli exited with code 1: alarm command failed",
        }
        event = _single_event(test_app)
        assert event.category == "controller_buzzer_silence"
        assert event.summary.startswith("Buzzer silenced by operator admin")
        assert "failed: StorcliCommandFailed" in event.summary


def test_silence_without_maintenance_mode_returns_403_and_skips_storcli(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(list(args))
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/buzzer/silence",
            headers=headers,
            follow_redirects=False,
        )

        assert response.status_code == 403
        assert response.json() == {
            "error": "controller buzzer changes require maintenance_mode",
            "maintenance_mode": False,
        }
        assert calls == []
        event = _single_event(test_app)
        assert event.category == "controller_buzzer_silence"
        assert event.summary.startswith("Buzzer silenced by operator admin")
        assert event.summary.endswith("rejected: maintenance_mode required")


def test_silence_without_maintenance_mode_audit_failure_returns_500(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli should not be called without maintenance_mode")

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/buzzer/silence",
            headers=headers,
            follow_redirects=False,
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": "audit persistence failed",
        "action": "silence",
        "rejection_reason": "maintenance_mode required",
    }


def test_silence_audit_failure_returns_500(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
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
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/buzzer/silence",
            headers=headers,
            follow_redirects=False,
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": "audit persistence failed",
        "action": "silence",
        "argv": ["/c0", "set", "alarm=silence"],
        "result": {"Controllers": [{"Command Status": {"Status": "Success"}}]},
    }


def test_silence_audit_failure_after_storcli_failure_returns_500(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        raise StorcliCommandFailed("storcli exited with code 1: alarm command failed")

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
        response = client.post(
            "/controller/buzzer/silence",
            headers=headers,
            follow_redirects=False,
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": "audit persistence failed",
        "action": "silence",
        "argv": ["/c0", "set", "alarm=silence"],
        "storcli_error": "storcli exited with code 1: alarm command failed",
    }


def test_audit_event_summary_includes_operator_username(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/buzzer/silence",
            headers=headers,
            follow_redirects=False,
        )

        assert response.status_code == 303
        event = _single_event(test_app)
        assert "operator admin" in event.summary


def _csrf_request_headers(
    client: TestClient,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> dict[str, str]:
    headers = csrf_headers(client)
    token = headers["X-CSRF-Token"]
    return {**headers, "Cookie": f"__Host-csrf={token}"}


def _single_event(test_app: Any) -> Event:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return session.scalars(select(Event)).one()
