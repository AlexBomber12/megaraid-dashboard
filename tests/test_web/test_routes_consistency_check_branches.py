"""Branch coverage for consistency-check endpoints in ``web/routes.py``.

These tests target the error paths in routes.py lines 1973-2453 that are not
exercised by the happy-path service tests in
``tests/test_services/test_consistency_check.py``: storcli failures on the
GET endpoint, body-validation rejects on start/mode, maintenance-mode rejects
on start/stop, state-machine rejects (already running / not running) on the
mutators, refresh-phase failures inside ``_run_consistency_check_mutation``,
the audit-persistence-failure escalations on every audit write site, and the
SQLAlchemyError logging branch inside the inconsistency-event helper.
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


def _cc_show_payload(*, mode: str = "Manual") -> dict[str, Any]:
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
    state: str = "Stopped",
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


def _read_events(test_app: Any) -> list[Event]:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return list(session.scalars(select(Event).order_by(Event.id.asc())))


# ---------------------------------------------------------------------------
# GET /controller/consistency-check error paths (lines 1979-1990)
# ---------------------------------------------------------------------------


def test_consistency_check_get_returns_502_when_storcli_parse_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover lines 1979-1984: the ``except StorcliParseError`` branch on GET.

    A parse error from either of the two probe commands must surface a 502
    with the parser detail so operators can distinguish malformed firmware
    output from a missing binary or a permissions failure.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliParseError("consistency-check payload missing Controllers")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/consistency-check")

    assert response.status_code == 502
    assert response.json() == {
        "error": "storcli parse failed",
        "detail": "consistency-check payload missing Controllers",
    }


def test_consistency_check_get_returns_502_when_storcli_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover lines 1985-1990: the ``except StorcliError`` branch on GET."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliNotAvailable("storcli64 not installed")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/consistency-check")

    assert response.status_code == 502
    assert response.json() == {
        "error": "storcli command failed",
        "detail": "storcli64 not installed",
    }


# ---------------------------------------------------------------------------
# POST /controller/consistency-check/start body parsing (1999, 2130, 2132, 2135)
# ---------------------------------------------------------------------------


def test_consistency_check_start_rejects_invalid_json_body(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 1999 and 2130 (and 2165-2167): malformed JSON short-circuit.

    The body parser returns ``_request_payload``'s 400 response (lines
    2165-2167); the start handler must surface it directly via the
    ``isinstance(body, JSONResponse)`` branch (1998-1999) without touching
    storcli.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for malformed JSON body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers={
                **_csrf_request_headers(client, csrf_headers),
                "Content-Type": "application/json",
            },
            content="not-valid-json",
        )

    assert response.status_code == 400
    assert response.json() == {"error": "request body must be valid JSON"}


def test_consistency_check_mode_accepts_form_encoded_body(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover line 2165: the ``request.form()`` branch in ``_request_payload``.

    When the client posts ``application/x-www-form-urlencoded`` (the default
    for HTMX form submissions without an explicit JSON encoder) the body
    parser must coerce the form data into a dict before validating it.
    """

    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        calls.append(list(args))
        if list(args) == ["/c0", "set", "consistencycheck=on", "mode=manual", "J"]:
            return {"Controllers": [{"Command Status": {"Status": "Success"}}]}
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload(mode="Manual")
        if list(args) == ["/c0/vall", "show", "cc", "J"]:
            return _cc_progress_payload(state="Stopped")
        raise AssertionError(f"unexpected storcli invocation: {args!r}")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            data={"mode": "manual"},
        )

    assert response.status_code == 200
    assert ["/c0", "set", "consistencycheck=on", "mode=manual", "J"] in calls


def test_consistency_check_start_rejects_non_object_body(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover line 2173: ``_request_payload`` rejects non-dict JSON payloads."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for non-object body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers=_csrf_request_headers(client, csrf_headers),
            json=[1, 2, 3],
        )

    assert response.status_code == 400
    assert response.json() == {"error": "request body must be an object"}


