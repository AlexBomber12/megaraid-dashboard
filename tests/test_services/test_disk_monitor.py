from __future__ import annotations

from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.disk_monitor import (
    _resolve_data_partition,
    check_data_partition_free_space,
)

DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
NOW = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)


def test_resolve_data_partition_handles_relative_sqlite_url(tmp_path: Path) -> None:
    expected = tmp_path.resolve()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(tmp_path)
        assert _resolve_data_partition("sqlite:///./megaraid.db") == expected


def test_resolve_data_partition_handles_absolute_sqlite_url(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "megaraid.db"

    assert _resolve_data_partition(f"sqlite:////{database_path.as_posix().lstrip('/')}") == (
        tmp_path / "data"
    )


@pytest.mark.parametrize("driver", ["pysqlite", "aiosqlite"])
def test_resolve_data_partition_handles_driver_qualified_sqlite_url(
    tmp_path: Path,
    driver: str,
) -> None:
    database_path = tmp_path / "data" / "megaraid.db"

    assert _resolve_data_partition(
        f"sqlite+{driver}:////{database_path.as_posix().lstrip('/')}"
    ) == (tmp_path / "data")


def test_resolve_data_partition_handles_memory_sqlite_url(tmp_path: Path) -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(tmp_path)
        assert _resolve_data_partition("sqlite:///:memory:") == tmp_path


@pytest.mark.parametrize(
    ("free_mb", "expected_severity", "expected_summary"),
    [
        (50, "critical", "Free space on data partition: 50 MB (below critical threshold 100 MB)"),
        (300, "warning", "Free space on data partition: 300 MB (below warning threshold 500 MB)"),
    ],
)
def test_check_data_partition_free_space_emits_threshold_events(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    tmp_path: Path,
    free_mb: int,
    expected_severity: str,
    expected_summary: str,
) -> None:
    settings = _settings(tmp_path)
    _mock_free_space(monkeypatch, free_mb)

    events = check_data_partition_free_space(session, settings=settings, now=NOW)

    assert len(events) == 1
    assert events[0].occurred_at == NOW
    assert events[0].severity == expected_severity
    assert events[0].category == "disk_space"
    assert events[0].subject == "Data partition"
    assert events[0].summary == expected_summary


def test_check_data_partition_free_space_returns_empty_when_healthy(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    tmp_path: Path,
) -> None:
    _mock_free_space(monkeypatch, 800)

    events = check_data_partition_free_space(session, settings=_settings(tmp_path), now=NOW)

    assert events == []


def test_check_data_partition_free_space_suppresses_recent_same_severity(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    tmp_path: Path,
) -> None:
    _record_disk_event(session, severity="critical", occurred_at=NOW - timedelta(hours=5))
    _mock_free_space(monkeypatch, 50)

    events = check_data_partition_free_space(session, settings=_settings(tmp_path), now=NOW)

    assert events == []


def test_check_data_partition_free_space_reemits_after_suppression_window(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    tmp_path: Path,
) -> None:
    _record_disk_event(session, severity="critical", occurred_at=NOW - timedelta(hours=7))
    _mock_free_space(monkeypatch, 50)

    events = check_data_partition_free_space(session, settings=_settings(tmp_path), now=NOW)

    assert len(events) == 1
    assert events[0].severity == "critical"


def test_check_data_partition_free_space_emits_recovery_after_warning(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    tmp_path: Path,
) -> None:
    _record_disk_event(session, severity="warning", occurred_at=NOW - timedelta(hours=1))
    _mock_free_space(monkeypatch, 800)

    events = check_data_partition_free_space(session, settings=_settings(tmp_path), now=NOW)

    assert len(events) == 1
    assert events[0].severity == "info"
    assert events[0].summary == "Free space recovered: 800 MB"


def test_check_data_partition_free_space_ignores_non_sqlite_database(
    session: Session,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, database_url="postgresql://localhost/megaraid")

    events = check_data_partition_free_space(session, settings=settings, now=NOW)

    assert events == []


def test_check_data_partition_free_space_redacts_non_sqlite_database_url(
    session: Session,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, database_url="postgresql://user:secret@localhost/megaraid")

    with capture_logs() as logs:
        events = check_data_partition_free_space(session, settings=settings, now=NOW)

    assert events == []
    assert logs == [
        {
            "event": "disk_space_monitor_unsupported_database",
            "log_level": "warning",
            "database_backend": "postgresql",
        }
    ]
    assert "secret" not in str(logs)
    assert settings.database_url not in str(logs)


def test_check_data_partition_free_space_emits_for_driver_qualified_sqlite_url(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    tmp_path: Path,
) -> None:
    _mock_free_space(monkeypatch, 50)
    settings = _settings(tmp_path, database_url=f"sqlite+pysqlite:///{tmp_path / 'megaraid.db'}")

    events = check_data_partition_free_space(session, settings=settings, now=NOW)

    assert len(events) == 1
    assert events[0].severity == "critical"


def _mock_free_space(monkeypatch: pytest.MonkeyPatch, free_mb: int) -> None:
    monkeypatch.setattr(
        "megaraid_dashboard.services.disk_monitor.shutil.disk_usage",
        lambda path: DiskUsage(total=1024**3, used=0, free=free_mb * 1024 * 1024),
    )


def _record_disk_event(session: Session, *, severity: str, occurred_at: datetime) -> None:
    session.add(
        Event(
            occurred_at=occurred_at,
            severity=severity,
            category="disk_space",
            subject="Data partition",
            summary="previous event",
            before_json=None,
            after_json=None,
        )
    )
    session.commit()


def _settings(tmp_path: Path, *, database_url: str | None = None) -> Settings:
    return Settings(
        alert_smtp_host="smtp.example.test",
        alert_smtp_port=587,
        alert_smtp_user="alert@example.test",
        alert_smtp_password="test-token",
        alert_from="alert@example.test",
        alert_to="ops@example.test",
        admin_username="admin",
        admin_password_hash="test-bcrypt-hash",
        storcli_path="/usr/local/sbin/storcli64",
        metrics_interval_seconds=300,
        database_url=database_url or f"sqlite:///{tmp_path / 'megaraid.db'}",
        log_level="INFO",
    )
