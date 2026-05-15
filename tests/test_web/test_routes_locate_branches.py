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


def _csrf_request_headers(
    client: TestClient,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> dict[str, str]:
    headers = csrf_headers(client)
    token = headers["X-CSRF-Token"]
    return {**headers, "Cookie": f"__Host-csrf={token}"}


@pytest.mark.parametrize("action", ["start", "stop"])
def test_drive_locate_rejects_out_of_range_enclosure(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    action: str,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not be called for out-of-range enclosure")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(f"/drives/999:0/locate/{action}", headers=headers)

    assert response.status_code == 400
    assert "enclosure" in response.json()["error"]


@pytest.mark.parametrize("action", ["start", "stop"])
def test_drive_locate_rejects_out_of_range_slot(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    action: str,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not be called for out-of-range slot")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(f"/drives/2:999/locate/{action}", headers=headers)

    assert response.status_code == 400
    assert "slot" in response.json()["error"]


@pytest.mark.parametrize("action", ["start", "stop"])
def test_drive_locate_rejects_non_integer_slot(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    action: str,
) -> None:
    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not be called for non-integer slot")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(f"/drives/2:xyz/locate/{action}", headers=headers)

    assert response.status_code == 400


def test_drive_locate_stop_returns_500_when_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Mirror of the start-path SQLAlchemyError test for the stop verb so both
    locate verbs lock down the PR-069 contract: a 200 implies the audit row
    was persisted.
    """
    calls: list[list[str]] = []

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del use_sudo, binary_path
        calls.append(list(args))
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
        response = client.post("/drives/2:0/locate/stop", headers=headers)

        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "audit persistence failed"
        assert body["action"] == "stop"
        assert body["enclosure"] == 2
        assert body["slot"] == 0
        assert calls == [["/c0/e2/s0", "stop", "locate", "J"]]

        session_factory = test_app.state.session_factory
        assert isinstance(session_factory, sessionmaker)
        with session_factory() as session:
            assert isinstance(session, Session)
            assert session.scalars(select(Event)).all() == []
