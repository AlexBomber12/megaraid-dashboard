from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db import (
    Base,
    ControllerSnapshot,
    Event,
    get_sessionmaker,
    insert_snapshot,
)
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.services.scheduler import CollectorService
from megaraid_dashboard.storcli import StorcliNotAvailable, StorcliSnapshot


@pytest.fixture
def service_session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    Base.metadata.create_all(engine)
    try:
        yield get_sessionmaker(engine)
    finally:
        Base.metadata.drop_all(engine)


async def test_run_once_persists_snapshot_and_events(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
    sample_snapshot: StorcliSnapshot,
) -> None:
    previous = sample_snapshot.model_copy(
        deep=True,
        update={
            "captured_at": datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
            "controller": sample_snapshot.controller.model_copy(update={"alarm_state": "Off"}),
        },
    )
    current = sample_snapshot.model_copy(
        deep=True,
        update={
            "captured_at": datetime(2026, 4, 25, 12, 5, tzinfo=UTC),
            "controller": sample_snapshot.controller.model_copy(update={"alarm_state": "On"}),
        },
    )
    with service_session_factory() as session:
        insert_snapshot(session, previous)
        session.commit()

    async def fake_collect(*, settings: Settings) -> tuple[StorcliSnapshot, dict[str, Any]]:
        del settings
        return current, {"controller": {"stored": True}}

    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.collect_storcli_snapshot",
        fake_collect,
    )
    service = _service(service_session_factory)

    await service.run_once()

    with service_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 2
        events = list(session.scalars(select(Event).order_by(Event.id)))

    assert [(event.severity, event.category, event.summary) for event in events] == [
        ("info", "controller", "Alarm state changed from Off to On")
    ]


async def test_run_once_records_failure_and_recovery(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
    sample_snapshot: StorcliSnapshot,
) -> None:
    async def failing_collect(*, settings: Settings) -> tuple[StorcliSnapshot, dict[str, Any]]:
        del settings
        raise StorcliNotAvailable("missing storcli")

    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.collect_storcli_snapshot",
        failing_collect,
    )
    service = _service(service_session_factory)

    await service.run_once()

    async def successful_collect(*, settings: Settings) -> tuple[StorcliSnapshot, dict[str, Any]]:
        del settings
        return sample_snapshot, {"controller": {"stored": True}}

    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.collect_storcli_snapshot",
        successful_collect,
    )

    await service.run_once()

    with service_session_factory() as session:
        system_events = list(
            session.scalars(select(Event).where(Event.category == "system").order_by(Event.id))
        )

    assert len(system_events) == 2
    assert system_events[0].severity == "critical"
    assert system_events[0].summary.startswith("Collection failed: StorcliNotAvailable:")
    assert (system_events[1].severity, system_events[1].summary) == (
        "info",
        "Collection recovered",
    )


async def test_run_once_records_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
    sample_snapshot: StorcliSnapshot,
) -> None:
    async def successful_collect(*, settings: Settings) -> tuple[StorcliSnapshot, dict[str, Any]]:
        del settings
        return sample_snapshot, {"controller": {"stored": True}}

    def failing_insert_snapshot(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("database write failed")

    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.collect_storcli_snapshot",
        successful_collect,
    )
    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.insert_snapshot",
        failing_insert_snapshot,
    )
    service = _service(service_session_factory)

    await service.run_once()

    with service_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 0
        events = list(session.scalars(select(Event).order_by(Event.id)))

    assert len(events) == 1
    assert events[0].severity == "critical"
    assert events[0].category == "system"
    assert events[0].summary.startswith("Collection failed: RuntimeError: database write failed")


async def test_run_retention_once_invokes_retention_functions_in_order(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
) -> None:
    calls: list[str] = []
    call_thread_ids: list[int] = []
    event_loop_thread_id = threading.get_ident()

    def spy(name: str, return_value: int) -> Callable[..., int]:
        def _inner(*args: Any, **kwargs: Any) -> int:
            del args, kwargs
            calls.append(name)
            call_thread_ids.append(threading.get_ident())
            return return_value

        return _inner

    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.downsample_to_hourly",
        spy("downsample_to_hourly", 1),
    )
    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.downsample_to_daily",
        spy("downsample_to_daily", 2),
    )
    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.prune_raw_snapshots",
        spy("prune_raw_snapshots", 3),
    )
    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.prune_hourly_metrics",
        spy("prune_hourly_metrics", 4),
    )
    service = _service(service_session_factory)

    await service.run_retention_once()

    assert calls == [
        "downsample_to_hourly",
        "downsample_to_daily",
        "prune_raw_snapshots",
        "prune_hourly_metrics",
    ]
    assert call_thread_ids
    assert all(thread_id != event_loop_thread_id for thread_id in call_thread_ids)


