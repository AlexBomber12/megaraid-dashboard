"""Branch coverage for patrol-read endpoints in ``web/routes.py``.

These tests target the error paths in routes.py lines 1591-1972 that are not
exercised by the happy-path service tests in
``tests/test_services/test_patrol_read.py``: storcli parse failures on the
GET endpoint, the missing ``StorcliParseError`` branch on stop, the mode
endpoint's body-validation and builder error branches, and every audit
persistence-failure exit (precheck, mutation, rejection).
"""

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
from megaraid_dashboard.storcli import StorcliNotAvailable, StorcliParseError
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


def _csrf_request_headers(
    client: TestClient,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> dict[str, str]:
    headers = csrf_headers(client)
    token = headers["X-CSRF-Token"]
    return {**headers, "Cookie": f"__Host-csrf={token}"}


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


def _read_events(test_app: Any) -> list[Event]:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return list(session.scalars(select(Event)))


def test_patrol_read_get_returns_502_when_storcli_parse_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover line 1597: the ``except StorcliParseError`` branch on GET.

    The happy-path coverage exercises the generic ``StorcliError`` branch only;
    a ``StorcliParseError`` triggers a distinct error message and is the gate
    that protects operators from a silently malformed firmware payload.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliParseError("patrol-read payload missing Controllers")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/patrol-read")

    assert response.status_code == 502
    assert response.json() == {
        "error": "storcli parse failed",
        "detail": "patrol-read payload missing Controllers",
    }


def test_patrol_read_stop_returns_502_when_storcli_parse_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover line 1675: stop endpoint's ``except StorcliParseError`` precheck.

    The parametrized service test only pairs ``stop`` with
    ``StorcliNotAvailable``, leaving the parse-error branch of the stop
    precheck uncovered.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliParseError("malformed patrolread payload")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/patrol-read/stop",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        events = _read_events(test_app)

    assert response.status_code == 502
    assert response.json() == {
        "error": "storcli parse failed",
        "action": "stop",
        "detail": "malformed patrolread payload",
    }
    assert len(events) == 1
    assert (
        events[0].summary
        == "patrol read stop failed: StorcliParseError: malformed patrolread payload"
    )


def test_patrol_read_mode_returns_400_when_body_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 1739-1740 (and the 1715 early-return) for malformed JSON.

    The body parser falls through to ``_patrol_read_error_response`` and the
    endpoint must short-circuit before any storcli call.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for malformed JSON body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/patrol-read/mode",
            headers={**headers, "Content-Type": "application/json"},
            content="not valid json",
        )

    assert response.status_code == 400
    assert response.json() == {"error": "request body must be valid JSON"}


def test_patrol_read_mode_returns_400_when_body_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 1747-1748: ``PatrolReadModeRequest.model_validate`` failure.

    Pydantic raises ``ValidationError`` when ``mode`` is outside
    ``Literal["auto", "manual", "disable"]``; the endpoint must surface that
    as a 400 with the error detail rather than passing it to the builder.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for invalid mode body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/patrol-read/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "enabled"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid request body"
    assert isinstance(body["detail"], list)
    assert body["detail"], "validation detail must not be empty"


def test_patrol_read_mode_returns_400_when_builder_raises(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 1718-1719: ``except ValueError`` from the builder.

    ``PatrolReadModeRequest`` already constrains ``mode``, so this branch is
    only reachable when ``build_patrol_read_mode_command`` itself raises —
    the equivalent of a builder regression. Patch the builder to pin the
    branch.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when builder raises")

    def raise_value_error(*_args: object, **_kwargs: object) -> Any:
        raise ValueError("synthetic patrol-read mode builder failure")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.build_patrol_read_mode_command",
        raise_value_error,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/patrol-read/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "manual"},
        )

    assert response.status_code == 400
    assert response.json() == {"error": "synthetic patrol-read mode builder failure"}


