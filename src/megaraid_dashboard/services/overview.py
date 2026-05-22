"""Legacy overview and redesigned main-page view models."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, selectinload

from megaraid_dashboard.config import Settings, get_settings
from megaraid_dashboard.db.dao import (
    get_latest_snapshot,
)
from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.event_detector import (
    physical_drive_state_severity,
    virtual_drive_state_severity,
)
from megaraid_dashboard.services.events import list_recent_events
from megaraid_dashboard.services.notifier import get_notifier_health

_CONTROLLER_LABEL = "LSI MegaRAID SAS9270CV-8i"
_PD_OPTIMAL_STATES = {"Onln"}
_PD_WARNING_STATES = {"UBad", "Rbld", "Rebld", "Rebuild", "Rebuilding"}
_PD_CRITICAL_STATES = {"Failed", "Missing"}
_SEVERITY_RANK = ("critical", "warning", "info", "optimal", "neutral", "unknown")
_MAIN_PAGE_RECENT_ACTIVITY_LIMIT = 10
_DEFAULT_MAIN_PAGE_REFRESH_SECONDS = 30


@dataclass(frozen=True)
class PhysicalDriveRow:
    slot: str
    slot_url: str
    model: str
    serial_number: str
    state: str
    row_state: str
    status_icon: str
    temperature: str
    temperature_sort: int
    temperature_severity: str
    temperature_tooltip: str | None
    size: str
    size_bytes: int
    media_errors: int
    other_errors: int
    predictive_failures: int
    smart_label: str
    smart_severity: str


@dataclass(frozen=True)
class DriveListSummary:
    total: int
    optimal: int
    warning: int
    critical: int


@dataclass(frozen=True)
class RecentActivityItem:
    category: str
    message: str
    severity: str
    severity_icon: str
    occurred_at: datetime
    age_text: str


@dataclass(frozen=True)
class DriveListViewModel:
    has_snapshot: bool
    controller_label: str
    captured_at: datetime | None
    physical_drives: tuple[PhysicalDriveRow, ...]
    drive_summary: DriveListSummary
    empty_title: str
    empty_body: str
    empty_next_run: str


@dataclass(frozen=True)
class ActiveOperation:
    name: str
    progress_percent: int | None
    tooltip: str


@dataclass(frozen=True)
class ControllerSummaryViewModel:
    state: str
    model: str
    serial: str
    raid_summary: str
    roc_temperature_celsius: int | None
    cv_capacitance_percent: int | None
    bbu_status: str
    errors_24h: int
    active_operations: list[ActiveOperation]
    last_patrol_read_completed_at: datetime | None
    last_patrol_read_completed_text: str | None
    last_patrol_read_duration_text: str | None
    next_patrol_read_in_text: str | None


@dataclass(frozen=True)
class DriveGridTileViewModel:
    slot_label: str
    enclosure_id: int
    slot_id: int
    temperature_celsius: int | None
    temperature_state: str
    state_text: str
    state_severity: str
    tile_severity: str
    error_badge_count: int | None
    detail_url: str


@dataclass(frozen=True)
class DriveGridViewModel:
    tiles: list[DriveGridTileViewModel]
    worst_severity: str


@dataclass(frozen=True)
class SystemHealthViewModel:
    notifier_ok: bool
    collector_last_run_at: datetime | None
    collector_last_run_text: str
    db_size_human: str
    app_version: str


@dataclass(frozen=True)
class MainPageViewModel:
    controller: ControllerSummaryViewModel
    drive_grid: DriveGridViewModel
    recent_activity: list[RecentActivityItem]
    system_health: SystemHealthViewModel
    updated_at: datetime
    auto_refresh_seconds: int


class _SchedulerJob(Protocol):
    next_run_time: datetime | None


class _Scheduler(Protocol):
    def get_job(self, job_id: str) -> _SchedulerJob | None: ...


class _CollectorStateProvider(Protocol):
    def get_last_collector_run_at(self) -> datetime | None: ...


class _OperationState(Protocol):
    is_running: bool
    progress_percent: int | None
    last_run_timestamp: str | None


def load_main_page_view_model(
    session: Session,
    *,
    settings: Settings,
    scheduler: _CollectorStateProvider | None,
    collector_enabled: bool,
    app_version: str,
) -> MainPageViewModel:
    now = datetime.now(UTC)
    snapshot = _get_latest_overview_snapshot(session)
    return MainPageViewModel(
        controller=_load_controller_summary(session, settings=settings, snapshot=snapshot, now=now),
        drive_grid=_load_drive_grid(settings=settings, snapshot=snapshot),
        recent_activity=_load_recent_activity(
            session,
            limit=_MAIN_PAGE_RECENT_ACTIVITY_LIMIT,
            now=now,
        ),
        system_health=_load_system_health(
            settings=settings,
            scheduler=scheduler,
            collector_enabled=collector_enabled,
            app_version=app_version,
            now=now,
        ),
        updated_at=now if snapshot is None else snapshot.captured_at,
        auto_refresh_seconds=getattr(
            settings,
            "auto_refresh_seconds",
            _DEFAULT_MAIN_PAGE_REFRESH_SECONDS,
        ),
    )


def _load_controller_summary(
    session: Session,
    *,
    settings: Settings,
    snapshot: ControllerSnapshot | None,
    now: datetime,
) -> ControllerSummaryViewModel:
    del settings
    if snapshot is None:
        return ControllerSummaryViewModel(
            state="UNKNOWN",
            model="Unknown",
            serial="Unknown",
            raid_summary="Unknown",
            roc_temperature_celsius=None,
            cv_capacitance_percent=None,
            bbu_status="Unknown",
            errors_24h=_count_recent_warning_and_critical_events(session, now=now),
            active_operations=[],
            last_patrol_read_completed_at=None,
            last_patrol_read_completed_text=None,
            last_patrol_read_duration_text=None,
            next_patrol_read_in_text=None,
        )

    cachevault = snapshot.cachevault
    patrol_read_state = _load_patrol_read_state(snapshot)
    last_patrol_read_completed_at = _parse_operation_timestamp(
        None if patrol_read_state is None else patrol_read_state.last_run_timestamp
    )
    next_patrol_read_at = _parse_operation_timestamp(
        _first_raw_text(snapshot.raw_json or {}, "Next Patrol Read launch")
    )
    return ControllerSummaryViewModel(
        state=derive_controller_health(
            snapshot,
            snapshot.physical_drives,
            snapshot.virtual_drives,
        ).upper(),
        model=snapshot.model_name,
        serial=snapshot.serial_number,
        raid_summary=_main_page_raid_summary(snapshot.virtual_drives, snapshot.physical_drives),
        roc_temperature_celsius=snapshot.roc_temperature_celsius,
        cv_capacitance_percent=None if cachevault is None else cachevault.capacitance_percent,
        bbu_status=_main_page_bbu_status(snapshot),
        errors_24h=_count_recent_warning_and_critical_events(session, now=now),
        active_operations=_load_active_operations(snapshot),
        last_patrol_read_completed_at=last_patrol_read_completed_at,
        last_patrol_read_completed_text=_format_short_date(last_patrol_read_completed_at),
        last_patrol_read_duration_text=None,
        next_patrol_read_in_text=_format_future_time(next_patrol_read_at, now=now),
    )


def _load_drive_grid(
    *,
    settings: Settings,
    snapshot: ControllerSnapshot | None,
) -> DriveGridViewModel:
    if snapshot is None:
        return DriveGridViewModel(tiles=[], worst_severity="optimal")

    tiles = [
        _drive_grid_tile(
            drive,
            temp_warning=settings.temp_warning_celsius,
            temp_critical=settings.temp_critical_celsius,
        )
        for drive in sorted(
            snapshot.physical_drives, key=lambda drive: (drive.enclosure_id, drive.slot_id)
        )
    ]
    worst_severity = "optimal"
    for tile in tiles:
        worst_severity = _worst_severity(worst_severity, tile.tile_severity)
    return DriveGridViewModel(tiles=tiles, worst_severity=worst_severity)


def _load_system_health(
    *,
    settings: Settings,
    scheduler: _CollectorStateProvider | None,
    collector_enabled: bool,
    app_version: str,
    now: datetime,
) -> SystemHealthViewModel:
    collector_last_run_at = None if scheduler is None else scheduler.get_last_collector_run_at()
    collector_last_run_at = (
        None if collector_last_run_at is None else _require_aware_utc(collector_last_run_at)
    )
    return SystemHealthViewModel(
        notifier_ok=get_notifier_health(),
        collector_last_run_at=collector_last_run_at,
        collector_last_run_text=(
            "disabled"
            if not collector_enabled and collector_last_run_at is None
            else _format_relative_time(collector_last_run_at, now=now)
        ),
        db_size_human=_format_db_size(_database_size_bytes(settings.database_url)),
        app_version=app_version,
    )


def _drive_grid_tile(
    drive: PhysicalDriveSnapshot,
    *,
    temp_warning: int,
    temp_critical: int,
) -> DriveGridTileViewModel:
    temperature_state = temperature_severity(
        drive.temperature_celsius,
        temp_warning=temp_warning,
        temp_critical=temp_critical,
    )
    if temperature_state == "unknown":
        temperature_state = "optimal"
    state_severity = _drive_grid_state_severity(drive.state)
    error_count = (
        drive.media_errors
        + drive.other_errors
        + getattr(drive, "bbm_errors", 0)
        + drive.predictive_failures
    )
    return DriveGridTileViewModel(
        slot_label=f"S{drive.slot_id}",
        enclosure_id=drive.enclosure_id,
        slot_id=drive.slot_id,
        temperature_celsius=drive.temperature_celsius,
        temperature_state=temperature_state,
        state_text=drive.state,
        state_severity=state_severity,
        tile_severity=compute_drive_tile_severity(
            drive,
            temp_warning=temp_warning,
            temp_critical=temp_critical,
        ),
        error_badge_count=None if error_count == 0 else error_count,
        detail_url=f"/drives/{drive.enclosure_id}:{drive.slot_id}",
    )


def compute_drive_tile_severity(
    drive: PhysicalDriveSnapshot,
    *,
    temp_warning: int,
    temp_critical: int,
) -> str:
    temperature_state = temperature_severity(
        drive.temperature_celsius,
        temp_warning=temp_warning,
        temp_critical=temp_critical,
    )
    if temperature_state == "unknown":
        temperature_state = "optimal"
    return _worst_severity(temperature_state, _drive_grid_state_severity(drive.state))


def _drive_grid_state_severity(state: str) -> str:
    if state in _PD_OPTIMAL_STATES or state == "UGood":
        return "optimal"
    if state in _PD_CRITICAL_STATES:
        return "critical"
    if state in _PD_WARNING_STATES:
        return "warning"
    severity = _event_severity_to_status(physical_drive_state_severity("Onln", state))
    return "warning" if severity in {"unknown", "optimal"} else severity


def _load_active_operations(snapshot: ControllerSnapshot) -> list[ActiveOperation]:
    candidates: list[tuple[int, ActiveOperation]] = []
    rebuild_operation = _load_rebuild_operation(snapshot)
    if rebuild_operation is not None:
        candidates.append((0, rebuild_operation))
    consistency_check_state = _load_consistency_check_state(snapshot)
    if consistency_check_state is not None and consistency_check_state.is_running:
        candidates.append(
            (1, _active_operation("Consistency check", consistency_check_state, "VD 0"))
        )
    patrol_read_state = _load_patrol_read_state(snapshot)
    if patrol_read_state is not None and patrol_read_state.is_running:
        candidates.append((2, _active_operation("Patrol read", patrol_read_state, "Controller")))
    foreign_import_operation = _load_foreign_config_import_operation(snapshot)
    if foreign_import_operation is not None:
        candidates.append((3, foreign_import_operation))
    return [operation for _, operation in sorted(candidates, key=lambda item: item[0])]


def _active_operation(name: str, state: _OperationState, subject: str) -> ActiveOperation:
    tooltip = f"{subject}, ETA unknown"
    started_at = _parse_operation_timestamp(state.last_run_timestamp)
    if started_at is not None:
        tooltip = f"{subject}, started {started_at.strftime('%H:%M')} - ETA unknown"
    return ActiveOperation(
        name=name,
        progress_percent=state.progress_percent,
        tooltip=tooltip,
    )


def _load_rebuild_operation(snapshot: ControllerSnapshot) -> ActiveOperation | None:
    for drive in snapshot.physical_drives:
        if drive.state in {"Rbld", "Rebld", "Rebuild", "Rebuilding"}:
            return ActiveOperation(
                name="Rebuild",
                progress_percent=None,
                tooltip=f"Drive {drive.enclosure_id}:{drive.slot_id}, ETA unknown",
            )
    return None


def _load_foreign_config_import_operation(snapshot: ControllerSnapshot) -> ActiveOperation | None:
    del snapshot
    return None


def _load_patrol_read_state(snapshot: ControllerSnapshot) -> _OperationState | None:
    del snapshot
    return None


def _load_consistency_check_state(snapshot: ControllerSnapshot) -> _OperationState | None:
    del snapshot
    return None


def _count_recent_warning_and_critical_events(session: Session, *, now: datetime) -> int:
    return (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.severity.in_(("warning", "critical")),
                Event.occurred_at > _require_aware_utc(now) - timedelta(hours=24),
            )
        )
        or 0
    )


def _main_page_raid_summary(
    virtual_drives: Sequence[VirtualDriveSnapshot],
    physical_drives: Sequence[PhysicalDriveSnapshot],
) -> str:
    return (
        f"{_dominant_raid_level(virtual_drives)} "
        f"({len(virtual_drives)} VD, {len(physical_drives)} PD)"
    )


def _main_page_bbu_status(snapshot: ControllerSnapshot) -> str:
    if snapshot.cachevault is not None and snapshot.cv_present and not snapshot.bbu_present:
        return "N/A"
    if snapshot.cachevault is not None:
        return _virtual_drive_state_label(snapshot.cachevault.state)
    if not snapshot.bbu_present:
        return "N/A"
    return "Unknown"


def _format_relative_time(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "never"
    resolved_now = datetime.now(UTC) if now is None else _require_aware_utc(now)
    elapsed = max(timedelta(), resolved_now - _require_aware_utc(value))
    seconds = int(elapsed.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} {_pluralize(minutes, 'min', 'min')} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} {_pluralize(hours, 'hour', 'hours')} ago"
    days = hours // 24
    return f"{days} {_pluralize(days, 'day', 'days')} ago"


def _format_short_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _require_aware_utc(value).strftime("%b %-d")


def _format_future_time(value: datetime | None, *, now: datetime) -> str | None:
    if value is None:
        return None
    elapsed = max(timedelta(), _require_aware_utc(value) - _require_aware_utc(now))
    seconds = int(elapsed.total_seconds())
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {minutes}m"
    if minutes:
        return f"in {minutes}m"
    return "now"


def _format_db_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    value = float(size_bytes)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{int(value * 10) / 10:.1f} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


def _database_size_bytes(database_url: str) -> int:
    try:
        parsed = make_url(database_url)
    except Exception:
        return 0
    database = parsed.database
    if parsed.get_backend_name() != "sqlite" or database in {None, "", ":memory:"}:
        return 0
    assert database is not None
    return os.path.getsize(database) if os.path.exists(database) else 0


def _parse_operation_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.split("(", maxsplit=1)[0].strip()
    if not text:
        return None
    for format_string in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y, %H:%M:%S",
        "%b %d, %Y %H:%M",
    ):
        try:
            return datetime.strptime(text, format_string).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def load_drive_list_view_model(
    session: Session,
    *,
    slot_url_factory: Callable[[int, int], str],
    scheduler: _Scheduler | None = None,
) -> DriveListViewModel:
    settings = get_settings()
    snapshot = get_latest_snapshot(session)
    if snapshot is None:
        return DriveListViewModel(
            has_snapshot=False,
            controller_label=_CONTROLLER_LABEL,
            captured_at=None,
            physical_drives=(),
            drive_summary=DriveListSummary(total=0, optimal=0, warning=0, critical=0),
            empty_title="Waiting for first metrics collection",
            empty_body="The collector has not yet completed its first run.",
            empty_next_run=_empty_next_run_text(
                scheduler=scheduler,
                collector_enabled=settings.collector_enabled,
            ),
        )

    sorted_drives = tuple(
        sorted(snapshot.physical_drives, key=lambda drive: (drive.enclosure_id, drive.slot_id))
    )
    temp_warning = settings.temp_warning_celsius
    temp_critical = settings.temp_critical_celsius
    physical_drives = tuple(
        _physical_drive_row(
            drive,
            temp_warning=temp_warning,
            temp_critical=temp_critical,
            slot_url=slot_url_factory(drive.enclosure_id, drive.slot_id),
        )
        for drive in sorted_drives
    )

    return DriveListViewModel(
        has_snapshot=True,
        controller_label=_CONTROLLER_LABEL,
        captured_at=snapshot.captured_at,
        physical_drives=physical_drives,
        drive_summary=_drive_list_summary(physical_drives),
        empty_title="Waiting for first metrics collection",
        empty_body="The collector has not yet completed its first run.",
        empty_next_run="",
    )


def _get_latest_overview_snapshot(session: Session) -> ControllerSnapshot | None:
    return session.scalars(
        select(ControllerSnapshot)
        .options(
            selectinload(ControllerSnapshot.physical_drives),
            selectinload(ControllerSnapshot.virtual_drives),
            selectinload(ControllerSnapshot.cachevault),
        )
        .order_by(ControllerSnapshot.captured_at.desc())
        .limit(1)
    ).one_or_none()


def _load_recent_activity(
    session: Session,
    *,
    limit: int = 8,
    now: datetime | None = None,
) -> list[RecentActivityItem]:
    return [
        RecentActivityItem(
            category=event.category,
            message=event.summary,
            severity=event.severity,
            severity_icon=_severity_icon(event.severity),
            occurred_at=_require_aware_utc(event.occurred_at),
            age_text=_format_relative_time(event.occurred_at, now=now),
        )
        for event in list_recent_events(session, limit=limit)
    ]


def _first_raw_text(raw_json: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _find_raw_value(raw_json, key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in {"-", "None", "N/A"}:
            return text
    return None


def _find_raw_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        for candidate_key, candidate_value in value.items():
            if str(candidate_key).strip().lower() == key.lower():
                return candidate_value
            found = _find_raw_value(candidate_value, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        property_value = _property_value(value, key)
        if property_value is not None:
            return property_value
        for item in value:
            found = _find_raw_value(item, key)
            if found is not None:
                return found
    return None


def _property_value(items: list[Any], key: str) -> Any:
    for item in items:
        if not isinstance(item, Mapping):
            continue
        property_name = item.get("Property") or item.get("Ctrl_Prop")
        if str(property_name).strip().lower() == key.lower():
            return item.get("Value")
    return None


def _severity_icon(severity: str) -> str:
    return {
        "critical": "x-circle",
        "warning": "alert-triangle",
        "info": "check-circle",
    }.get(severity, "info")


def _empty_next_run_text(*, scheduler: _Scheduler | None, collector_enabled: bool) -> str:
    if not collector_enabled:
        return "Metrics collection is disabled; no collection run is scheduled."

    next_run_time = _next_scheduler_run(scheduler)
    if next_run_time is None:
        return "No collection run is currently scheduled."

    now = datetime.now(UTC)
    if next_run_time.tzinfo is None or next_run_time.utcoffset() is None:
        next_run_utc = next_run_time.replace(tzinfo=UTC)
    else:
        next_run_utc = next_run_time.astimezone(UTC)
    seconds = max(0, int((next_run_utc - now).total_seconds()))
    return f"Next scheduled run in {seconds} seconds."


def _next_scheduler_run(scheduler: _Scheduler | None) -> datetime | None:
    if scheduler is None:
        return None
    metrics_job = scheduler.get_job("metrics_collector")
    if metrics_job is None:
        return None
    return metrics_job.next_run_time


def derive_controller_health(
    snapshot: ControllerSnapshot,
    physical_drives: Sequence[PhysicalDriveSnapshot],
    virtual_drives: Sequence[VirtualDriveSnapshot],
    *,
    physical_drive_severity: str | None = None,
) -> Literal["optimal", "warning", "critical"]:
    # ``snapshot.alarm_state`` reflects the HwCfg buzzer-enabled flag, not an
    # active alarm condition, so it must not influence controller health.
    del snapshot
    severity: Literal["optimal", "warning", "critical"] = "optimal"
    resolved_physical_drive_severity = physical_drive_severity
    if resolved_physical_drive_severity is None:
        resolved_physical_drive_severity = _physical_drive_aggregate_status(physical_drives)
    severity = _worst_controller_health(severity, resolved_physical_drive_severity)

    severity = _worst_controller_health(
        severity,
        _virtual_drive_controller_health_status(virtual_drives),
    )

    return severity


def _physical_drive_row(
    drive: PhysicalDriveSnapshot,
    *,
    temp_warning: int,
    temp_critical: int,
    slot_url: str = "",
) -> PhysicalDriveRow:
    smart_alert = drive.smart_alert
    row_state = _drive_row_state(
        drive,
        temp_warning=temp_warning,
        temp_critical=temp_critical,
    )
    return PhysicalDriveRow(
        slot=f"{drive.enclosure_id}:{drive.slot_id}",
        slot_url=slot_url,
        model=drive.model,
        serial_number=drive.serial_number,
        state=drive.state,
        row_state=row_state,
        status_icon=_drive_status_icon(row_state),
        temperature="Unknown"
        if drive.temperature_celsius is None
        else f"{drive.temperature_celsius} C",
        temperature_sort=-1 if drive.temperature_celsius is None else drive.temperature_celsius,
        temperature_severity=_drive_temperature_badge(
            drive,
            temp_warning=temp_warning,
            temp_critical=temp_critical,
            row_state=row_state,
        ),
        temperature_tooltip=_temperature_tooltip(
            drive.temperature_celsius,
            warning=temp_warning,
            critical=temp_critical,
        ),
        size=format_tb(drive.size_bytes),
        size_bytes=drive.size_bytes,
        media_errors=drive.media_errors,
        other_errors=drive.other_errors,
        predictive_failures=drive.predictive_failures,
        smart_label="Yes" if smart_alert else "No",
        smart_severity="critical" if smart_alert else "neutral",
    )


def _drive_list_summary(drives: Sequence[PhysicalDriveRow]) -> DriveListSummary:
    counts = Counter(drive.row_state for drive in drives)
    return DriveListSummary(
        total=len(drives),
        optimal=counts["optimal"],
        warning=counts["warning"],
        critical=counts["critical"],
    )


def _drive_row_state(
    drive: PhysicalDriveSnapshot,
    *,
    temp_warning: int,
    temp_critical: int,
) -> str:
    return _drive_state_badge(
        drive,
        temp_warning=temp_warning,
        temp_critical=temp_critical,
    )


def _drive_state_badge(
    drive: PhysicalDriveSnapshot,
    *,
    temp_warning: int,
    temp_critical: int,
) -> str:
    """Return the single row badge; state and temperature are mutually exclusive.

    Critical wins over warning, both win over optimal, and unknown drive states
    are treated as warning so a non-online state is never hidden by temperature.
    """
    state_status = _event_severity_to_status(physical_drive_state_severity("Onln", drive.state))
    if drive.state not in _PD_OPTIMAL_STATES and state_status == "optimal":
        state_status = "warning"
    if state_status == "unknown":
        state_status = "warning"
    temp_status = temperature_severity(
        drive.temperature_celsius,
        temp_warning=temp_warning,
        temp_critical=temp_critical,
    )
    if temp_status == "unknown":
        temp_status = "optimal"
    return _higher_severity(state_status, temp_status)


def _drive_temperature_badge(
    drive: PhysicalDriveSnapshot,
    *,
    temp_warning: int,
    temp_critical: int,
    row_state: str,
) -> str:
    temp_status = temperature_severity(
        drive.temperature_celsius,
        temp_warning=temp_warning,
        temp_critical=temp_critical,
    )
    if temp_status == "unknown":
        return "unknown"
    if row_state in {"critical", "warning"}:
        return "neutral"
    return temp_status


def _drive_status_icon(row_state: str) -> str:
    return {
        "optimal": "check-circle",
        "warning": "alert-triangle",
        "critical": "x-circle",
    }.get(row_state, "help-circle")


def _virtual_drive_controller_health_status(
    virtual_drives: Sequence[VirtualDriveSnapshot],
) -> Literal["optimal", "warning", "critical"]:
    severity: Literal["optimal", "warning", "critical"] = "optimal"
    for virtual_drive in virtual_drives:
        severity = _worst_controller_health(
            severity,
            _event_severity_to_status(virtual_drive_state_severity(virtual_drive.state)),
        )
    return severity


def _physical_drive_aggregate_status(
    physical_drives: Sequence[PhysicalDriveSnapshot],
) -> Literal["optimal", "warning", "critical"]:
    severity: Literal["optimal", "warning", "critical"] = "optimal"
    for physical_drive in physical_drives:
        state_status = _event_severity_to_status(
            physical_drive_state_severity("Onln", physical_drive.state)
        )
        if physical_drive.state not in _PD_OPTIMAL_STATES and state_status == "optimal":
            state_status = "warning"
        severity = _worst_controller_health(severity, state_status)
    return severity


def _dominant_raid_level(virtual_drives: Sequence[VirtualDriveSnapshot]) -> str:
    if not virtual_drives:
        return "Unknown"
    raid_levels = Counter(virtual_drive.raid_level for virtual_drive in virtual_drives)
    return sorted(raid_levels.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _virtual_drive_state_label(state: str) -> str:
    labels = {
        "Optl": "Optimal",
        "Optimal": "Optimal",
        "Pdgd": "Degraded",
        "Partially Degraded": "Degraded",
        "Degraded": "Degraded",
        "Failed": "Failed",
        "Offln": "Failed",
        "Offline": "Failed",
    }
    return labels.get(state, state)


def _event_severity_to_status(severity: str) -> str:
    if severity == "info":
        return "optimal"
    if severity == "critical":
        return "critical"
    if severity == "warning":
        return "warning"
    return "unknown"


def temperature_severity(
    temperature_celsius: int | None,
    *,
    temp_warning: int,
    temp_critical: int,
) -> str:
    if temperature_celsius is None:
        return "unknown"
    if temperature_celsius >= temp_critical:
        return "critical"
    if temperature_celsius >= temp_warning:
        return "warning"
    return "optimal"


def _temperature_tooltip(
    value: int | None,
    *,
    warning: int,
    critical: int,
) -> str | None:
    if value is None:
        return None
    return f"Current {value} C / Warning {warning} C / Critical {critical} C"


def format_tb(size_bytes: int) -> str:
    return f"{size_bytes / 10**12:.1f} TB"


def _worst_severity(current: str, candidate: str) -> str:
    return _higher_severity(current, candidate)


def _worst_controller_health(
    current: Literal["optimal", "warning", "critical"],
    candidate: str,
) -> Literal["optimal", "warning", "critical"]:
    if candidate == "critical":
        return "critical"
    if candidate == "warning" and current == "optimal":
        return "warning"
    return current


def _higher_severity(a: str, b: str) -> str:
    return a if _SEVERITY_RANK.index(a) <= _SEVERITY_RANK.index(b) else b


def _pluralize(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)
