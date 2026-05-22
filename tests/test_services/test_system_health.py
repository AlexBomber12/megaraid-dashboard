from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from megaraid_dashboard.config import Settings
from megaraid_dashboard.services import notifier
from megaraid_dashboard.services import scheduler as scheduler_module
from megaraid_dashboard.services.overview import (
    _database_size_bytes,
    _format_db_size,
    _format_relative_time,
    _load_system_health,
    _parse_operation_timestamp,
)
from megaraid_dashboard.web.metrics import COLLECTOR_LAST_RUN_TIMESTAMP


def test_notifier_health_true_false(tmp_path: Path) -> None:
    notifier._record_notifier_health(True)
    healthy = _load_system_health(
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
        now=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
    )
    notifier._record_notifier_health(False)
    unhealthy = _load_system_health(
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
        now=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
    )

    assert healthy.notifier_ok is True
    assert unhealthy.notifier_ok is False
    notifier._record_notifier_health(True)


def test_collector_last_run_relative_time_formatting() -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)

    assert _format_relative_time(None, now=now) == "never"
    assert _format_relative_time(now - timedelta(seconds=30), now=now) == "just now"
    assert _format_relative_time(now - timedelta(minutes=3), now=now) == "3 min ago"
    assert _format_relative_time(now - timedelta(hours=2), now=now) == "2 hours ago"
    assert _format_relative_time(now - timedelta(days=1), now=now) == "1 day ago"


def test_db_size_formatting() -> None:
    assert _format_db_size(42) == "42 B"
    assert _format_db_size(1_536) == "1.5 KB"
    assert _format_db_size(6_766_592) == "6.4 MB"
    assert _format_db_size(3 * 1024 * 1024 * 1024) == "3.0 GB"


def test_database_size_bytes_fallbacks(tmp_path: Path) -> None:
    db_path = tmp_path / "megaraid.db"
    db_path.write_bytes(b"1234")

    assert _database_size_bytes(f"sqlite:///{db_path}") == 4
    assert _database_size_bytes("sqlite:///:memory:") == 0
    assert _database_size_bytes("postgresql://localhost/megaraid") == 0
    assert _database_size_bytes("not a valid url") == 0
    assert _database_size_bytes(f"sqlite:///{tmp_path / 'missing.db'}") == 0


def test_operation_timestamp_parsing() -> None:
    assert _parse_operation_timestamp(None) is None
    assert _parse_operation_timestamp("   ") is None
    assert _parse_operation_timestamp("2026-04-25 18:15:00") == datetime(
        2026,
        4,
        25,
        18,
        15,
        tzinfo=UTC,
    )
    assert _parse_operation_timestamp("04/25/2026, 18:15:00") == datetime(
        2026,
        4,
        25,
        18,
        15,
        tzinfo=UTC,
    )
    assert _parse_operation_timestamp("unparseable") is None


def test_scheduler_last_collector_run_getter_from_metric() -> None:
    original = scheduler_module.get_last_collector_run_at()
    try:
        value = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
        COLLECTOR_LAST_RUN_TIMESTAMP.set(value.timestamp())

        assert scheduler_module.get_last_collector_run_at() == value
    finally:
        COLLECTOR_LAST_RUN_TIMESTAMP.set(0.0 if original is None else original.timestamp())


def test_system_health_uses_collector_last_run_metric(tmp_path: Path) -> None:
    original = scheduler_module.get_last_collector_run_at()
    try:
        now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
        COLLECTOR_LAST_RUN_TIMESTAMP.set((now - timedelta(seconds=30)).timestamp())

        health = _load_system_health(
            settings=_settings(tmp_path),
            scheduler=scheduler_module,
            collector_enabled=True,
            app_version="0.1.0",
            now=now,
        )

        assert health.collector_last_run_text == "just now"
    finally:
        COLLECTOR_LAST_RUN_TIMESTAMP.set(0.0 if original is None else original.timestamp())


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        alert_smtp_host="smtp.example.test",
        alert_smtp_port=587,
        alert_smtp_user="alert@example.test",
        alert_smtp_password="test-token",
        alert_from="alert@example.test",
        alert_to="ops@example.test",
        admin_username="admin",
        admin_password_hash="hash",
        storcli_path="/usr/local/sbin/storcli64",
        metrics_interval_seconds=300,
        database_url=f"sqlite:///{tmp_path / 'megaraid.db'}",
        log_level="INFO",
    )
