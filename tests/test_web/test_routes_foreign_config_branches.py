"""Branch coverage for foreign-config endpoints in ``web/routes.py``.

These tests target the error paths in routes.py lines 2454-2957 that are not
exercised by the happy-path tests in
``tests/test_web/test_foreign_config_routes.py``:

* GET /controller/foreign-config storcli error paths (JSON output)
* POST /controller/foreign-config/import body / query-string parsing
* POST /controller/foreign-config/clear body / query-string parsing
* Audit-persistence failures in both ``_reject_foreign_config_destructive``
  and ``_run_foreign_config_destructive``
* The inner SQLAlchemyError handler in
  ``_record_foreign_config_operator_action_sync``

The endpoints under test are the highest-risk in the codebase: an
"import" adopts external metadata into the live array and "clear"
permanently wipes foreign drive metadata. Every rejection, every
storcli failure, and every audit-persistence failure path must be
covered so that operators cannot act on a 2xx response without a
matching events row.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
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
from megaraid_dashboard.storcli import StorcliCommandFailed, StorcliParseError
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "storcli" / "redacted"


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
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fall_present_payload() -> dict[str, Any]:
    return _load_fixture("c0_fall_show_all_present.json")


# ---------------------------------------------------------------------------
# GET /controller/foreign-config error paths (lines 2467-2474, 2498-2501)
# ---------------------------------------------------------------------------


def test_get_foreign_config_json_returns_502_on_storcli_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover lines 2467-2469 and 2498-2501 for the JSON ``Accept`` path.

    The happy-path test only exercises the GET response body for a valid
    payload. When ``run_storcli`` returns a payload the parser rejects,
    the JSON branch must surface a 502 with ``error`` and ``detail``.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliParseError("foreign-config payload missing Response Data")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/controller/foreign-config",
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 502
    assert response.json() == {
        "error": "storcli parse failed",
        "detail": "foreign-config payload missing Response Data",
    }


def test_get_foreign_config_json_returns_502_on_storcli_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover lines 2471-2474 and 2498-2501 for the JSON ``Accept`` path.

    A generic ``StorcliError`` (e.g. storcli binary unavailable) takes the
    second exception arm and produces ``error="storcli command failed"``
    with the upstream detail.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliCommandFailed("storcli unavailable", err_msg="storcli unavailable")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/controller/foreign-config",
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "storcli command failed"
    assert "storcli unavailable" in body["detail"]


def test_get_foreign_config_html_renders_storcli_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the HTML branch of the GET endpoint when storcli fails.

    The HTML response shapes the same ``error``/``detail`` into the
    foreign-config template; the status code propagates from the
    storcli exception arm rather than the default 200.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise StorcliParseError("malformed foreign-config payload")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get(
            "/controller/foreign-config",
            headers={"Accept": "text/html"},
        )

    assert response.status_code == 502
    assert "malformed foreign-config payload" in response.text


# ---------------------------------------------------------------------------
# POST /controller/foreign-config/import body / query parsing
# (lines 2514, 2517, 2880-2881, 2884-2885)
# ---------------------------------------------------------------------------


def test_import_returns_400_when_body_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2880-2881 (and the 2514 early-return) for malformed JSON.

    ``_parse_foreign_config_import_body`` must short-circuit with a 400
    before storcli runs when the body cannot be decoded as JSON.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for malformed JSON body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers={**headers, "Content-Type": "application/json"},
            content="not valid json",
        )
        _assert_no_audit_event(test_app)

    assert response.status_code == 400
    assert response.json() == {"error": "request body must be valid JSON"}


def test_import_returns_400_when_body_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2884-2885 (and the 2514 early-return) for schema errors.

    ``ForeignConfigImportRequest.confirmation`` has ``min_length=1``; an
    empty string trips the pydantic ``ValidationError`` branch.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for invalid request body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/foreign-config/import",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": ""},
        )
        _assert_no_audit_event(test_app)

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid request body"
    assert isinstance(body["detail"], list)
    assert body["detail"], "validation detail must not be empty"


def test_import_returns_400_when_dry_run_query_param_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover line 2517: the ``isinstance(query_dry_run, JSONResponse)`` branch.

    ``_parse_query_dry_run`` returns a 400 ``JSONResponse`` when the
    ``dry_run`` query parameter is non-empty but not a known boolean
    keyword. The endpoint must propagate that response without invoking
    storcli.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for malformed dry_run query")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/foreign-config/import?dry_run=maybe",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": "anything"},
        )
        _assert_no_audit_event(test_app)

    assert response.status_code == 400
    assert response.json() == {"error": "dry_run query parameter must be a boolean"}


# ---------------------------------------------------------------------------
# POST /controller/foreign-config/clear body / query parsing
# (lines 2639, 2642, 2896-2897, 2900-2901)
# ---------------------------------------------------------------------------


def test_clear_returns_400_when_body_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2896-2897 (and the 2639 early-return) for malformed JSON."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for malformed JSON body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers={**headers, "Content-Type": "application/json"},
            content="not valid json",
        )
        _assert_no_audit_event(test_app)

    assert response.status_code == 400
    assert response.json() == {"error": "request body must be valid JSON"}


def test_clear_returns_400_when_body_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2900-2901 (and the 2639 early-return) for schema errors.

    ``ForeignConfigClearRequest`` enforces ``min_length=1`` on
    ``confirmation``; an empty string triggers the pydantic
    ``ValidationError`` branch.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for invalid clear body")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/foreign-config/clear",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": ""},
        )
        _assert_no_audit_event(test_app)

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid request body"
    assert isinstance(body["detail"], list)
    assert body["detail"], "validation detail must not be empty"


def test_clear_returns_400_when_dry_run_query_param_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover line 2642: the ``isinstance(query_dry_run, JSONResponse)`` branch."""

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run for malformed dry_run query")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.post(
            "/controller/foreign-config/clear?dry_run=maybe",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )
        _assert_no_audit_event(test_app)

    assert response.status_code == 400
    assert response.json() == {"error": "dry_run query parameter must be a boolean"}


