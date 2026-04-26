from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

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
from megaraid_dashboard.services.event_detector import DetectedEvent, EventDetector
from megaraid_dashboard.storcli import StorcliError, StorcliSnapshot

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

    async def run_once(self) -> None:
        try:
            snapshot, raw_payload = await collect_storcli_snapshot(settings=self.settings)
        except (StorcliError, OSError, TimeoutError) as exc:
            self._record_collection_failure(exc)
            return

        try:
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
        except Exception:
            LOGGER.exception("collector_run_failed")

    async def run_retention_once(self) -> None:
        try:
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
                LOGGER.info(
                    "retention_run_completed",
                    hourly_count=hourly_count,
                    daily_count=daily_count,
                    raw_pruned_count=raw_pruned_count,
                    hourly_pruned_count=hourly_pruned_count,
                )
        except Exception:
            LOGGER.exception("retention_run_failed")

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
        await self._run_tracked_job(self.run_once)

    async def _run_retention_job(self) -> None:
        await self._run_tracked_job(self.run_retention_once)

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

    def _record_collection_failure(self, exc: BaseException) -> None:
        try:
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