async def test_run_once_waits_for_retention_write_lock(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
    sample_snapshot: StorcliSnapshot,
) -> None:
    service = _service(service_session_factory)
    retention_started = threading.Event()
    release_retention = threading.Event()

    def slow_retention_transaction() -> tuple[int, int, int, int]:
        retention_started.set()
        assert release_retention.wait(timeout=5)
        return (0, 0, 0, 0)

    async def fake_collect(*, settings: Settings) -> tuple[StorcliSnapshot, dict[str, Any]]:
        del settings
        return sample_snapshot, {"controller": {"stored": True}}

    monkeypatch.setattr(service, "_run_retention_transaction", slow_retention_transaction)
    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.collect_storcli_snapshot",
        fake_collect,
    )

    retention_task = asyncio.create_task(service.run_retention_once())
    assert await asyncio.to_thread(retention_started.wait, 5)
    run_once_task = asyncio.create_task(service.run_once())

    try:
        await asyncio.sleep(0.05)
        with service_session_factory() as session:
            assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 0
        assert run_once_task.done() is False
    finally:
        release_retention.set()

    await retention_task
    await run_once_task

    with service_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 1


async def test_run_once_waits_for_notifier_write_lock(
    monkeypatch: pytest.MonkeyPatch,
    service_session_factory: sessionmaker[Session],
    sample_snapshot: StorcliSnapshot,
) -> None:
    service = _service(service_session_factory)
    notifier_started = threading.Event()
    release_notifier = threading.Event()

    def slow_notifier_cycle_with_lock() -> None:
        notifier_started.set()
        assert release_notifier.wait(timeout=5)

    async def fake_collect(*, settings: Settings) -> tuple[StorcliSnapshot, dict[str, Any]]:
        del settings
        return sample_snapshot, {"controller": {"stored": True}}

    monkeypatch.setattr(
        service,
        "_run_notifier_cycle_with_lock",
        slow_notifier_cycle_with_lock,
    )
    monkeypatch.setattr(
        "megaraid_dashboard.services.scheduler.collect_storcli_snapshot",
        fake_collect,
    )

    notifier_task = asyncio.create_task(service._run_notifier_once())
    assert await asyncio.to_thread(notifier_started.wait, 5)
    run_once_task = asyncio.create_task(service.run_once())

    try:
        await asyncio.sleep(0.05)
        with service_session_factory() as session:
            assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 0
        assert run_once_task.done() is False
    finally:
        release_notifier.set()

    await notifier_task
    await run_once_task

    with service_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 1


async def test_start_registers_jobs_and_shutdown_stops_scheduler(
    service_session_factory: sessionmaker[Session],
) -> None:
    service = _service(service_session_factory)

    scheduler = await service.start()
    try:
        assert {job.id for job in scheduler.get_jobs()} == {
            "metrics_collector",
            "metrics_retention",
            "event_notifier",
        }
    finally:
        await service.shutdown(scheduler)

    assert scheduler.running is False


async def test_shutdown_waits_for_running_jobs(
    service_session_factory: sessionmaker[Session],
) -> None:
    service = _service(service_session_factory)
    scheduler = await service.start()
    started = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def slow_job() -> None:
        nonlocal finished
        started.set()
        await release.wait()
        finished = True

    tracked_job = asyncio.create_task(service._run_tracked_job(slow_job))
    await started.wait()

    shutdown = asyncio.create_task(service.shutdown(scheduler))
    await asyncio.sleep(0)

    assert shutdown.done() is False
    assert finished is False

    release.set()
    await shutdown
    await tracked_job

    assert finished is True
    assert scheduler.running is False


def _service(session_factory: sessionmaker[Session]) -> CollectorService:
    settings = _settings()
    return CollectorService(
        settings=settings,
        session_factory=session_factory,
        event_detector=EventDetector(
            temp_warning=100,
            temp_critical=110,
            temp_hysteresis=5,
            cv_capacitance_warning_percent=settings.cv_capacitance_warning_percent,
        ),
        clock=lambda: datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
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
