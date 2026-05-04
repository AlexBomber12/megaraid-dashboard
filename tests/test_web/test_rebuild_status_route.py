from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Thread
from time import sleep
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.audit import record_operator_action
from megaraid_dashboard.web.routes import _record_rebuild_complete_once_sync
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
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_drive_rebuild_status_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        calls.append(list(args))
        return _rebuild_payload(percent=42, state="In progress", eta="1234 Minutes")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/2:0/replace/rebuild-status")

    assert response.status_code == 200
    assert response.json() == {
        "enclosure": 2,
        "slot": 0,
        "percent_complete": 42,
        "state": "In progress",
        "time_remaining_minutes": 1234,
    }
    assert calls == [["/c0/e2/s0", "show", "rebuild", "J"]]


def test_drive_rebuild_status_returns_502_when_command_status_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        payload = _rebuild_payload(percent=100, state="Complete")
        payload["Controllers"][0]["Command Status"] = {
            "Status": "Failure",
            "Description": "None",
            "Detailed Status": [{"ErrMsg": "drive missing"}],
        }
        return payload

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "storcli command failed"
    assert "drive missing" in body["detail"]
    assert events == []


def test_drive_rebuild_status_accepts_html_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=42, state="In progress", eta="1234 Minutes")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    headers = {**TEST_AUTH_HEADER, "Accept": "Text/HTML"}
    with TestClient(test_app, headers=headers) as client:
        response = client.get("/drives/2:0/replace/rebuild-status")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'class="rebuild-progress"' in response.text


def test_drive_rebuild_status_records_completion_audit_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=100, state="Complete")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        first = client.get("/drives/2:0/replace/rebuild-status")
        second = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(events) == 1
    assert events[0].category == "operator_action"
    assert events[0].severity == "info"
    assert events[0].summary == "rebuild complete drive 2:0"
    assert events[0].operator_username == "admin"


def test_drive_rebuild_status_records_completion_on_terminal_idle_after_replacement_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = [
        _rebuild_payload(percent=42, state="In progress"),
        _rebuild_payload(percent=0, state="Not in progress"),
    ]

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return statuses.pop(0)

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _record_operator_action(
            test_app,
            summary=(
                "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded"
            ),
        )
        first = client.get("/drives/2:0/replace/rebuild-status")
        second = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert first.status_code == 200
    assert second.status_code == 200
    assert [event.summary for event in events] == [
        "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded",
        "rebuild progress observed drive 2:0",
        "rebuild complete drive 2:0",
    ]


def test_drive_rebuild_status_records_completion_after_zero_percent_active_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = [
        _rebuild_payload(percent=0, state="In progress"),
        _rebuild_payload(percent=0, state="Not in progress"),
    ]

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return statuses.pop(0)

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _record_operator_action(
            test_app,
            summary=(
                "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded"
            ),
        )
        first = client.get("/drives/2:0/replace/rebuild-status")
        second = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert first.status_code == 200
    assert second.status_code == 200
    assert [event.summary for event in events] == [
        "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded",
        "rebuild progress observed drive 2:0",
        "rebuild complete drive 2:0",
    ]
    assert events[1].after_json == {"percent_complete": 0, "state": "In progress"}


def test_drive_rebuild_status_does_not_record_idle_completion_after_insert_without_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=0, state="Not in progress")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    insert_summary = (
        "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded"
    )
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _record_operator_action(test_app, summary=insert_summary)
        response = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert response.status_code == 200
    assert [event.summary for event in events] == [insert_summary]


def test_drive_rebuild_status_does_not_record_idle_completion_without_replacement_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=0, state="Not in progress")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert response.status_code == 200
    assert events == []


def test_drive_rebuild_status_does_not_record_idle_completion_after_missing_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=0, state="Not in progress")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    missing_summary = "replace step missing drive 2:0 serial outgoing-2 succeeded"
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _record_operator_action(test_app, summary=missing_summary)
        response = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert response.status_code == 200
    assert [event.summary for event in events] == [missing_summary]


@pytest.mark.parametrize(
    "failed_summary",
    [
        "replace step missing drive 2:0 serial outgoing-2 failed: storcli reported not succeeded",
        (
            "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 "
            "failed: storcli reported not succeeded"
        ),
    ],
)
def test_drive_rebuild_status_does_not_record_idle_completion_after_failed_replacement_step(
    monkeypatch: pytest.MonkeyPatch,
    failed_summary: str,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=0, state="Not in progress")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _record_operator_action(
            test_app,
            summary=failed_summary,
        )
        response = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert response.status_code == 200
    assert [event.summary for event in events] == [failed_summary]


def test_drive_rebuild_status_does_not_duplicate_completion_audit_after_later_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=100, state="Complete")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        first = client.get("/drives/2:0/replace/rebuild-status")
        _record_operator_action(test_app, summary="locate start drive 2:0")
        second = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert first.status_code == 200
    assert second.status_code == 200
    assert [event.summary for event in events] == [
        "rebuild complete drive 2:0",
        "locate start drive 2:0",
    ]


def test_drive_rebuild_status_records_completion_for_later_replacement_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=100, state="Complete")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        first = client.get("/drives/2:0/replace/rebuild-status")
        _record_operator_action(
            test_app,
            summary=(
                "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded"
            ),
        )
        second = client.get("/drives/2:0/replace/rebuild-status")
        events = _all_events(test_app)

    assert first.status_code == 200
    assert second.status_code == 200
    assert [event.summary for event in events] == [
        "rebuild complete drive 2:0",
        "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded",
        "rebuild complete drive 2:0",
    ]


def test_rebuild_completion_dedupe_is_atomic_for_concurrent_writers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rebuild.db'}")
    get_settings.cache_clear()

    original_record_operator_action = record_operator_action
    first_insert_started = ThreadEvent()

    def slow_record_operator_action(*args: Any, **kwargs: Any) -> Event:
        event = original_record_operator_action(*args, **kwargs)
        first_insert_started.set()
        sleep(0.2)
        return event

    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        slow_record_operator_action,
    )

    test_app = create_app()
    errors: list[BaseException] = []

    def record_completion() -> None:
        try:
            request = _request_for_app(test_app)
            _record_rebuild_complete_once_sync(request=request, enclosure_id=2, slot_id=0)
        except BaseException as exc:
            errors.append(exc)

    with TestClient(test_app, headers=TEST_AUTH_HEADER):
        first = Thread(target=record_completion)
        first.start()
        assert first_insert_started.wait(timeout=2)
        second = Thread(target=record_completion)
        second.start()
        first.join(timeout=2)
        second.join(timeout=2)
        events = _all_events(test_app)

    assert errors == []
    assert [event.summary for event in events] == ["rebuild complete drive 2:0"]


def _rebuild_payload(
    *,
    percent: int,
    state: str,
    eta: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "Progress%": f"{percent}%",
        "State": state,
    }
    if eta is not None:
        row["Estimated Time Left"] = eta
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {"Drive /c0/e2/s0 - Rebuild Progress": [row]},
            }
        ]
    }


def _all_events(test_app: FastAPI) -> list[Event]:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return list(session.scalars(select(Event)).all())


def _record_operator_action(test_app: FastAPI, *, summary: str) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session, session.begin():
        record_operator_action(session, username="admin", message=summary)


def _request_for_app(test_app: FastAPI) -> Any:
    return type(
        "RequestStub",
        (),
        {"app": test_app, "scope": {"user_username": "admin"}},
    )()