# ---------------------------------------------------------------------------
# Rejection-path audit-persistence failures
# (lines 2781-2782, plus the inner 2949-2955 SQLAlchemyError handler)
# ---------------------------------------------------------------------------


def test_import_returns_500_when_rejection_audit_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    """Cover lines 2781-2782 via the confirmation-mismatch rejection path.

    ``_reject_foreign_config_destructive`` audits every destructive
    attempt that was rejected before storcli runs. If the audit write
    fails the route must escalate to 500 ``audit persistence failed``
    rather than silently emit the original 409.
    """

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        return fall_present_payload

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
            "/controller/foreign-config/import",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": "wrong-digest"},
        )
        _assert_no_audit_event(test_app)

    assert response.status_code == 500
    body = response.json()
    assert body == {
        "error": "audit persistence failed",
        "action": "import",
        "rejection_reason": "confirmation mismatch",
    }


def test_clear_returns_500_when_rejection_audit_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    """Cover lines 2781-2782 via the clear confirmation-phrase rejection.

    The clear endpoint short-circuits on a phrase mismatch before any
    storcli call. With ``record_operator_action`` raising, both the
    inner sync helper (2949-2955) and the outer audit handler
    (2781-2782) must fire and produce a 500.
    """

    async def fake_run_storcli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("storcli must not run before phrase check")

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
            "/controller/foreign-config/clear",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": "DELETE FOREIGN CONFIG"},
        )
        _assert_no_audit_event(test_app)

    assert response.status_code == 500
    body = response.json()
    assert body == {
        "error": "audit persistence failed",
        "action": "clear",
        "rejection_reason": "confirmation phrase mismatch",
    }


# ---------------------------------------------------------------------------
# Run-path audit-persistence failures (lines 2819-2825)
# ---------------------------------------------------------------------------


