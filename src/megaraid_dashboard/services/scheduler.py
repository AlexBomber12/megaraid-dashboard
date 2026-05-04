from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import stat
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic, time
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.alerts import SmtpAlertTransport
from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import (
    clear_temp_state_for_slot,
    get_latest_snapshot,
    get_temp_state,
    insert_snapshot,
    record_event,
    upsert_temp_state,
)
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.db.retention import (
    downsample_to_daily,
    downsample_to_hourly,
    prune_hourly_metrics,
    prune_raw_snapshots,
)
from megaraid_dashboard.services.collector import collect_storcli_snapshot
from megaraid_dashboard.services.disk_monitor import check_data_partition_free_space
from megaraid_dashboard.services.event_detector import DetectedEvent, EventDetector
from megaraid_dashboard.services.notifier import _LOCK_PATH_DEFAULT, run_notifier_cycle
from megaraid_dashboard.storcli import StorcliError, StorcliSnapshot
from megaraid_dashboard.web.metrics import (
    COLLECTOR_CYCLE_DURATION,
    COLLECTOR_LAST_RUN_TIMESTAMP,
    EVENTS_TOTAL,
)

LOGGER = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CollectorService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        event_detector: EventDetector,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.event_detector = event_detector
        self.clock = clock
        self._active_jobs: set[asyncio.Task[Any]] = set()
        self._active_jobs_idle = asyncio.Event()
        self._active_jobs_idle.set()
        self._write_lock = asyncio.Lock()

    async def run_once(self) -> bool:
        try:
            snapshot, raw_payload = await collect_storcli_snapshot(settings=self.settings)
        except (StorcliError, OSError, TimeoutError) as exc:
            await self._record_collection_failure(exc)
            return False

        try:
            async with self._write_lock:
                with self.session_factory() as session:
                    previous = get_latest_snapshot(session)
                    self.event_detector.set_temperature_states(
                        self._load_temperature_states(session, snapshot)
                    )
                    insert_snapshot(
                        session,
                        snapshot,
                        store_raw=self.settings.store_raw_snapshot_payload,
                        raw_payload=raw_payload,
                    )
                    events = self.event_detector.detect(previous, snapshot)
                    if self._latest_system_event_was_collection_failure(session):
                        events.append(
                            DetectedEvent(
                                severity="info",
                                category="system",
                                subject="Controller",
                                summary="Collection recovered",
                                before=None,
                                after=None,
                            )
                        )
                    for event in events:
                        record_event(
                            session,
                            severity=event.severity,
                            category=event.category,
                            subject=event.subject,
                            summary=event.summary,
                            before=event.before,
                            after=event.after,
                        )
                    for temp_clear in self.event_detector.temperature_clears:
                        clear_temp_state_for_slot(
                            session,
                            enclosure_id=temp_clear.enclosure_id,
                            slot_id=temp_clear.slot_id,
                        )
                    for temp_update in self.event_detector.temperature_updates:
                        upsert_temp_state(
                            session,
                            enclosure_id=temp_update.enclosure_id,
                            slot_id=temp_update.slot_id,
                            serial_number=temp_update.serial_number,
                            state=temp_update.state,
                        )
                    session.commit()
                    LOGGER.info(
                        "collector_run_completed",
                        captured_at=snapshot.captured_at.isoformat(),
                        event_count=len(events),
                        physical_drive_count=len(snapshot.physical_drives),
                        virtual_drive_count=len(snapshot.virtual_drives),
                    )
        except Exception as exc:
            await self._record_collection_failure(exc)
            return False
        return True

    async def run_retention_once(self) -> None:
        try:
            async with self._write_lock:
                (
                    hourly_count,
                    daily_count,
                    raw_pruned_count,
                    hourly_pruned_count,
                ) = await asyncio.to_thread(self._run_retention_transaction)
            LOGGER.info(
                "retention_run_completed",
                hourly_count=hourly_count,
                daily_count=daily_count,
                raw_pruned_count=raw_pruned_count,
                hourly_pruned_count=hourly_pruned_count,
            )
        except Exception:
            LOGGER.exception("retention_run_failed")

    def _run_retention_transaction(self) -> tuple[int, int, int, int]:
        with self.session_factory() as session:
            now = _require_aware_utc(self.clock())
            hourly_count = downsample_to_hourly(
                session,
                now_utc=now,
                retention_days=self.settings.metrics_raw_retention_days,
            )
            daily_count = downsample_to_daily(
                session,
                now_utc=now,
                retention_days=self.settings.metrics_hourly_retention_days,
            )
            raw_pruned_count = prune_raw_snapshots(
                session,
                now_utc=now,
                retention_days=self.settings.metrics_raw_retention_days,
            )
            hourly_pruned_count = prune_hourly_metrics(
                session,
                now_utc=now,
                retention_days=self.settings.metrics_hourly_retention_days,
            )
            session.commit()
            return hourly_count, daily_count, raw_pruned_count, hourly_pruned_count

    async def start(self) -> AsyncIOScheduler:
        scheduler = AsyncIOScheduler(timezone=UTC)
        scheduler.add_job(
            self._run_once_job,
            "interval",
            seconds=self.settings.metrics_interval_seconds,
            id="metrics_collector",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            self._run_retention_job,
            "cron",
            hour=3,
            minute=30,
            timezone=UTC,
            id="metrics_retention",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            self._run_notifier_job,
            "interval",
            seconds=60,
            id="event_notifier",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            self._run_disk_space_monitor_job,
            "interval",
            minutes=self.settings.disk_check_interval_minutes,
            id="disk_space_monitor",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        return scheduler

    async def shutdown(self, scheduler: AsyncIOScheduler) -> None:
        if scheduler.running:
            scheduler.pause()
            await asyncio.sleep(0)
        await self._wait_for_active_jobs()
        if not scheduler.running:
            return
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)

    async def _run_once_job(self) -> None:
        await self._run_tracked_job(self._run_collector_cycle)

    async def _run_collector_cycle(self) -> None:
        start = monotonic()
        successful = False
        try:
            successful = await self.run_once()
        finally:
            elapsed = monotonic() - start
            COLLECTOR_CYCLE_DURATION.set(elapsed)
            if successful:
                COLLECTOR_LAST_RUN_TIMESTAMP.set(time())
            LOGGER.info(
                "collector_cycle_metrics_recorded",
                duration_seconds=elapsed,
                successful=successful,
            )

    async def _run_retention_job(self) -> None:
        await self._run_tracked_job(self.run_retention_once)

    async def _run_notifier_job(self) -> None:
        await self._run_tracked_job(self._run_notifier_once)

    async def _run_disk_space_monitor_job(self) -> None:
        await self._run_tracked_job(self._run_disk_space_monitor_once)

    async def _run_disk_space_monitor_once(self) -> None:
        async with self._write_lock:
            await asyncio.to_thread(self._run_disk_space_monitor_transaction)

    def _run_disk_space_monitor_transaction(self) -> None:
        with self.session_factory() as session:
            events = check_data_partition_free_space(
                session,
                settings=self.settings,
                now=self.clock(),
            )
            for event in events:
                session.add(event)
                EVENTS_TOTAL.labels(severity=event.severity, category=event.category).inc()
            if events:
                session.commit()

    async def _run_notifier_once(self) -> None:
        async with self._write_lock:
            await asyncio.to_thread(self._run_notifier_cycle_with_lock)

    def _run_notifier_cycle_with_lock(self) -> None:
        lock_fd = _try_acquire_notifier_lock(_LOCK_PATH_DEFAULT)
        if lock_fd is None:
            LOGGER.info("notifier_overlap_skipped", lock_path=_LOCK_PATH_DEFAULT)
            return
        try:
            transport = SmtpAlertTransport(self.settings)
            with self.session_factory() as session:
                try:
                    run_notifier_cycle(
                        session,
                        transport,
                        settings=self.settings,
                        now=self.clock(),
                    )
                except Exception:
                    LOGGER.exception("notifier_cycle_failed")
        finally:
            _release_notifier_lock(lock_fd)

    async def _run_tracked_job(self, job: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.current_task()
        if task is None:
            await job()
            return

        self._active_jobs_idle.clear()
        self._active_jobs.add(task)
        try:
            await job()
        finally:
            self._active_jobs.discard(task)
            if not self._active_jobs:
                self._active_jobs_idle.set()

    async def _wait_for_active_jobs(self) -> None:
        while self._active_jobs:
            await self._active_jobs_idle.wait()

    async def _record_collection_failure(self, exc: BaseException) -> None:
        try:
            async with self._write_lock:
                with self.session_factory() as session:
                    record_event(
                        session,
                        severity="critical",
                        category="system",
                        subject="Controller",
                        summary=f"Collection failed: {type(exc).__name__}: {exc}",
                    )
                    session.commit()
        except Exception:
            LOGGER.exception("collection_failure_event_record_failed")
            return

        LOGGER.exception(
            "collection_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )

    def _load_temperature_states(
        self,
        session: Session,
        snapshot: StorcliSnapshot,
    ) -> dict[tuple[int, int, str], str]:
        states: dict[tuple[int, int, str], str] = {}
        for drive in snapshot.physical_drives:
            temp_state = get_temp_state(
                session,
                enclosure_id=drive.enclosure_id,
                slot_id=drive.slot_id,
                serial_number=drive.serial_number,
            )
            if temp_state is not None:
                states[(drive.enclosure_id, drive.slot_id, drive.serial_number)] = temp_state.state
        return states

    def _latest_system_event_was_collection_failure(self, session: Session) -> bool:
        event = session.scalars(
            select(Event)
            .where(Event.category == "system")
            .order_by(Event.occurred_at.desc(), Event.id.desc())
            .limit(1)
        ).one_or_none()
        return event is not None and event.summary.startswith("Collection failed:")


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "naive datetimes are not allowed; use timezone-aware UTC datetimes"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _try_acquire_notifier_lock(lock_path: str) -> int | None:
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            msg = f"notifier lock path must not be a symlink: {lock_path}"
            raise RuntimeError(msg) from exc
        raise
    try:
        _validate_notifier_lock_file(lock_fd, lock_path)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        return None
    except Exception:
        os.close(lock_fd)
        raise

    os.ftruncate(lock_fd, 0)
    os.write(lock_fd, str(os.getpid()).encode("ascii"))
    return lock_fd


def _validate_notifier_lock_file(lock_fd: int, lock_path: str) -> None:
    lock_stat = os.fstat(lock_fd)
    if not stat.S_ISREG(lock_stat.st_mode):
        msg = f"notifier lock path must be a regular file: {lock_path}"
        raise RuntimeError(msg)
    if lock_stat.st_uid != os.getuid():
        msg = f"notifier lock path must be owned by the current user: {lock_path}"
        raise RuntimeError(msg)
    if lock_stat.st_nlink != 1:
        msg = f"notifier lock path must not have hard links: {lock_path}"
        raise RuntimeError(msg)


def _release_notifier_lock(lock_fd: int) -> None:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
