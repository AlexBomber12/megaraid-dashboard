from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db import Base, get_sessionmaker
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services import scheduler
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.services.scheduler import CollectorService
from megaraid_dashboard.web import metrics


@pytest.fixture
def service_session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    Base.metadata.create_all(engine)
    try:
        yield get_sessionmaker(engine)
    finally:
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def reset_runtime_metrics() -> None:
    metrics._reset_runtime_metrics_for_tests()


async def test_collector_cycle_records_duration_and_success_timestamp(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
) -> None:
    service = _service(service_session_factory)

    async def successful_run_once() -> bool:
        return True

    monotonic_values = iter([10.0, 12.5])
    monkeypatch.setattr(service, "run_once", successful_run_once)
    monkeypatch.setattr(scheduler, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(scheduler, "time", lambda: 1_777_888_999.0)

    await service._run_collector_cycle()

    assert metrics.COLLECTOR_CYCLE_DURATION._value.get() == 2.5
    assert metrics.COLLECTOR_LAST_RUN_TIMESTAMP._value.get() == 1_777_888_999.0


async def test_collector_cycle_records_duration_but_not_timestamp_on_exception(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
) -> None:
    service = _service(service_session_factory)
    metrics.COLLECTOR_LAST_RUN_TIMESTAMP.set(123.0)

    async def failing_run_once() -> bool:
        raise RuntimeError("collector failed")

    monotonic_values = iter([20.0, 21.25])
    monkeypatch.setattr(service, "run_once", failing_run_once)
    monkeypatch.setattr(scheduler, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(RuntimeError, match="collector failed"):
        await service._run_collector_cycle()

    assert metrics.COLLECTOR_CYCLE_DURATION._value.get() == 1.25
    assert metrics.COLLECTOR_LAST_RUN_TIMESTAMP._value.get() == 123.0


def test_disk_space_monitor_counts_emitted_events(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
) -> None:
    service = _service(service_session_factory)

    def fake_check_data_partition_free_space(
        session: Session,
        *,
        settings: Settings,
        now: datetime,
    ) -> list[Event]:
        del session, settings, now
        return [
            Event(
                occurred_at=datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
                severity="critical",
                category="disk_space",
                subject="Data partition",
                summary="Free space on data partition: 50 MB",
            )
        ]

    monkeypatch.setattr(
        scheduler,
        "check_data_partition_free_space",
        fake_check_data_partition_free_space,
    )

    service._run_disk_space_monitor_transaction()

    assert metrics.EVENTS_TOTAL.labels(severity="critical", category="disk_space")._value.get() == 1


def _service(session_factory: sessionmaker[Session]) -> CollectorService:
    settings = _settings()
    return CollectorService(
        settings=settings,
        session_factory=session_factory,
        event_detector=EventDetector(
            temp_warning=100,
            temp_critical=110,
            temp_hysteresis=5,
            roc_temp_warning=settings.roc_temp_warning_celsius,
            roc_temp_critical=settings.roc_temp_critical_celsius,
            roc_temp_hysteresis=settings.roc_temp_hysteresis_celsius,
            cv_capacitance_warning_percent=settings.cv_capacitance_warning_percent,
        ),
    )


def _settings() -> Settings:
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
        metrics_raw_retention_days=30,
        metrics_hourly_retention_days=365,
        database_url="sqlite:///:memory:",
        log_level="INFO",
    )