def test_import_succeeds_storcli_but_audit_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    """Cover lines 2819-2822 (``result is not None`` branch, no storcli_error).

    The storcli call succeeds, ``result`` is populated, ``storcli_error``
    remains None. With the audit insert failing the route emits a 500
    enriched with ``action``, ``argv``, and ``result`` but NOT
    ``storcli_error``.
    """

    success_result: dict[str, Any] = {
        "Controllers": [{"Command Status": {"Status": "Success"}}],
    }

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        return success_result

    audit_calls: list[str] = []

    def selective_fail_record_operator_action(
        *_args: object, message: str = "", **_kwargs: object
    ) -> None:
        audit_calls.append(message)
        # Let the rebuild-state / precheck path proceed without persisting
        # rejections, but the post-storcli outcome row must fail.
        if "succeeded" in message:
            raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        selective_fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Onln")
        response = client.post(
            "/controller/foreign-config/import",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": digest},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == "import"
    assert body["argv"] == ["/c0/fall", "import", "J"]
    assert body["result"] == success_result
    assert "storcli_error" not in body
    assert audit_calls and audit_calls[-1].endswith("succeeded")


def test_import_storcli_raises_and_audit_fails_returns_500_without_result(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    """Cover the 2821->2823 branch where ``result is None`` and ``storcli_error`` is set.

    ``run_storcli`` raises a ``StorcliError`` directly during the import
    call (after the precheck returned the present payload), so ``result``
    is never assigned. With the audit insert also failing the route emits
    a 500 carrying ``storcli_error`` but omitting ``result``.
    """

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise StorcliCommandFailed("import binary missing", err_msg="import binary missing")

    def selective_fail_record_operator_action(
        *_args: object, message: str = "", **_kwargs: object
    ) -> None:
        # Pass through pre-storcli precheck audits; fail the post-storcli
        # outcome row so the run-path audit handler fires.
        if "failed:" in message:
            raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        selective_fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Onln")
        response = client.post(
            "/controller/foreign-config/import",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": digest},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == "import"
    assert body["argv"] == ["/c0/fall", "import", "J"]
    assert "result" not in body
    assert "import binary missing" in body["storcli_error"]


def test_clear_storcli_failure_and_audit_failure_returns_500_with_storcli_error(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    """Cover lines 2819, 2823-2825 (``storcli_error is not None`` branch).

    ``ensure_command_succeeded`` raises ``StorcliCommandFailed`` after
    ``result`` was assigned, so both ``result`` and ``storcli_error``
    appear in the 500 payload alongside the action and argv.
    """

    failure_result: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {
                    "Status": "Failure",
                    "Detailed Status": [{"ErrMsg": "delete refused"}],
                }
            }
        ]
    }

    async def fake_run_storcli(args: list[str], **_kwargs: object) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        return failure_result

    def selective_fail_record_operator_action(
        *_args: object, message: str = "", **_kwargs: object
    ) -> None:
        # Let any pre-storcli precheck audits through; fail only the
        # final outcome row.
        if "failed:" in message:
            raise SQLAlchemyError("database is locked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        selective_fail_record_operator_action,
    )

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="Onln")
        response = client.post(
            "/controller/foreign-config/clear",
            headers=_csrf_request_headers(client, csrf_headers),
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "audit persistence failed"
    assert body["action"] == "clear"
    assert body["argv"] == ["/c0/fall", "delete", "J"]
    assert body["result"] == failure_result
    assert "delete refused" in body["storcli_error"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _digest_from_payload(client: TestClient) -> str:
    response = client.get("/controller/foreign-config", headers={"Accept": "application/json"})
    assert response.status_code == 200
    digest = response.json()["digest"]
    assert isinstance(digest, str)
    assert digest
    return digest


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
    state: str,
    enclosure_id: int = 2,
    slot_id: int = 0,
) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        controller = ControllerSnapshot(
            captured_at=datetime.now(UTC),
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
                serial_number="SN-test",
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


def _assert_no_audit_event(test_app: FastAPI) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        assert session.scalars(select(Event)).all() == []


def _load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload
