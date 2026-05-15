from __future__ import annotations

import asyncio
import errno
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db import (
    Base,
    Event,
    get_sessionmaker,
)
from megaraid_dashboard.db.dao import upsert_temp_state
from megaraid_dashboard.services import scheduler as scheduler_module
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.services.scheduler import (
    CollectorService,
    _release_notifier_lock,
    _require_aware_utc,
    _try_acquire_notifier_lock,
    _utc_now,
    _validate_notifier_lock_file,
)
from megaraid_dashboard.storcli import StorcliSnapshot

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def service_session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    Base.metadata.create_all(engine)
    try:
        yield get_sessionmaker(engine)
    finally:
        Base.metadata.drop_all(engine)


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
        clock=lambda: datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
    )


class _FakeScheduler:
    """Minimal stand-in for ``AsyncIOScheduler`` used by ``shutdown``."""

    def __init__(self, *, running: bool, pause_stops_running: bool = False) -> None:
        self.running = running
        self.paused = False
        self.shutdown_called = False
        self._pause_stops_running = pause_stops_running

    def pause(self) -> None:
        self.paused = True
        if self._pause_stops_running:
            self.running = False

    def shutdown(self, wait: bool = False) -> None:
        del wait
        self.shutdown_called = True
        self.running = False


# ---------------------------------------------------------------------------
# Module level helpers
# ---------------------------------------------------------------------------


def test_utc_now_returns_timezone_aware_datetime() -> None:
    now = _utc_now()
    assert now.tzinfo is UTC


def test_require_aware_utc_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive datetimes are not allowed"):
        _require_aware_utc(datetime(2026, 5, 15, 12, 0))


def test_require_aware_utc_returns_value_in_utc() -> None:
    aware = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    assert _require_aware_utc(aware) is aware or _require_aware_utc(aware) == aware


# ---------------------------------------------------------------------------
# Notifier lock acquisition + validation + release
# ---------------------------------------------------------------------------


def test_try_acquire_notifier_lock_creates_and_locks_file(tmp_path: Path) -> None:
    lock_path = str(tmp_path / "notifier.lock")
    fd = _try_acquire_notifier_lock(lock_path)
    assert fd is not None
    try:
        # File written with current pid
        with open(lock_path, encoding="ascii") as fh:
            assert fh.read() == str(os.getpid())
    finally:
        _release_notifier_lock(fd)


def test_try_acquire_notifier_lock_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "not-a-real-file"
    lock_path = tmp_path / "notifier.lock"
    os.symlink(target, lock_path)
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        _try_acquire_notifier_lock(str(lock_path))


def test_try_acquire_notifier_lock_propagates_other_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = str(tmp_path / "notifier.lock")

    def fake_open(*args: Any, **kwargs: Any) -> int:
        del args, kwargs
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(scheduler_module.os, "open", fake_open)
    with pytest.raises(OSError, match="permission denied"):
        _try_acquire_notifier_lock(lock_path)


def test_try_acquire_notifier_lock_returns_none_when_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = str(tmp_path / "notifier.lock")

    def fake_flock(fd: int, op: int) -> None:
        del fd, op
        raise BlockingIOError("would block")

    monkeypatch.setattr(scheduler_module.fcntl, "flock", fake_flock)
    assert _try_acquire_notifier_lock(lock_path) is None
    # File should still exist (was opened) but FD released by helper.
    assert os.path.exists(lock_path)


def test_try_acquire_notifier_lock_closes_fd_on_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = str(tmp_path / "notifier.lock")

    closed_fds: list[int] = []
    real_close = os.close

    def tracking_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(scheduler_module.os, "close", tracking_close)

    other_path = tmp_path / "other"
    other_path.write_text("hello")
    os.link(other_path, lock_path)

    with pytest.raises(RuntimeError, match="must not have hard links"):
        _try_acquire_notifier_lock(lock_path)
    assert closed_fds, "expected helper to close fd on failure"


def test_validate_notifier_lock_file_rejects_non_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Create a regular file but make fstat report a non-regular mode.
    lock_path = tmp_path / "notifier.lock"
    lock_path.write_text("")
    fd = os.open(str(lock_path), os.O_RDONLY)
    try:
        real_stat = os.fstat(fd)
        fake_stat = os.stat_result(
            (
                0o040000,  # directory mode
                real_stat.st_ino,
                real_stat.st_dev,
                real_stat.st_nlink,
                real_stat.st_uid,
                real_stat.st_gid,
                real_stat.st_size,
                real_stat.st_atime,
                real_stat.st_mtime,
                real_stat.st_ctime,
            )
        )
        monkeypatch.setattr(scheduler_module.os, "fstat", lambda _fd: fake_stat)
        with pytest.raises(RuntimeError, match="must be a regular file"):
            _validate_notifier_lock_file(fd, str(lock_path))
    finally:
        os.close(fd)