@pytest.mark.parametrize(
    ("path", "action", "audit_prefix"),
    [
        ("/controller/patrol-read/start", "start", "patrol read start"),
        ("/controller/patrol-read/stop", "stop", "patrol read stop"),
    ],
)
def test_patrol_read_precheck_returns_500_when_storcli_and_audit_both_fail(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    action: str,
    audit_prefix: str,
) -> None:
    """Cover lines 1799-1800: ``_fail_patrol_read_precheck`` audit-fail branch.

    The precheck-failure audit row is the contract that lets operators
    correlate a 502 with a recorded attempt. If the audit insert itself
    fails, the route must escalate to 500 (``audit persistence failed``)
    rather than silently emit a 502 with no trace.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliNotAvailable("storcli unavailable")

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(path, headers=_csrf_request_headers(client, csrf_headers))
        events = _read_events(test_app)

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == action
    assert body["storcli_error"] == "storcli unavailable"
    assert events == [], f"audit row must not exist when {audit_prefix} fails to persist"


def test_patrol_read_mode_returns_500_when_storcli_failure_and_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 1852-1858 and the 1964-1970 inner audit-failure block.

    Returning a storcli payload whose Command Status is ``Failure`` lets
    ``ensure_command_succeeded`` raise ``StorcliCommandFailed`` after
    ``result`` has been assigned. With the audit insert also failing, both
    extras branches (``result is not None`` and ``storcli_error is not None``)
    fire and the response must be a 500 enriched with both fields.
    """

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        del args
        return {
            "Controllers": [
                {
                    "Command Status": {
                        "Status": "Failure",
                        "Detailed Status": [
                            {"ErrMsg": "patrolread mode change rejected by firmware"},
                        ],
                    }
                }
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
        response = client.post(
            "/controller/patrol-read/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "auto"},
        )
        events = _read_events(test_app)

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == "mode"
    assert body["argv"] == ["/c0", "set", "patrolread=on", "mode=auto", "J"]
    assert body["result"]["Controllers"][0]["Command Status"]["Status"] == "Failure"
    assert "patrolread mode change rejected by firmware" in body["storcli_error"]
    assert events == []


@pytest.mark.parametrize(
    ("refresh_error", "expected_error"),
    [
        (StorcliParseError("malformed refresh payload"), "storcli refresh parse failed"),
        (StorcliNotAvailable("storcli unavailable on refresh"), "storcli refresh failed"),
    ],
    ids=["parse_error", "storcli_error"],
)
def test_patrol_read_mode_returns_502_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    refresh_error: Exception,
    expected_error: str,
) -> None:
    """Cover lines 1874-1885: refresh-time ``StorcliParseError`` / ``StorcliError``.

    The mutation succeeds and the audit row is written, but the post-mutation
    refresh fails. Both branches of the refresh handler must produce a 502
    with the action and detail surfaced; the audit row remains because
    refresh failure does not invalidate the recorded mutation.
    """

    set_command = ["/c0", "set", "patrolread=on", "mode=manual", "J"]
    show_command = ["/c0", "show", "patrolread", "J"]
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        calls.append(list(args))
        if list(args) == set_command:
            return {"Controllers": [{"Command Status": {"Status": "Success"}}]}
        if list(args) == show_command:
            raise refresh_error
        raise AssertionError(f"unexpected storcli invocation: {args!r}")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/patrol-read/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "manual"},
        )
        events = _read_events(test_app)

    assert response.status_code == 502
    assert response.json() == {
        "error": expected_error,
        "action": "mode",
        "detail": str(refresh_error),
    }
    assert calls == [set_command, show_command]
    assert len(events) == 1
    assert events[0].summary == "patrol read mode set to manual succeeded"


def test_patrol_read_start_rejection_returns_500_when_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 1915-1916 and 1964-1970 via the start ``already running`` reject.

    ``_reject_patrol_read_mutation`` records ``rejected: already running``;
    when ``record_operator_action`` itself raises ``SQLAlchemyError``, the
    inner sync helper logs and re-raises (1964-1970) and the outer
    coroutine returns a 500 (1915-1916) — never the 409 the rejection
    would normally emit.
    """

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        del args
        return _patrol_payload(mode="Manual", state="Active")

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/patrol-read/start",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        events = _read_events(test_app)

    assert response.status_code == 500
    assert response.json() == {
        "error": "audit persistence failed",
        "action": "start",
        "rejection_reason": "already running",
    }
    assert events == []
