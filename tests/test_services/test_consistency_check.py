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
    ConsistencyCheckMode,
    build_consistency_check_mode_command,
    build_consistency_check_show_command,
    build_consistency_check_show_progress_command,
    build_consistency_check_start_command,
    build_consistency_check_stop_command,
    parse_consistency_check_status,
)
from megaraid_dashboard.storcli import StorcliNotAvailable, run_storcli
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


def test_consistency_check_command_builders_are_exact() -> None:
    assert build_consistency_check_show_command() == ["/c0", "show", "cc", "J"]
    assert build_consistency_check_show_progress_command() == ["/c0/vall", "show", "cc", "J"]
    assert build_consistency_check_start_command(None) == ["/c0/vall", "start", "cc", "J"]
    assert build_consistency_check_start_command(2) == ["/c0/v2", "start", "cc", "J"]
    assert build_consistency_check_stop_command() == ["/c0/vall", "stop", "cc", "J"]
    assert build_consistency_check_mode_command("auto") == [
        "/c0",
        "set",
        "consistencycheck=on",
        "mode=auto",
        "J",
    ]
    assert build_consistency_check_mode_command("manual") == [
        "/c0",
        "set",
        "consistencycheck=on",
        "mode=manual",
        "J",
    ]


def test_consistency_check_mode_builder_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown consistency check mode"):
        build_consistency_check_mode_command(cast(ConsistencyCheckMode, "disable"))


def test_parse_consistency_check_status_from_controller_properties() -> None:
    status = parse_consistency_check_status(
        _cc_show_payload(mode="Auto"),
        _cc_progress_payload(state="Active 25%", extra_props=[("CC Inconsistencies", "0")]),
    )

    assert status.mode == "auto"
    assert status.state == "active"
    assert status.progress_percent == 25
    assert status.last_run_timestamp == "2026/05/04 02:00:00"
    assert status.inconsistency_count == 0
    assert status.has_inconsistency is False


def test_parse_consistency_check_status_detects_inconsistency() -> None:
    status = parse_consistency_check_status(
        _cc_show_payload(mode="Manual"),
        _cc_progress_payload(state="Stopped", extra_props=[("CC Inconsistencies", "3")]),
    )

    assert status.inconsistency_count == 3
    assert status.inconsistency_detail == "3"
    assert status.has_inconsistency is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "argv",
    [
        ["/c0", "show", "cc", "J"],
        ["/c0/vall", "show", "cc", "J"],
        ["/c0/vall", "start", "cc", "J"],
        ["/c0/v2", "start", "cc", "J"],
        ["/c0/vall", "stop", "cc", "J"],
        ["/c0", "set", "consistencycheck=on", "mode=auto", "J"],
        ["/c0", "set", "consistencycheck=on", "mode=manual", "J"],
    ],
)
async def test_consistency_check_runner_templates_are_allowed(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    async def fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> FakeProcess:
        captured["argv"] = args
        return FakeProcess(b'{"Controllers":[]}', b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await run_storcli(argv, use_sudo=False, binary_path="storcli64")

    assert captured["argv"] == ("storcli64", *argv)


def test_consistency_check_get_returns_current_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(list(args))
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload(mode="Auto")
        return _cc_progress_payload(state="Stopped")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/consistency-check")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "auto",
        "state": "stopped",
        "progress_percent": None,
        "last_run_timestamp": "2026/05/04 02:00:00",
        "inconsistency_count": None,
        "inconsistency_detail": None,
    }
    assert calls == [["/c0", "show", "cc", "J"], ["/c0/vall", "show", "cc", "J"]]


def test_consistency_check_start_specific_vd_succeeds_and_audits(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    calls: list[list[str]] = []
    progress_calls = 0

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        nonlocal progress_calls
        calls.append(list(args))
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload(mode="Manual")
        if list(args) == ["/c0/vall", "show", "cc", "J"]:
            progress_calls += 1
            return _cc_progress_payload(state="Stopped" if progress_calls == 1 else "Active")
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"vd_id": 2},
        )
        event = _read_single_event(test_app)

    assert response.status_code == 200
    assert response.json()["state"] == "active"
    assert calls == [
        ["/c0", "show", "cc", "J"],
        ["/c0/vall", "show", "cc", "J"],
        ["/c0/v2", "start", "cc", "J"],
        ["/c0", "show", "cc", "J"],
        ["/c0/vall", "show", "cc", "J"],
    ]
    assert event.summary == "consistency check start vd 2 succeeded"


def test_consistency_check_mode_rejects_without_maintenance_mode_and_audits(
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
            "/controller/consistency-check/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "manual"},
        )
        event = _read_single_event(test_app)

    assert response.status_code == 403
    assert response.json()["error"] == "consistency check changes require maintenance_mode"
    assert calls == []
    assert event.summary == (
        "consistency check mode set to manual rejected: maintenance_mode required"
    )


def test_consistency_check_get_records_inconsistency_event_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload(mode="Auto")
        return _cc_progress_payload(state="Stopped", extra_props=[("CC Inconsistencies", "2")])

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        first_response = client.get("/controller/consistency-check")
        second_response = client.get("/controller/consistency-check")
        events = _read_events(test_app)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(events) == 1
    assert events[0].severity == "warning"
    assert events[0].category == "consistency_check_inconsistency"
    assert events[0].summary == "consistency check inconsistency detected"


def test_consistency_check_mutation_command_error_is_swappable_for_htmx(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(list(args))
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload(mode="Manual")
        if list(args) == ["/c0/vall", "show", "cc", "J"]:
            return _cc_progress_payload(state="Stopped")
        raise StorcliNotAvailable("storcli unavailable")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers={
                **_csrf_request_headers(client, csrf_headers),
                "HX-Request": "true",
            },
        )
        event = _read_single_event(test_app)

    assert response.status_code == 200
    assert response.json()["error"] == "storcli command failed"
    assert response.json()["action"] == "start"
    assert calls == [
        ["/c0", "show", "cc", "J"],
        ["/c0/vall", "show", "cc", "J"],
        ["/c0/vall", "start", "cc", "J"],
    ]
    assert event.summary.endswith("failed: StorcliNotAvailable: storcli unavailable")


def _cc_show_payload(*, mode: str) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "Controller Properties": [
                        {"Ctrl_Prop": "CC Mode", "Value": mode},
                        {"Ctrl_Prop": "CC Last Run", "Value": "2026/05/04 02:00:00"},
                    ]
                },
            }
        ]
    }


def _cc_progress_payload(
    *,
    state: str,
    extra_props: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    controller_properties = [{"Ctrl_Prop": "CC Current State", "Value": state}]
    controller_properties.extend(
        {"Ctrl_Prop": key, "Value": value} for key, value in (extra_props or [])
    )
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {"VD Operation Status": controller_properties},
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
    events = _read_events(test_app)
    assert len(events) == 1
    return events[0]


def _read_events(test_app: Any) -> list[Event]:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return list(session.scalars(select(Event).order_by(Event.id.asc())))
