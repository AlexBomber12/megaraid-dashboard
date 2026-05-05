from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.drive_actions import (
    PatrolReadMode,
    build_patrol_read_mode_command,
    build_patrol_read_show_command,
    build_patrol_read_start_command,
    build_patrol_read_stop_command,
    parse_patrol_read_status,
)
from megaraid_dashboard.storcli import run_storcli
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        return None

    async def wait(self) -> None:
        return None


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
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_patrol_read_command_builders_are_exact() -> None:
    assert build_patrol_read_show_command() == ["/c0", "show", "patrolread", "J"]
    assert build_patrol_read_start_command() == ["/c0", "start", "patrolread", "J"]
    assert build_patrol_read_stop_command() == ["/c0", "stop", "patrolread", "J"]
    assert build_patrol_read_mode_command("auto") == [
        "/c0",
        "set",
        "patrolread=on",
        "mode=auto",
        "J",
    ]
    assert build_patrol_read_mode_command("manual") == [
        "/c0",
        "set",
        "patrolread=on",
        "mode=manual",
        "J",
    ]
    assert build_patrol_read_mode_command("disable") == ["/c0", "set", "patrolread=off", "J"]


def test_patrol_read_mode_builder_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown patrol read mode"):
        build_patrol_read_mode_command(cast(PatrolReadMode, "enabled"))


def test_parse_patrol_read_status_from_controller_properties() -> None:
    status = parse_patrol_read_status(_patrol_payload(mode="Auto", state="Active 33%"))

    assert status.mode == "auto"
    assert status.state == "active"
    assert status.progress_percent == 33
    assert status.last_run_timestamp == "2026/05/04 01:00:00"
    assert status.is_running is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "argv",
    [
        ["/c0", "show", "patrolread", "J"],
        ["/c0", "start", "patrolread", "J"],
        ["/c0", "stop", "patrolread", "J"],
        ["/c0", "set", "patrolread=on", "mode=auto", "J"],
        ["/c0", "set", "patrolread=on", "mode=manual", "J"],
        ["/c0", "set", "patrolread=off", "J"],
    ],
)
async def test_patrol_read_runner_templates_are_allowed(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> FakeProcess:
        captured["argv"] = args
        return FakeProcess(b'{"Controllers":[]}', b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await run_storcli(argv, use_sudo=False, binary_path="storcli64")

    assert captured["argv"] == ("storcli64", *argv)


def test_patrol_read_get_returns_current_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(list(args))
        return _patrol_payload(mode="Auto", state="Stopped")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/patrol-read")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "auto",
        "state": "stopped",
        "progress_percent": None,
        "last_run_timestamp": "2026/05/04 01:00:00",
    }
    assert calls == [["/c0", "show", "patrolread", "J"]]


def test_patrol_read_start_rejects_when_already_running(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(list(args))
        return _patrol_payload(mode="Manual", state="Active")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/patrol-read/start",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        event = _read_single_event(test_app)

    assert response.status_code == 409
    assert response.json()["error"] == "patrol read already running"
    assert calls == [["/c0", "show", "patrolread", "J"]]
    assert event.summary == "patrol read start rejected: already running"


def test_patrol_read_stop_rejects_when_not_running(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(list(args))
        return _patrol_payload(mode="Auto", state="Stopped")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/patrol-read/stop",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        event = _read_single_event(test_app)

    assert response.status_code == 409
    assert response.json()["error"] == "patrol read is not running"
    assert calls == [["/c0", "show", "patrolread", "J"]]
    assert event.summary == "patrol read stop rejected: not running"


def test_patrol_read_mode_rejects_without_maintenance_mode_and_audits(
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
        response = client.post(
            "/controller/patrol-read/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "manual"},
        )
        event = _read_single_event(test_app)

    assert response.status_code == 403
    assert response.json()["error"] == "patrol read changes require maintenance_mode"
    assert calls == []
    assert event.summary == "patrol read mode set to manual rejected: maintenance_mode required"


@pytest.mark.parametrize(
    ("path", "show_state", "expected_action", "expected_audit"),
    [
        (
            "/controller/patrol-read/start",
            "Stopped",
            "start",
            "patrol read start succeeded",
        ),
        (
            "/controller/patrol-read/stop",
            "Active",
            "stop",
            "patrol read stop succeeded",
        ),
    ],
)
def test_patrol_read_start_and_stop_succeed_and_audit(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    show_state: str,
    expected_action: str,
    expected_audit: str,
) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(list(args))
        if list(args) == ["/c0", "show", "patrolread", "J"]:
            return _patrol_payload(mode="Manual", state=show_state)
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(path, headers=_csrf_request_headers(client, csrf_headers))
        event = _read_single_event(test_app)

    assert response.status_code == 200
    assert response.json()["action"] == expected_action
    assert calls[0] == ["/c0", "show", "patrolread", "J"]
    assert event.summary == expected_audit


def test_patrol_read_mode_set_succeeds_and_audits(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(list(args))
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/patrol-read/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "manual"},
        )
        event = _read_single_event(test_app)

    assert response.status_code == 200
    assert response.json()["argv"] == ["/c0", "set", "patrolread=on", "mode=manual", "J"]
    assert calls == [["/c0", "set", "patrolread=on", "mode=manual", "J"]]
    assert event.summary == "patrol read mode set to manual succeeded"


def _patrol_payload(*, mode: str, state: str) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "Controller Properties": [
                        {"Ctrl_Prop": "PR Mode", "Value": mode},
                        {"Ctrl_Prop": "PR Current State", "Value": state},
                        {"Ctrl_Prop": "PR Last Run", "Value": "2026/05/04 01:00:00"},
                    ]
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


def _read_single_event(test_app: Any) -> Event:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return session.scalars(select(Event)).one()
