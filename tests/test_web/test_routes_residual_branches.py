"""Residual branch coverage for ``web/routes.py`` after PR-072c..g.

PR-072h's primary scope is lines 2958+. While auditing the final coverage
report, three partial branches and two missed lines outside the tail were
still showing as uncovered:

* ``_collector_health`` line 3199 — collector enabled, but neither the
  collector triple nor a live retry task is present.
* ``_task_is_alive`` line 3204 — argument that does not satisfy
  ``_TaskLike``.
* ``_run_patrol_read_mutation`` 1854->1856 and 1856->1858 — the audit
  persistence-failure handler's ``result is not None`` / ``storcli_error is
  not None`` False arcs.

These tests are minimal and focused so the final coverage report comes out
at 100% line + ~100% branch.  The single remaining partial branch
2499->2501 (the ``if detail is not None:`` False arc inside the foreign-
config JSON branch) is defensive dead code under the current control flow
of ``controller_foreign_config``: ``detail`` is only ever assigned in the
same except handlers that assign ``error``, so the False arc is
unreachable without a code change or a ``# pragma`` annotation, both of
which are explicitly disallowed by the task.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.storcli import StorcliNotAvailable
from megaraid_dashboard.web.routes import _task_is_alive
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


@pytest.fixture(autouse=True)
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    env = {
        "ALERT_SMTP_HOST": "smtp.example.test",
        "ALERT_SMTP_PORT": "587",
        "ALERT_SMTP_USER": "alert@example.test",
        "ALERT_SMTP_PASSWORD": "test-token",
        "ALERT_FROM": "alert@example.test",
        "ALERT_TO": "ops@example.test",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": TEST_ADMIN_PASSWORD_HASH,
        "STORCLI_PATH": "/usr/local/sbin/storcli64",
        "METRICS_INTERVAL_SECONDS": "300",
        "COLLECTOR_ENABLED": "false",
        "COLLECTOR_LOCK_PATH": str(tmp_path / "collector.lock"),
        "METRICS_LOCK_PATH": str(tmp_path / "metrics.lock"),
        "DATABASE_URL": "sqlite:///:memory:",
        "LOG_LEVEL": "INFO",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_collector_health_returns_idle_when_collector_enabled_but_no_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover line 3199: ``_collector_health`` falls through to ``"idle"``.

    The collector is enabled, but the application state has neither a live
    collector triple nor a live retry task — for example, when start-up has
    not yet completed.  ``healthz`` must still respond 200/ok.
    """
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        test_app.state.collector = None
        test_app.state.collector_lock_fd = None
        test_app.state.scheduler = None
        test_app.state.collector_retry_task = None

        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "collector": "idle"}


def test_task_is_alive_returns_false_for_non_task_like_input() -> None:
    """Cover line 3204: ``_task_is_alive`` rejects objects without ``done()``."""
    assert _task_is_alive(object()) is False
    assert _task_is_alive(None) is False


def test_patrol_read_mode_returns_500_when_storcli_succeeds_but_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover the 1856->1858 False arc: ``storcli_error is None`` at audit failure.

    The storcli payload succeeds end to end, so ``storcli_error`` stays
    ``None``. With the audit insert raising ``SQLAlchemyError``, the audit
    persistence-failure response carries the ``result`` extra but no
    ``storcli_error`` field.
    """
    monkeypatch.setenv("MAINTENANCE_MODE", "true")
    get_settings.cache_clear()

    success_payload: dict[str, Any] = {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        del args
        return success_payload

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        token_headers = csrf_headers(client)
        token = token_headers["X-CSRF-Token"]
        response = client.post(
            "/controller/patrol-read/mode",
            headers={**token_headers, "Cookie": f"__Host-csrf={token}"},
            json={"mode": "auto"},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == "mode"
    assert body["result"] == success_payload
    assert "storcli_error" not in body


def test_patrol_read_mode_returns_500_when_storcli_raises_before_result(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover the 1854->1856 False arc: ``result is None`` at audit failure.

    ``run_storcli`` raises ``StorcliNotAvailable`` before ever assigning to
    ``result``, so the audit-failure response surfaces only the storcli
    error and omits the ``result`` field entirely.
    """
    monkeypatch.setenv("MAINTENANCE_MODE", "true")
    get_settings.cache_clear()

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliNotAvailable("storcli64 missing")

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        token_headers = csrf_headers(client)
        token = token_headers["X-CSRF-Token"]
        response = client.post(
            "/controller/patrol-read/mode",
            headers={**token_headers, "Cookie": f"__Host-csrf={token}"},
            json={"mode": "auto"},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == "mode"
    assert "result" not in body
    assert "storcli64 missing" in body["storcli_error"]