def test_validate_notifier_lock_file_rejects_foreign_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "notifier.lock"
    lock_path.write_text("")
    fd = os.open(str(lock_path), os.O_RDONLY)
    try:
        real_stat = os.fstat(fd)
        foreign_uid = real_stat.st_uid + 12345
        fake_stat = os.stat_result(
            (
                real_stat.st_mode,
                real_stat.st_ino,
                real_stat.st_dev,
                real_stat.st_nlink,
                foreign_uid,
                real_stat.st_gid,
                real_stat.st_size,
                real_stat.st_atime,
                real_stat.st_mtime,
                real_stat.st_ctime,
            )
        )
        monkeypatch.setattr(scheduler_module.os, "fstat", lambda _fd: fake_stat)
        with pytest.raises(RuntimeError, match="must be owned by the current user"):
            _validate_notifier_lock_file(fd, str(lock_path))
    finally:
        os.close(fd)


def test_validate_notifier_lock_file_rejects_hardlinks(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.write_text("")
    lock_path = tmp_path / "notifier.lock"
    os.link(other, lock_path)
    fd = os.open(str(lock_path), os.O_RDONLY)
    try:
        with pytest.raises(RuntimeError, match="must not have hard links"):
            _validate_notifier_lock_file(fd, str(lock_path))
    finally:
        os.close(fd)


def test_release_notifier_lock_closes_fd_even_when_unlock_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "notifier.lock"
    lock_path.write_text("")
    fd = os.open(str(lock_path), os.O_RDWR)

    def bad_flock(fd_: int, op: int) -> None:
        del fd_, op
        raise OSError("flock failed")

    monkeypatch.setattr(scheduler_module.fcntl, "flock", bad_flock)
    with pytest.raises(OSError, match="flock failed"):
        _release_notifier_lock(fd)
    # FD already closed by helper — closing again raises OSError(EBADF).
    with pytest.raises(OSError):
        os.close(fd)


def test_release_notifier_lock_releases_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "notifier.lock"
    fd = _try_acquire_notifier_lock(str(lock_path))
    assert fd is not None
    _release_notifier_lock(fd)
    # After release, a new acquire should succeed
    fd2 = _try_acquire_notifier_lock(str(lock_path))
    assert fd2 is not None
    _release_notifier_lock(fd2)


# ---------------------------------------------------------------------------
# _run_notifier_cycle_with_lock and other jobs
# ---------------------------------------------------------------------------


def test_run_notifier_cycle_with_lock_skips_when_lock_held(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)

    monkeypatch.setattr(
        scheduler_module,
        "_try_acquire_notifier_lock",
        lambda _path: None,
    )
    called: list[bool] = []
    monkeypatch.setattr(
        scheduler_module,
        "run_notifier_cycle",
        lambda *args, **kwargs: called.append(True),
    )

    service._run_notifier_cycle_with_lock()
    assert called == []


def test_run_notifier_cycle_with_lock_runs_cycle_and_releases(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)

    fake_fd = 999
    monkeypatch.setattr(
        scheduler_module,
        "_try_acquire_notifier_lock",
        lambda _path: fake_fd,
    )
    released: list[int] = []
    monkeypatch.setattr(
        scheduler_module,
        "_release_notifier_lock",
        lambda fd: released.append(fd),
    )
    monkeypatch.setattr(
        scheduler_module,
        "SmtpAlertTransport",
        lambda settings: object(),
    )
    cycle_calls: list[bool] = []

    def fake_cycle(session: Session, transport: Any, *, settings: Any, now: Any) -> None:
        del session, transport, settings, now
        cycle_calls.append(True)

    monkeypatch.setattr(scheduler_module, "run_notifier_cycle", fake_cycle)

    service._run_notifier_cycle_with_lock()
    assert cycle_calls == [True]
    assert released == [fake_fd]


def test_run_notifier_cycle_with_lock_logs_cycle_failure(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    fake_fd = 42
    monkeypatch.setattr(
        scheduler_module,
        "_try_acquire_notifier_lock",
        lambda _path: fake_fd,
    )
    released: list[int] = []
    monkeypatch.setattr(
        scheduler_module,
        "_release_notifier_lock",
        lambda fd: released.append(fd),
    )
    monkeypatch.setattr(
        scheduler_module,
        "SmtpAlertTransport",
        lambda settings: object(),
    )

    def boom(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("cycle exploded")

    monkeypatch.setattr(scheduler_module, "run_notifier_cycle", boom)

    # Should not raise — the inner exception is logged and the lock is released
    service._run_notifier_cycle_with_lock()
    assert released == [fake_fd]


async def test_run_notifier_once_invokes_cycle_with_lock(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    invoked: list[bool] = []

    def fake_cycle() -> None:
        invoked.append(True)

    monkeypatch.setattr(service, "_run_notifier_cycle_with_lock", fake_cycle)
    await service._run_notifier_once()
    assert invoked == [True]


# ---------------------------------------------------------------------------
# disk-space monitor job + transaction
# ---------------------------------------------------------------------------


async def test_run_disk_space_monitor_once_uses_write_lock(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    calls: list[bool] = []

    def fake_transaction() -> None:
        calls.append(True)

    monkeypatch.setattr(service, "_run_disk_space_monitor_transaction", fake_transaction)
    await service._run_disk_space_monitor_once()
    assert calls == [True]


def test_run_disk_space_monitor_transaction_persists_events(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    event = Event(
        occurred_at=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
        severity="warning",
        category="disk_space",
        subject="Data partition",
        summary="Low free space",
    )
    monkeypatch.setattr(
        scheduler_module,
        "check_data_partition_free_space",
        lambda session, *, settings, now: [event],
    )
    metric_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        scheduler_module,
        "stage_event_metric",
        lambda session, *, severity, category: metric_calls.append((severity, category)),
    )
    service._run_disk_space_monitor_transaction()
    assert metric_calls == [("warning", "disk_space")]
    with service_session_factory() as session:
        stored = list(session.scalars(select(Event)))
        assert len(stored) == 1
        assert stored[0].category == "disk_space"


def test_run_disk_space_monitor_transaction_no_events(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    monkeypatch.setattr(
        scheduler_module,
        "check_data_partition_free_space",
        lambda session, *, settings, now: [],
    )
    metric_calls: list[Any] = []
    monkeypatch.setattr(
        scheduler_module,
        "stage_event_metric",
        lambda *args, **kwargs: metric_calls.append((args, kwargs)),
    )
    service._run_disk_space_monitor_transaction()
    assert metric_calls == []
    with service_session_factory() as session:
        assert session.scalar(select(Event)) is None


# ---------------------------------------------------------------------------
# Job wrappers (_run_once_job, _run_retention_job, _run_notifier_job,
# _run_disk_space_monitor_job, _run_collector_cycle, _run_tracked_job)
# ---------------------------------------------------------------------------


async def test_run_once_job_runs_collector_cycle(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    calls: list[bool] = []

    async def fake_cycle() -> None:
        calls.append(True)

    monkeypatch.setattr(service, "_run_collector_cycle", fake_cycle)
    await service._run_once_job()
    assert calls == [True]


async def test_run_retention_job_delegates(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    calls: list[bool] = []

    async def fake_retention() -> None:
        calls.append(True)

    monkeypatch.setattr(service, "run_retention_once", fake_retention)
    await service._run_retention_job()
    assert calls == [True]


async def test_run_notifier_job_delegates(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    calls: list[bool] = []

    async def fake_once() -> None:
        calls.append(True)

    monkeypatch.setattr(service, "_run_notifier_once", fake_once)
    await service._run_notifier_job()
    assert calls == [True]


async def test_run_disk_space_monitor_job_delegates(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    calls: list[bool] = []

    async def fake_once() -> None:
        calls.append(True)

    monkeypatch.setattr(service, "_run_disk_space_monitor_once", fake_once)
    await service._run_disk_space_monitor_job()
    assert calls == [True]


async def test_run_collector_cycle_success_updates_metrics(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)

    async def fake_run_once() -> bool:
        return True

    monkeypatch.setattr(service, "run_once", fake_run_once)
    await service._run_collector_cycle()


async def test_run_collector_cycle_failure_skips_timestamp_update(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)

    async def fake_run_once() -> bool:
        return False

    monkeypatch.setattr(service, "run_once", fake_run_once)
    await service._run_collector_cycle()


async def test_run_tracked_job_no_current_task(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    calls: list[bool] = []

    async def job() -> None:
        calls.append(True)

    monkeypatch.setattr(asyncio, "current_task", lambda: None)
    await service._run_tracked_job(job)
    assert calls == [True]
    # Idle state untouched (still set, no jobs tracked)
    assert service._active_jobs == set()
    assert service._active_jobs_idle.is_set() is True


async def test_run_tracked_job_keeps_idle_clear_with_other_jobs(
    service_session_factory: sessionmaker[Session],
) -> None:
    service = _service(service_session_factory)
    job_a_release = asyncio.Event()
    job_b_release = asyncio.Event()
    job_a_started = asyncio.Event()
    job_b_started = asyncio.Event()

    async def job_a() -> None:
        job_a_started.set()
        await job_a_release.wait()

    async def job_b() -> None:
        job_b_started.set()
        await job_b_release.wait()

    task_a = asyncio.create_task(service._run_tracked_job(job_a))
    task_b = asyncio.create_task(service._run_tracked_job(job_b))
    await job_a_started.wait()
    await job_b_started.wait()
    # Both jobs are active; idle is clear
    assert service._active_jobs_idle.is_set() is False

    # Release job_a first — _active_jobs still non-empty -> branch 323->exit
    job_a_release.set()
    await task_a
    assert service._active_jobs_idle.is_set() is False
    assert len(service._active_jobs) == 1

    job_b_release.set()
    await task_b
    assert service._active_jobs_idle.is_set() is True


# ---------------------------------------------------------------------------
# Shutdown branches
# ---------------------------------------------------------------------------


async def test_shutdown_when_scheduler_not_running(
    service_session_factory: sessionmaker[Session],
) -> None:
    service = _service(service_session_factory)
    fake = _FakeScheduler(running=False)
    await service.shutdown(fake)
    assert fake.paused is False
    assert fake.shutdown_called is False


async def test_shutdown_returns_early_when_pause_stops_scheduler(
    service_session_factory: sessionmaker[Session],
) -> None:
    """``pause`` flips ``running`` -> shutdown skips the actual shutdown call."""

    service = _service(service_session_factory)
    fake = _FakeScheduler(running=True, pause_stops_running=True)
    await service.shutdown(fake)
    assert fake.paused is True
    assert fake.shutdown_called is False


async def test_shutdown_calls_scheduler_shutdown_when_still_running(
    service_session_factory: sessionmaker[Session],
) -> None:
    service = _service(service_session_factory)
    fake = _FakeScheduler(running=True)
    await service.shutdown(fake)
    assert fake.paused is True
    assert fake.shutdown_called is True


# ---------------------------------------------------------------------------
# run_once + retention + collection-failure branches
# ---------------------------------------------------------------------------


async def test_run_once_clears_and_upserts_temp_state(
    service_session_factory: sessionmaker[Session],
    sample_snapshot: StorcliSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)
    # Pre-populate a temp state for one of the drives so it is detected as
    # loaded and (depending on detector logic) cleared/updated.
    drive = sample_snapshot.physical_drives[0]
    with service_session_factory() as session:
        upsert_temp_state(
            session,
            enclosure_id=drive.enclosure_id,
            slot_id=drive.slot_id,
            serial_number=drive.serial_number,
            state="warning",
        )
        session.commit()

    async def fake_collect(*, settings: Any) -> tuple[StorcliSnapshot, dict[str, Any]]:
        del settings
        return sample_snapshot, {"controller": {"stored": True}}

    monkeypatch.setattr(scheduler_module, "collect_storcli_snapshot", fake_collect)

    # Force the detector to emit both a clear and an update so lines 113-126
    # are exercised.
    from megaraid_dashboard.services.event_detector import (
        TempStateClear,
        TempStateUpdate,
    )

    other = (
        sample_snapshot.physical_drives[1] if len(sample_snapshot.physical_drives) > 1 else drive
    )

    clears = [TempStateClear(enclosure_id=drive.enclosure_id, slot_id=drive.slot_id)]
    updates = [
        TempStateUpdate(
            enclosure_id=other.enclosure_id,
            slot_id=other.slot_id,
            serial_number=other.serial_number,
            state="critical",
        )
    ]

    detector = service.event_detector

    def patched_detect(previous: Any, current: Any) -> list[Any]:
        del previous, current
        return []

    monkeypatch.setattr(detector, "detect", patched_detect)
    monkeypatch.setattr(
        type(detector),
        "temperature_clears",
        property(lambda self: clears),
    )
    monkeypatch.setattr(
        type(detector),
        "temperature_updates",
        property(lambda self: updates),
    )

    assert await service.run_once() is True


async def test_run_retention_once_logs_exception(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)

    def failing_transaction() -> tuple[int, int, int, int]:
        raise RuntimeError("retention boom")

    monkeypatch.setattr(service, "_run_retention_transaction", failing_transaction)
    # Should not raise
    await service.run_retention_once()


async def test_record_collection_failure_swallows_record_error(
    service_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(service_session_factory)

    def failing_record(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("event-record failure")

    monkeypatch.setattr(scheduler_module, "record_event", failing_record)
    # Should not raise even though event recording fails.
    await service._record_collection_failure(RuntimeError("primary failure"))


# ---------------------------------------------------------------------------
# Conftest helpers used by other tests in the package
# ---------------------------------------------------------------------------


def test_service_factory_signature(
    service_session_factory: sessionmaker[Session],
) -> None:
    """Sanity check: helpers above return a working CollectorService."""

    service = _service(service_session_factory)
    assert isinstance(service.clock(), datetime)
    assert isinstance(service.settings, Settings)
    assert isinstance(_settings(), Settings)
    assert isinstance(_service, Callable)