def test_consistency_check_start_treats_empty_vd_id_as_all(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2131-2132: the ``vd_id == ''`` normalization.

    HTML forms commonly submit blank text fields as empty strings; the body
    parser must coerce that to ``None`` (= start against ``/c0/vall``) rather
    than reject it as a non-integer.
    """

    calls: list[list[str]] = []
    progress_states = iter(["Stopped", "Active"])

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        calls.append(list(args))
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload()
        if list(args) == ["/c0/vall", "show", "cc", "J"]:
            return _cc_progress_payload(state=next(progress_states))
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"vd_id": ""},
        )

    assert response.status_code == 200
    assert ["/c0/vall", "start", "cc", "J"] in calls


def test_consistency_check_start_rejects_invalid_vd_id(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2135-2136: ``ConsistencyCheckStartRequest`` validation error."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for invalid vd_id body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"vd_id": -1},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid request body"
    assert isinstance(body["detail"], list)
    assert body["detail"], "validation detail must not be empty"


# ---------------------------------------------------------------------------
# POST /controller/consistency-check/start precheck error paths (2007-2033)
# ---------------------------------------------------------------------------


def test_consistency_check_start_rejects_without_maintenance_mode(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2006-2012: ``settings.maintenance_mode`` guard on start."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when maintenance_mode is off")

    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"vd_id": 2},
        )
        events = _read_events(test_app)

    assert response.status_code == 403
    assert response.json() == {
        "error": "consistency check changes require maintenance_mode",
        "maintenance_mode": False,
    }
    assert len(events) == 1
    assert events[0].summary == ("consistency check start vd 2 rejected: maintenance_mode required")


@pytest.mark.parametrize(
    ("exc", "expected_error"),
    [
        (StorcliParseError("malformed precheck payload"), "storcli parse failed"),
        (StorcliNotAvailable("storcli unavailable"), "storcli command failed"),
    ],
    ids=["parse_error", "storcli_error"],
)
def test_consistency_check_start_502_when_precheck_storcli_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    exc: Exception,
    expected_error: str,
) -> None:
    """Cover lines 2016-2031: both precheck failure branches on start.

    The audit row records the failure so operators can correlate a 502 with
    a recorded attempt; the response surfaces the storcli detail.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise exc

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        events = _read_events(test_app)

    assert response.status_code == 502
    assert response.json() == {
        "error": expected_error,
        "action": "start",
        "detail": str(exc),
    }
    assert len(events) == 1
    assert events[0].summary == (f"consistency check start all failed: {type(exc).__name__}: {exc}")


def test_consistency_check_start_rejects_when_already_running(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2032-2044: the ``already running`` rejection on start."""

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload()
        if list(args) == ["/c0/vall", "show", "cc", "J"]:
            return _cc_progress_payload(state="Active 50%")
        raise AssertionError(f"unexpected storcli invocation: {args!r}")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/start",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"vd_id": 2},
        )
        events = _read_events(test_app)

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "consistency check already running"
    assert body["target"] == "2"
    assert body["consistency_check"]["state"] == "active"
    assert len(events) == 1
    assert events[0].summary == ("consistency check start vd 2 rejected: already running")


# ---------------------------------------------------------------------------
# POST /controller/consistency-check/stop branches (2056-2101)
# ---------------------------------------------------------------------------


def test_consistency_check_stop_rejects_without_maintenance_mode(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2057-2063: stop ``maintenance_mode`` guard."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when maintenance_mode is off")

    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/stop",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        events = _read_events(test_app)

    assert response.status_code == 403
    assert response.json() == {
        "error": "consistency check changes require maintenance_mode",
        "maintenance_mode": False,
    }
    assert len(events) == 1
    assert events[0].summary == ("consistency check stop rejected: maintenance_mode required")


@pytest.mark.parametrize(
    ("exc", "expected_error"),
    [
        (StorcliParseError("malformed stop precheck"), "storcli parse failed"),
        (StorcliNotAvailable("storcli unavailable"), "storcli command failed"),
    ],
    ids=["parse_error", "storcli_error"],
)
def test_consistency_check_stop_502_when_precheck_storcli_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    exc: Exception,
    expected_error: str,
) -> None:
    """Cover lines 2065-2082: both precheck failure branches on stop."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise exc

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/stop",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        events = _read_events(test_app)

    assert response.status_code == 502
    assert response.json() == {
        "error": expected_error,
        "action": "stop",
        "detail": str(exc),
    }
    assert len(events) == 1
    assert events[0].summary == (f"consistency check stop failed: {type(exc).__name__}: {exc}")


def test_consistency_check_stop_rejects_when_not_running(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2083-2094: the ``not running`` rejection on stop."""

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload()
        if list(args) == ["/c0/vall", "show", "cc", "J"]:
            return _cc_progress_payload(state="Stopped")
        raise AssertionError(f"unexpected storcli invocation: {args!r}")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/stop",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        events = _read_events(test_app)

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "consistency check is not running"
    assert body["consistency_check"]["state"] == "stopped"
    assert len(events) == 1
    assert events[0].summary == "consistency check stop rejected: not running"


def test_consistency_check_stop_succeeds_when_running(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2095-2101: the happy-path mutation dispatch on stop.

    The test pairs an ``Active`` precheck with a successful storcli stop and
    a ``Stopped`` refresh so the response carries the freshly-refreshed
    state.
    """

    calls: list[list[str]] = []
    progress_states = iter(["Active 25%", "Stopped"])

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        calls.append(list(args))
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload()
        if list(args) == ["/c0/vall", "show", "cc", "J"]:
            return _cc_progress_payload(state=next(progress_states))
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/stop",
            headers=_csrf_request_headers(client, csrf_headers),
        )
        events = _read_events(test_app)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "stopped"
    assert ["/c0/vall", "stop", "cc", "J"] in calls
    assert len(events) == 1
    assert events[0].summary == "consistency check stop succeeded"


# ---------------------------------------------------------------------------
# POST /controller/consistency-check/mode body parsing (2108, 2111-2112, 2148, 2151-2152)
# ---------------------------------------------------------------------------


def test_consistency_check_mode_rejects_invalid_json_body(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2107-2108 and 2148: malformed JSON on mode endpoint."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for malformed JSON body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/mode",
            headers={
                **_csrf_request_headers(client, csrf_headers),
                "Content-Type": "application/json",
            },
            content="not-valid-json",
        )

    assert response.status_code == 400
    assert response.json() == {"error": "request body must be valid JSON"}


def test_consistency_check_mode_rejects_invalid_mode_field(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2151-2152: ``ConsistencyCheckModeRequest`` validation error."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for invalid mode body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "disable"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid request body"
    assert isinstance(body["detail"], list)
    assert body["detail"], "validation detail must not be empty"


def test_consistency_check_mode_returns_400_when_builder_raises(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2111-2112: ``except ValueError`` from the mode builder.

    ``ConsistencyCheckModeRequest`` already pins ``mode`` to the literal set,
    so this branch is only reachable when the builder itself rejects the
    value (a builder regression). Patch the builder to pin the branch.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run when builder raises")

    def raise_value_error(*_args: object, **_kwargs: object) -> Any:
        raise ValueError("synthetic consistency-check mode builder failure")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.build_consistency_check_mode_command",
        raise_value_error,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "manual"},
        )

    assert response.status_code == 400
    assert response.json() == {"error": "synthetic consistency-check mode builder failure"}


# ---------------------------------------------------------------------------
# _fail_consistency_check_precheck audit-failure (lines 2223-2236)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "action", "audit_prefix"),
    [
        ("/controller/consistency-check/start", "start", "consistency check start all"),
        ("/controller/consistency-check/stop", "stop", "consistency check stop"),
    ],
)
def test_consistency_check_precheck_returns_500_when_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    path: str,
    action: str,
    audit_prefix: str,
) -> None:
    """Cover lines 2223-2236 and 2391-2397: precheck audit-write failure.

    The audit row is the contract that lets operators correlate a 502 with
    a recorded attempt. If the audit insert itself fails, the route must
    escalate to 500 (``audit persistence failed``) rather than silently
    return a 502 with no trace.
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


# ---------------------------------------------------------------------------
# _run_consistency_check_mutation audit-failure branches (2284-2290)
# ---------------------------------------------------------------------------


def test_consistency_check_mode_returns_500_when_storcli_failure_and_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2284-2290 with both ``result`` and ``storcli_error`` set.

    Returning a storcli payload whose Command Status is ``Failure`` lets
    ``ensure_command_succeeded`` raise after ``result`` is assigned. With
    the audit insert also failing, the response must be a 500 enriched with
    both fields.
    """

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        del args
        return {
            "Controllers": [
                {
                    "Command Status": {
                        "Status": "Failure",
                        "Detailed Status": [
                            {"ErrMsg": "cc mode change rejected by firmware"},
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
            "/controller/consistency-check/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "auto"},
        )
        events = _read_events(test_app)

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == "mode"
    assert body["argv"] == ["/c0", "set", "consistencycheck=on", "mode=auto", "J"]
    assert body["result"]["Controllers"][0]["Command Status"]["Status"] == "Failure"
    assert "cc mode change rejected by firmware" in body["storcli_error"]
    assert events == []


def test_consistency_check_mode_returns_500_when_storcli_unavailable_and_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover the ``result is None`` branch (2286->2288) in the audit fallback.

    ``StorcliNotAvailable`` raises before ``result`` is assigned, so the
    failure-extras dict carries ``storcli_error`` but not ``result`` —
    different from the firmware-rejection case where both are present.
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
        response = client.post(
            "/controller/consistency-check/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "manual"},
        )
        events = _read_events(test_app)

    assert response.status_code == 500
    body = response.json()
    assert body == {
        "error": "audit persistence failed",
        "action": "mode",
        "argv": ["/c0", "set", "consistencycheck=on", "mode=manual", "J"],
        "storcli_error": "storcli unavailable",
    }
    assert "result" not in body
    assert events == []


def test_consistency_check_mode_returns_500_when_storcli_succeeds_but_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover the ``storcli_error is None`` branch (2288->2290) in the audit fallback.

    Storcli reports Success, so ``ensure_command_succeeded`` passes and
    ``storcli_error`` stays ``None``; only ``result`` is attached to the
    audit-failure response.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
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
        response = client.post(
            "/controller/consistency-check/mode",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"mode": "auto"},
        )
        events = _read_events(test_app)

    assert response.status_code == 500
    body = response.json()
    assert body == {
        "error": "audit persistence failed",
        "action": "mode",
        "argv": ["/c0", "set", "consistencycheck=on", "mode=auto", "J"],
        "result": {"Controllers": [{"Command Status": {"Status": "Success"}}]},
    }
    assert "storcli_error" not in body
    assert events == []


# ---------------------------------------------------------------------------
# Refresh-phase failure in _run_consistency_check_mutation (2307-2326)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("refresh_error", "expected_error"),
    [
        (StorcliParseError("malformed refresh payload"), "storcli refresh parse failed"),
        (StorcliNotAvailable("storcli unavailable on refresh"), "storcli refresh failed"),
    ],
    ids=["parse_error", "storcli_error"],
)
def test_consistency_check_mode_returns_502_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    refresh_error: Exception,
    expected_error: str,
) -> None:
    """Cover lines 2307-2326: refresh-time ``StorcliParseError`` / ``StorcliError``.

    The mutation succeeds and the audit row is written, but the post-mutation
    refresh fails. Both branches of the refresh handler must produce a 502
    with the action and detail surfaced; the audit row remains because
    refresh failure does not invalidate the recorded mutation.
    """

    set_command = ["/c0", "set", "consistencycheck=on", "mode=manual", "J"]
    show_command = ["/c0", "show", "cc", "J"]
    progress_command = ["/c0/vall", "show", "cc", "J"]
    calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        calls.append(list(args))
        if list(args) == set_command:
            return {"Controllers": [{"Command Status": {"Status": "Success"}}]}
        if list(args) == show_command or list(args) == progress_command:
            raise refresh_error
        raise AssertionError(f"unexpected storcli invocation: {args!r}")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/consistency-check/mode",
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
    assert calls[0] == set_command
    assert calls[1] == show_command
    assert len(events) == 1
    assert events[0].summary == "consistency check mode set to manual succeeded"


# ---------------------------------------------------------------------------
# _reject_consistency_check_mutation audit-failure (2348-2349)
# ---------------------------------------------------------------------------


def test_consistency_check_start_rejection_returns_500_when_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2348-2349 via the start ``already running`` reject.

    ``_reject_consistency_check_mutation`` records ``rejected: already
    running``; when ``record_operator_action`` raises ``SQLAlchemyError``,
    the inner sync helper logs and re-raises (2391-2397) and the outer
    coroutine returns a 500 — never the 409 the rejection would normally
    emit.
    """

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload()
        if list(args) == ["/c0/vall", "show", "cc", "J"]:
            return _cc_progress_payload(state="Active 50%")
        raise AssertionError(f"unexpected storcli invocation: {args!r}")

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
            "/controller/consistency-check/start",
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


# ---------------------------------------------------------------------------
# Inconsistency event-helper SQLAlchemyError branch (2449-2451)
# ---------------------------------------------------------------------------


def test_consistency_check_get_propagates_inconsistency_event_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover lines 2449-2451: SQLAlchemyError logging inside the helper.

    When the storcli snapshot reports inconsistencies the GET handler must
    emit a warning event. If ``record_event`` raises ``SQLAlchemyError``,
    the helper logs and re-raises; the unhandled exception bubbles up to
    FastAPI and the test client (with ``raise_server_exceptions=False``)
    captures a 500.
    """

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        if list(args) == ["/c0", "show", "cc", "J"]:
            return _cc_show_payload(mode="Auto")
        return _cc_progress_payload(
            state="Stopped",
            extra_props=[("CC Inconsistencies", "2")],
        )

    def fail_record_event(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("event insert failed")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr("megaraid_dashboard.web.routes.record_event", fail_record_event)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER, raise_server_exceptions=False) as client:
        response = client.get("/controller/consistency-check")

    assert response.status_code == 500
