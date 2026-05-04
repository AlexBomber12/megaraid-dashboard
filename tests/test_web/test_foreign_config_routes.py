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
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fall_present_payload() -> dict[str, Any]:
    return _load_fixture("c0_fall_show_all_present.json")


@pytest.fixture
def fall_absent_payload() -> dict[str, Any]:
    return _load_fixture("c0_fall_show_all_absent.json")


def test_get_foreign_config_returns_present_payload(
    monkeypatch: pytest.MonkeyPatch,
    fall_present_payload: dict[str, Any],
) -> None:
    runner_calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        runner_calls.append(list(args))
        return fall_present_payload

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/foreign-config", headers={"Accept": "application/json"})

        assert response.status_code == 200
        body = response.json()
        assert body["present"] is True
        assert body["dg_count"] == 1
        assert body["drive_count"] == 4
        assert body["digest"].startswith("FC-DG1-PD4-")
        assert runner_calls == [["/c0/fall", "show", "all", "J"]]


def test_get_foreign_config_returns_absent_payload(
    monkeypatch: pytest.MonkeyPatch,
    fall_absent_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        return fall_absent_payload

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/foreign-config", headers={"Accept": "application/json"})

        assert response.status_code == 200
        assert response.json()["present"] is False


def test_get_foreign_config_html_renders_page(
    monkeypatch: pytest.MonkeyPatch,
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        return fall_present_payload

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/controller/foreign-config", headers={"Accept": "text/html"})

        assert response.status_code == 200
        assert "Foreign configuration" in response.text
        assert "FC-DG1-PD4-" in response.text


def test_import_dry_run_returns_argv_and_skips_runner(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    runner_calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        runner_calls.append(list(args))
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("storcli should not be called for dry runs beyond show")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": digest, "dry_run": True},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["dry_run"] is True
        assert body["argv"] == ["/c0/fall", "import", "J"]
        assert runner_calls == [
            ["/c0/fall", "show", "all", "J"],
            ["/c0/fall", "show", "all", "J"],
        ]
        _assert_no_audit_event(test_app)


def test_import_succeeds_and_audits(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    runner_calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        runner_calls.append(list(args))
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": digest},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "import"
        assert runner_calls == [
            ["/c0/fall", "show", "all", "J"],
            ["/c0/fall", "show", "all", "J"],
            ["/c0/fall", "import", "J"],
        ]
        event = _read_single_event(test_app)
        assert event.category == "operator_action"
        assert event.summary.startswith("foreign config import digest=")
        assert event.summary.endswith("succeeded")


def test_import_returns_502_when_command_status_failed(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        return {
            "Controllers": [
                {
                    "Command Status": {
                        "Status": "Failure",
                        "Description": "None",
                        "Detailed Status": [{"ErrMsg": "import not allowed"}],
                    }
                }
            ]
        }

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": digest},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli command failed"
        assert "import not allowed" in body["detail"]
        event = _read_single_event(test_app)
        assert event.summary.startswith(f"foreign config import digest={digest}")
        assert "failed: StorcliCommandFailed" in event.summary
        assert "import not allowed" in event.summary


def test_import_audits_rejection_when_precheck_parse_error(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        raise StorcliParseError("Response Data is not a mapping")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": "anything"},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli parse failed"
        assert "Response Data is not a mapping" in body["detail"]
        event = _read_single_event(test_app)
        assert event.summary.startswith("foreign config import digest=unknown")
        assert "rejected: storcli parse failed" in event.summary
        assert "Response Data is not a mapping" in event.summary


def test_import_audits_rejection_when_precheck_command_error(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        raise StorcliCommandFailed("storcli unavailable", err_msg="storcli unavailable")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": "anything"},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli command failed"
        assert "storcli unavailable" in body["detail"]
        event = _read_single_event(test_app)
        assert event.summary.startswith("foreign config import digest=unknown")
        assert "rejected: storcli command failed" in event.summary
        assert "storcli unavailable" in event.summary


def test_import_rejects_confirmation_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("import command should not be invoked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": "wrong-digest"},
        )

        assert response.status_code == 409
        assert response.json()["error"] == "confirmation mismatch"
        event = _read_single_event(test_app)
        assert event.summary.startswith("foreign config import digest=FC-DG1-PD4-")
        assert event.summary.endswith("rejected: confirmation mismatch")


def test_import_rejects_when_no_foreign_config(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_absent_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        return fall_absent_payload

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": "anything"},
        )

        assert response.status_code == 409
        assert response.json()["error"] == "no foreign configuration present"
        event = _read_single_event(test_app)
        assert (
            event.summary
            == "foreign config import digest=unknown rejected: no foreign configuration present"
        )


def test_import_rejects_during_active_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("import command should not be invoked")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Rbld")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": digest},
        )

        assert response.status_code == 409
        assert "rebuild" in response.json()["error"]
        event = _read_single_event(test_app)
        assert event.summary.startswith(f"foreign config import digest={digest}")
        assert event.summary.endswith("rejected: rebuild in progress")


def test_import_rejects_when_rebuild_state_unknown(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("import command should not be invoked when rebuild state is unknown")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        # Deliberately do not seed any controller snapshot; the rebuild gate
        # must fail closed rather than treating "no snapshot" as "no rebuild".
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": digest},
        )

        assert response.status_code == 409
        assert "rebuild state is unknown" in response.json()["error"]
        event = _read_single_event(test_app)
        assert event.summary.startswith(f"foreign config import digest={digest}")
        assert event.summary.endswith("rejected: rebuild state unknown")


def test_import_blocked_without_modes(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    monkeypatch.setenv("DESTRUCTIVE_MODE", "false")
    get_settings.cache_clear()

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("import command should not be invoked when modes are off")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/import",
            headers=headers,
            json={"confirmation": digest},
        )

        assert response.status_code == 403
        body = response.json()
        assert body["maintenance_mode"] is False
        assert body["destructive_mode"] is False
        event = _read_single_event(test_app)
        assert event.summary.startswith(f"foreign config import digest={digest}")
        assert event.summary.endswith("rejected: maintenance_mode and destructive_mode required")


def test_clear_dry_run_returns_argv_and_skips_runner(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    runner_calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        runner_calls.append(list(args))
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("clear command should not be invoked for dry run")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG", "dry_run": True},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["dry_run"] is True
        assert body["argv"] == ["/c0/fall", "delete", "J"]
        _assert_no_audit_event(test_app)


def test_clear_succeeds_and_audits(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    runner_calls: list[list[str]] = []

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        runner_calls.append(list(args))
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "clear"
        assert runner_calls == [
            ["/c0/fall", "show", "all", "J"],
            ["/c0/fall", "delete", "J"],
        ]
        event = _read_single_event(test_app)
        assert event.summary.startswith("foreign config clear digest=")
        assert event.summary.endswith("succeeded")


def test_clear_returns_502_when_command_status_failed(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        return {
            "Controllers": [
                {
                    "Command Status": {
                        "Status": "Failure",
                        "Description": "None",
                        "Detailed Status": [{"ErrMsg": "delete failed"}],
                    }
                }
            ]
        }

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli command failed"
        assert "delete failed" in body["detail"]
        event = _read_single_event(test_app)
        assert event.summary.startswith(f"foreign config clear digest={digest}")
        assert "failed: StorcliCommandFailed" in event.summary
        assert "delete failed" in event.summary


def test_clear_audits_rejection_when_precheck_parse_error(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        raise StorcliParseError("Response Data is not a mapping")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli parse failed"
        assert "Response Data is not a mapping" in body["detail"]
        event = _read_single_event(test_app)
        assert event.summary.startswith("foreign config clear digest=unknown")
        assert "rejected: storcli parse failed" in event.summary
        assert "Response Data is not a mapping" in event.summary


def test_clear_audits_rejection_when_precheck_command_error(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        raise StorcliCommandFailed("storcli unavailable", err_msg="storcli unavailable")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "storcli command failed"
        assert "storcli unavailable" in body["detail"]
        event = _read_single_event(test_app)
        assert event.summary.startswith("foreign config clear digest=unknown")
        assert "rejected: storcli command failed" in event.summary
        assert "storcli unavailable" in event.summary


def test_clear_rejects_wrong_confirmation_phrase(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        return fall_present_payload

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "DELETE FOREIGN CONFIG"},
        )

        assert response.status_code == 409
        assert "CLEAR FOREIGN CONFIG" in response.json()["error"]
        event = _read_single_event(test_app)
        # Phrase mismatch is checked before the foreign-config probe runs,
        # so the audit row records ``digest=unknown``.
        assert (
            event.summary
            == "foreign config clear digest=unknown rejected: confirmation phrase mismatch"
        )


def test_clear_rejects_when_no_foreign_config(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_absent_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        del args
        return fall_absent_payload

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

        assert response.status_code == 409
        assert response.json()["error"] == "no foreign configuration present"
        event = _read_single_event(test_app)
        assert (
            event.summary
            == "foreign config clear digest=unknown rejected: no foreign configuration present"
        )


def test_clear_rejects_during_active_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("clear command should not be invoked while rebuilding")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Rbld")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

        assert response.status_code == 409
        assert "rebuild" in response.json()["error"]
        event = _read_single_event(test_app)
        assert event.summary.startswith(f"foreign config clear digest={digest}")
        assert event.summary.endswith("rejected: rebuild in progress")


def test_clear_rejects_when_rebuild_state_unknown(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("clear command should not be invoked when rebuild state is unknown")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        # Deliberately do not seed any controller snapshot; the rebuild gate
        # must fail closed rather than treating "no snapshot" as "no rebuild".
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

        assert response.status_code == 409
        assert "rebuild state is unknown" in response.json()["error"]
        event = _read_single_event(test_app)
        assert event.summary.startswith(f"foreign config clear digest={digest}")
        assert event.summary.endswith("rejected: rebuild state unknown")


def test_clear_blocked_without_modes(
    monkeypatch: pytest.MonkeyPatch,
    csrf_headers: Callable[[TestClient], dict[str, str]],
    fall_present_payload: dict[str, Any],
) -> None:
    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    monkeypatch.setenv("DESTRUCTIVE_MODE", "false")
    get_settings.cache_clear()

    async def fake_run_storcli(args: list[str], **_: Any) -> dict[str, Any]:
        if list(args) == ["/c0/fall", "show", "all", "J"]:
            return fall_present_payload
        raise AssertionError("clear command should not be invoked when modes are off")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        digest = _digest_from_payload(client)
        _seed_drive(test_app, state="Onln")
        headers = _csrf_request_headers(client, csrf_headers)
        response = client.post(
            "/controller/foreign-config/clear",
            headers=headers,
            json={"confirmation": "CLEAR FOREIGN CONFIG"},
        )

        assert response.status_code == 403
        event = _read_single_event(test_app)
        assert event.summary.startswith(f"foreign config clear digest={digest}")
        assert event.summary.endswith("rejected: maintenance_mode and destructive_mode required")


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


def _read_single_event(test_app: FastAPI) -> Event:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return session.scalars(select(Event)).one()


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
