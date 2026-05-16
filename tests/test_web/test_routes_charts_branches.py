"""Branch coverage for ``/drives/{e}/{s}/charts`` and helper functions.

Covers the validation branches inside ``_chart_identity_or_404`` and
``_require_aware_utc_query`` (lines 3488, 3493, 3500), the invalid
``range_days`` guard in ``_validate_range_days`` (line 3944), and the
``_event_severity_to_status`` mapping (lines 3951-3955) in
``web/routes.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.storcli import StorcliSnapshot
from megaraid_dashboard.web.routes import _event_severity_to_status
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


def test_charts_rejects_partial_chart_identity_query(
    sample_snapshot: StorcliSnapshot,
) -> None:
    """Cover line 3488: ``serial_number`` provided without ``captured_at``.

    Supplying one of the two pinning query parameters without the other must
    return 400 with the helpful detail describing the requirement.
    """
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get(
            "/drives/252/4/charts",
            params={"serial_number": "WD-WM00000005"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "serial_number and captured_at must be provided together"}


def test_charts_rejects_partial_chart_identity_when_only_captured_at_supplied(
    sample_snapshot: StorcliSnapshot,
) -> None:
    """Also covers the OR-branch in line 3487/3488 when only ``captured_at`` is set."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get(
            "/drives/252/4/charts",
            params={"captured_at": sample_snapshot.captured_at.isoformat()},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "serial_number and captured_at must be provided together"}


def test_charts_rejects_empty_serial_number(sample_snapshot: StorcliSnapshot) -> None:
    """Cover line 3493: empty ``serial_number`` must yield a 400 explaining why."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get(
            "/drives/252/4/charts",
            params={
                "serial_number": "",
                "captured_at": sample_snapshot.captured_at.isoformat(),
            },
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "serial_number must not be empty"}


def test_charts_rejects_naive_captured_at(sample_snapshot: StorcliSnapshot) -> None:
    """Cover line 3500: ``captured_at`` without a timezone must be rejected.

    ``_require_aware_utc_query`` raises a 400 when the parsed datetime is
    naive.  This is the only path that funnels through that helper.
    """
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get(
            "/drives/252/4/charts",
            params={
                "serial_number": "WD-WM00000005",
                "captured_at": "2026-04-25T12:00:00",
            },
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "captured_at must include a timezone"}


def test_charts_accepts_pinned_chart_identity(sample_snapshot: StorcliSnapshot) -> None:
    """Happy-path complement: both pin parameters supplied with timezone."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)
        drive = next(
            drive
            for drive in sample_snapshot.physical_drives
            if drive.enclosure_id == 252 and drive.slot_id == 4
        )

        response = client.get(
            "/drives/252/4/charts",
            params={
                "serial_number": drive.serial_number,
                "captured_at": sample_snapshot.captured_at.isoformat(),
            },
        )

    assert response.status_code == 200
    assert 'id="chart-area"' in response.text


def test_charts_rejects_unsupported_range_days(sample_snapshot: StorcliSnapshot) -> None:
    """Cover line 3944: ``_validate_range_days`` rejects values outside the allowlist."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        _insert_app_snapshot(test_app, sample_snapshot)

        response = client.get("/drives/252/4/charts", params={"range_days": 99})

    assert response.status_code == 400
    assert response.json() == {"detail": "range_days must be one of 7, 30, or 365"}


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("info", "optimal"),
        ("critical", "critical"),
        ("warning", "warning"),
        ("unknown_value", "unknown"),
        ("", "unknown"),
    ],
)
def test_event_severity_to_status_covers_each_branch(severity: str, expected: str) -> None:
    """Cover lines 3948-3955: every branch of ``_event_severity_to_status``."""
    assert _event_severity_to_status(severity) == expected


def _insert_app_snapshot(test_app: FastAPI, sample_snapshot: StorcliSnapshot) -> None:
    session_factory = cast(sessionmaker[Session], test_app.state.session_factory)
    with session_factory() as session:
        insert_snapshot(session, sample_snapshot)
        session.commit()
