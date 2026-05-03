from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings, get_settings
from megaraid_dashboard.db.dao import (
    count_events_notified_since,
    get_latest_snapshot,
    iter_pending_events,
)
from megaraid_dashboard.db.models import (
    CacheVaultSnapshot,
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.event_detector import (
    physical_drive_state_severity,
    virtual_drive_state_severity,
)

_CONTROLLER_LABEL = "LSI MegaRAID SAS9270CV-8i"
_VD_OPTIMAL_STATES = {"Optl", "Optimal"}
_PD_OPTIMAL_STATES = {"Onln"}
_CACHEVAULT_OPTIMAL_STATES = {"Optl", "Optimal"}
_VD_DEGRADED_STATES = {"Dgrd", "Degraded"}
_VD_PARTIALLY_DEGRADED_STATES = {"Pdgd", "Partially Degraded"}
_VD_CRITICAL_STATES = {"Failed", "Offln", "Offline"}


@dataclass(frozen=True)
class StatusBadge:
    label: str
    severity: str


@dataclass(frozen=True)
class StatCard:
    label: str
    value: str
    severity: str
    badges: tuple[StatusBadge, ...] = ()


@dataclass(frozen=True)
class PhysicalDriveRow:
    slot: str
    slot_url: str
    model: str
    serial_number: str
    temperature: str
    temperature_severity: str
    media_errors: int
    other_errors: int
    predictive_failures: int
    smart_label: str
    smart_severity: str


@dataclass(frozen=True)
class AlertStatusSection:
    last_alert_sent_at: datetime | None
    pending_count: int
    sent_last_hour: int
    health: str
    health_status: str
    health_label: str


@dataclass(frozen=True)
class RocTemperatureSection:
    value: int | None
    status: str
    label: str
    warning_threshold: int
    critical_threshold: int


@dataclass(frozen=True)
class StripTileViewModel:
    label: str
    value: str
    status: str
    icon: str
    href: str


@dataclass(frozen=True)
class OverviewStripSection:
    controller: StripTileViewModel
    vd: StripTileViewModel
    raid: StripTileViewModel
    bbu: StripTileViewModel
    max_temp: StripTileViewModel
    roc: StripTileViewModel


@dataclass(frozen=True)
class OverviewViewModel:
    has_snapshot: bool
    controller_label: str
    captured_at: datetime | None
    cards: tuple[StatCard, ...]
    strip: OverviewStripSection
    physical_drives: tuple[PhysicalDriveRow, ...]
    max_temperature_celsius: int | None
    elevated_drive_count: int
    critical_drive_count: int
    alert_status: AlertStatusSection
    roc_temperature: RocTemperatureSection
    empty_title: str
    empty_body: str
    empty_next_run: str


@dataclass(frozen=True)
class DriveListViewModel:
    has_snapshot: bool
    controller_label: str
    captured_at: datetime | None
    physical_drives: tuple[PhysicalDriveRow, ...]
    empty_title: str
    empty_body: str
    empty_next_run: str


class _SchedulerJob(Protocol):
    next_run_time: datetime | None


class _Scheduler(Protocol):
    def get_job(self, job_id: str) -> _SchedulerJob | None: ...


def load_overview_view_model(
    session: Session,
    *,
    scheduler: _Scheduler | None = None,
    now: datetime | None = None,
    overview_url: str = "/",
    drives_url: str = "/drives",
) -> OverviewViewModel:
    settings = get_settings()
    resolved_now = datetime.now(UTC) if now is None else _require_aware_utc(now)
    alert_status = _load_alert_status(session, settings=settings, now=resolved_now)
    snapshot = get_latest_snapshot(session)
    roc_temperature = _load_roc_temperature(session, settings=settings, latest_snapshot=snapshot)
    strip = _load_overview_strip(
        latest_snapshot=snapshot,
        settings=settings,
        roc=roc_temperature,
        overview_url=overview_url,
        drives_url=drives_url,
    )
    if snapshot is None:
        return OverviewViewModel(
            has_snapshot=False,
            controller_label=_CONTROLLER_LABEL,
            captured_at=None,
            cards=(),
            strip=strip,
            physical_drives=(),
            max_temperature_celsius=None,
            elevated_drive_count=0,
            critical_drive_count=0,
            alert_status=alert_status,
            roc_temperature=roc_temperature,
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
    overview_virtual_drive = _select_overview_virtual_drive(snapshot.virtual_drives)
    cachevault = snapshot.cachevault
    temp_warning = settings.temp_warning_celsius
    temp_critical = settings.temp_critical_celsius
    max_temp = _max_temperature(sorted_drives)
    elevated_count = _temperature_count(sorted_drives, threshold=temp_warning)
    critical_count = _temperature_count(sorted_drives, threshold=temp_critical)

    return OverviewViewModel(
        has_snapshot=True,
        controller_label=_CONTROLLER_LABEL,
        captured_at=snapshot.captured_at,
        cards=(
            _controller_health_card(snapshot=snapshot),
            _virtual_drive_card(overview_virtual_drive),
            _raid_type_card(overview_virtual_drive),
            _size_card(overview_virtual_drive),
            _cachevault_card(
                cachevault,
                capacitance_warning_percent=settings.cv_capacitance_warning_percent,
            ),
            _max_disk_temp_card(
                max_temp=max_temp,
                elevated_count=elevated_count,
                critical_count=critical_count,
                temp_warning=temp_warning,
                temp_critical=temp_critical,
            ),
        ),
        strip=strip,
        physical_drives=tuple(
            _physical_drive_row(
                drive,
                temp_warning=temp_warning,
                temp_critical=temp_critical,
            )
            for drive in sorted_drives
        ),
        max_temperature_celsius=max_temp,
        elevated_drive_count=elevated_count,
        critical_drive_count=critical_count,
        alert_status=alert_status,
        roc_temperature=roc_temperature,
        empty_title="Waiting for first metrics collection",
        empty_body="The collector has not yet completed its first run.",
        empty_next_run="",
    )


def _load_overview_strip(
    *,
    latest_snapshot: ControllerSnapshot | None,
    settings: Settings,
    roc: RocTemperatureSection,
    overview_url: str = "/",
    drives_url: str = "/drives",
) -> OverviewStripSection:
    return OverviewStripSection(
        controller=_load_controller_tile(latest_snapshot, overview_url=overview_url),
        vd=_load_vd_tile(latest_snapshot, overview_url=overview_url),
        raid=_load_raid_tile(latest_snapshot, overview_url=overview_url),
        bbu=_load_bbu_tile(latest_snapshot, overview_url=overview_url, drives_url=drives_url),
        max_temp=_load_max_temp_tile(latest_snapshot, settings=settings, drives_url=drives_url),
        roc=_load_roc_tile(roc, overview_url=overview_url),
    )


def _load_controller_tile(
    latest_snapshot: ControllerSnapshot | None,
    *,
    overview_url: str = "/",
) -> StripTileViewModel:
    status = "neutral"
    value = "Unknown"
    if latest_snapshot is not None:
        alarm_state = latest_snapshot.alarm_state.casefold()
        status = "optimal" if alarm_state in {"none", "off"} else "critical"
        value = "Optimal" if status == "optimal" else "Alarm"

    return StripTileViewModel(
        label="Controller",
        value=value,
        status=status,
        icon="cpu",
        href=overview_url,
    )


def _load_vd_tile(
    latest_snapshot: ControllerSnapshot | None,
    *,
    overview_url: str = "/",
) -> StripTileViewModel:
    virtual_drives = () if latest_snapshot is None else tuple(latest_snapshot.virtual_drives)
    status = _virtual_drive_aggregate_status(virtual_drives)
    return StripTileViewModel(
        label="VD",
        value=_virtual_drive_aggregate_value(virtual_drives),
        status=status,
        icon="hard-drive",
        href=overview_url,
    )


def _load_raid_tile(
    latest_snapshot: ControllerSnapshot | None,
    *,
    overview_url: str = "/",
) -> StripTileViewModel:
    virtual_drives = () if latest_snapshot is None else tuple(latest_snapshot.virtual_drives)
    return StripTileViewModel(
        label="RAID",
        value=_dominant_raid_level(virtual_drives),
        status=_virtual_drive_aggregate_status(virtual_drives),
        icon="hard-drive",
        href=overview_url,
    )


def _load_bbu_tile(
    latest_snapshot: ControllerSnapshot | None,
    *,
    overview_url: str = "/",
    drives_url: str = "/drives",
) -> StripTileViewModel:
    status = "neutral"
    value = "Unknown"
    href = overview_url
    if latest_snapshot is not None and latest_snapshot.cachevault is not None:
        cachevault = latest_snapshot.cachevault
        href = drives_url
        if cachevault.replacement_required:
            status = "critical"
            value = "Replace"
        elif cachevault.state not in _CACHEVAULT_OPTIMAL_STATES:
            status = "warning"
            value = "Warning"
        else:
            status = "optimal"
            value = "Optimal"
    elif latest_snapshot is not None and not latest_snapshot.bbu_present:
        value = "None"

    return StripTileViewModel(
        label="BBU",
        value=value,
        status=status,
        icon="lightbulb",
        href=href,
    )


def _load_max_temp_tile(
    latest_snapshot: ControllerSnapshot | None,
    *,
    settings: Settings,
    drives_url: str = "/drives",
) -> StripTileViewModel:
    physical_drives = () if latest_snapshot is None else tuple(latest_snapshot.physical_drives)
    hottest_drive = _hottest_drive(physical_drives)
    max_temp = None if hottest_drive is None else hottest_drive.temperature_celsius
    value = "Unknown" if max_temp is None else f"{max_temp} C"
    status = (
        "neutral"
        if max_temp is None
        else temperature_severity(
            max_temp,
            temp_warning=settings.temp_warning_celsius,
            temp_critical=settings.temp_critical_celsius,
        )
    )
    return StripTileViewModel(
        label="MaxTemp",
        value=value,
        status=status,
        icon="thermometer",
        href=drives_url if hottest_drive is None else _drive_detail_url(drives_url, hottest_drive),
    )


def _load_roc_tile(
    roc: RocTemperatureSection,
    *,
    overview_url: str = "/",
) -> StripTileViewModel:
    return StripTileViewModel(
        label="RoC",
        value=roc.label,
        status=roc.status,
        icon="thermometer",
        href=overview_url,
    )


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

    return DriveListViewModel(
        has_snapshot=True,
        controller_label=_CONTROLLER_LABEL,
        captured_at=snapshot.captured_at,
        physical_drives=tuple(
            _physical_drive_row(
                drive,
                temp_warning=temp_warning,
                temp_critical=temp_critical,
                slot_url=slot_url_factory(drive.enclosure_id, drive.slot_id),
            )
            for drive in sorted_drives
        ),
        empty_title="Waiting for first metrics collection",
        empty_body="The collector has not yet completed its first run.",
        empty_next_run="",
    )


def _load_alert_status(
    session: Session,
    *,
    settings: Settings,
    now: datetime,
) -> AlertStatusSection:
    now_utc = _require_aware_utc(now)
    last_alert_sent_at = session.scalar(select(func.max(Event.notified_at)))
    normalized_last_sent = (
        None if last_alert_sent_at is None else _require_aware_utc(last_alert_sent_at)
    )
    pending_since = now_utc - timedelta(minutes=settings.alert_suppress_window_minutes)
    pending_count = len(
        list(
            iter_pending_events(
                session,
                severity_threshold=settings.alert_severity_threshold,
                since=pending_since,
            )
        )
    )
    sent_last_hour = count_events_notified_since(session, since=now_utc - timedelta(hours=1))
    health = _alert_health(
        pending_count=pending_count,
        last_alert_sent_at=normalized_last_sent,
        now=now_utc,
    )
    return AlertStatusSection(
        last_alert_sent_at=normalized_last_sent,
        pending_count=pending_count,
        sent_last_hour=sent_last_hour,
        health=health,
        health_status=health,
        health_label=_alert_health_label(health),
    )


def _load_roc_temperature(
    session: Session,
    *,
    settings: Settings,
    latest_snapshot: ControllerSnapshot | None,
) -> RocTemperatureSection:
    del session
    warning_threshold = settings.roc_temp_warning_celsius
    critical_threshold = settings.roc_temp_critical_celsius
    if latest_snapshot is None or latest_snapshot.roc_temperature_celsius is None:
        return RocTemperatureSection(
            value=None,
            status="neutral",
            label="Unknown",
            warning_threshold=warning_threshold,
            critical_threshold=critical_threshold,
        )

    value = latest_snapshot.roc_temperature_celsius
    status = temperature_severity(
        value,
        temp_warning=warning_threshold,
        temp_critical=critical_threshold,
    )
    label = f"{value} C" if status == "optimal" else f"{value} C ({status})"
    return RocTemperatureSection(
        value=value,
        status=status,
        label=label,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
    )


def _alert_health(
    *,
    pending_count: int,
    last_alert_sent_at: datetime | None,
    now: datetime,
) -> str:
    if pending_count == 0:
        return "optimal"
    if last_alert_sent_at is None:
        return "critical"

    age = _require_aware_utc(now) - _require_aware_utc(last_alert_sent_at)
    if age < timedelta(minutes=2):
        return "optimal"
    if age < timedelta(minutes=10):
        return "warning"
    return "critical"


def _alert_health_label(health: str) -> str:
    labels = {
        "optimal": "Notifier OK",
        "warning": "Notifier catching up",
        "critical": "Notifier appears stuck",
    }
    return labels[health]


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


def _controller_health_card(*, snapshot: ControllerSnapshot) -> StatCard:
    severity = "optimal"
    if snapshot.alarm_state != "Off":
        severity = _worst_severity(severity, "warning")

    for virtual_drive in snapshot.virtual_drives:
        if virtual_drive.state not in _VD_OPTIMAL_STATES:
            severity = _worst_severity(
                severity,
                _event_severity_to_status(virtual_drive_state_severity(virtual_drive.state)),
            )

    for physical_drive in snapshot.physical_drives:
        if physical_drive.state not in _PD_OPTIMAL_STATES:
            severity = _worst_severity(
                severity,
                _event_severity_to_status(
                    physical_drive_state_severity("Onln", physical_drive.state)
                ),
            )
            if severity == "optimal":
                severity = "warning"

    value_by_severity = {
        "optimal": "Optimal",
        "warning": "Degraded",
        "critical": "Critical",
        "unknown": "Unknown",
    }
    return StatCard(
        label="Controller Health",
        value=value_by_severity[severity],
        severity=severity,
    )


def _virtual_drive_card(virtual_drive: VirtualDriveSnapshot | None) -> StatCard:
    if virtual_drive is None:
        return StatCard(label="Virtual Drive", value="Unknown", severity="unknown")
    return StatCard(
        label="Virtual Drive",
        value=_virtual_drive_state_label(virtual_drive.state),
        severity=_event_severity_to_status(virtual_drive_state_severity(virtual_drive.state)),
    )


def _raid_type_card(virtual_drive: VirtualDriveSnapshot | None) -> StatCard:
    value = "Unknown" if virtual_drive is None else virtual_drive.raid_level
    severity = "unknown" if virtual_drive is None else "neutral"
    return StatCard(label="RAID Type", value=value, severity=severity)


def _size_card(virtual_drive: VirtualDriveSnapshot | None) -> StatCard:
    value = "Unknown" if virtual_drive is None else format_tb(virtual_drive.size_bytes)
    severity = "unknown" if virtual_drive is None else "neutral"
    return StatCard(label="Size", value=value, severity=severity)


def _cachevault_card(
    cachevault: CacheVaultSnapshot | None,
    *,
    capacitance_warning_percent: int,
) -> StatCard:
    if cachevault is None:
        return StatCard(label="BBU/CV", value="Absent", severity="unknown")
    if cachevault.replacement_required or cachevault.state not in _CACHEVAULT_OPTIMAL_STATES:
        return StatCard(label="BBU/CV", value="Replace", severity="critical")
    if cachevault.capacitance_percent is None:
        return StatCard(label="BBU/CV", value="Unknown", severity="unknown")
    if cachevault.capacitance_percent >= capacitance_warning_percent:
        return StatCard(label="BBU/CV", value="Opt", severity="optimal")
    if cachevault.capacitance_percent > 0:
        return StatCard(label="BBU/CV", value="Warning", severity="warning")
    return StatCard(label="BBU/CV", value="Unknown", severity="unknown")


def _max_disk_temp_card(
    *,
    max_temp: int | None,
    elevated_count: int,
    critical_count: int,
    temp_warning: int,
    temp_critical: int,
) -> StatCard:
    if max_temp is None:
        return StatCard(label="Max Disk Temp", value="Unknown", severity="unknown")

    badges: list[StatusBadge] = []
    if critical_count > 0:
        badges.append(StatusBadge(label=f"{critical_count} drives critical", severity="critical"))
    if elevated_count > 0:
        badges.append(StatusBadge(label=f"{elevated_count} drives elevated", severity="warning"))

    return StatCard(
        label="Max Disk Temp",
        value=f"{max_temp} C",
        severity=temperature_severity(
            max_temp,
            temp_warning=temp_warning,
            temp_critical=temp_critical,
        ),
        badges=tuple(badges),
    )


def _physical_drive_row(
    drive: PhysicalDriveSnapshot,
    *,
    temp_warning: int,
    temp_critical: int,
    slot_url: str = "",
) -> PhysicalDriveRow:
    smart_alert = drive.smart_alert
    return PhysicalDriveRow(
        slot=f"e{drive.enclosure_id}:s{drive.slot_id}",
        slot_url=slot_url,
        model=drive.model,
        serial_number=drive.serial_number,
        temperature="Unknown"
        if drive.temperature_celsius is None
        else f"{drive.temperature_celsius} C",
        temperature_severity=temperature_severity(
            drive.temperature_celsius,
            temp_warning=temp_warning,
            temp_critical=temp_critical,
        ),
        media_errors=drive.media_errors,
        other_errors=drive.other_errors,
        predictive_failures=drive.predictive_failures,
        smart_label="Yes" if smart_alert else "No",
        smart_severity="critical" if smart_alert else "neutral",
    )


def _find_virtual_drive(
    virtual_drives: Sequence[VirtualDriveSnapshot],
    *,
    vd_id: int,
) -> VirtualDriveSnapshot | None:
    for virtual_drive in virtual_drives:
        if virtual_drive.vd_id == vd_id:
            return virtual_drive
    return None


def _select_overview_virtual_drive(
    virtual_drives: Sequence[VirtualDriveSnapshot],
) -> VirtualDriveSnapshot | None:
    vd0 = _find_virtual_drive(virtual_drives, vd_id=0)
    if vd0 is not None:
        return vd0
    if not virtual_drives:
        return None
    return min(virtual_drives, key=lambda virtual_drive: virtual_drive.vd_id)


def _virtual_drive_aggregate_status(virtual_drives: Sequence[VirtualDriveSnapshot]) -> str:
    if not virtual_drives:
        return "neutral"
    states = {virtual_drive.state for virtual_drive in virtual_drives}
    if states & _VD_CRITICAL_STATES:
        return "critical"
    if states & (_VD_DEGRADED_STATES | _VD_PARTIALLY_DEGRADED_STATES):
        return "warning"
    if states <= _VD_OPTIMAL_STATES:
        return "optimal"
    return "warning"


def _virtual_drive_aggregate_value(virtual_drives: Sequence[VirtualDriveSnapshot]) -> str:
    if not virtual_drives:
        return "Unknown"

    critical_count = sum(
        1 for virtual_drive in virtual_drives if virtual_drive.state in _VD_CRITICAL_STATES
    )
    if critical_count > 0:
        return f"{critical_count} failed"

    degraded_count = sum(
        1
        for virtual_drive in virtual_drives
        if virtual_drive.state in (_VD_DEGRADED_STATES | _VD_PARTIALLY_DEGRADED_STATES)
    )
    if degraded_count > 0:
        return f"{degraded_count} degraded"

    optimal_count = sum(
        1 for virtual_drive in virtual_drives if virtual_drive.state in _VD_OPTIMAL_STATES
    )
    if optimal_count == len(virtual_drives):
        return f"{optimal_count}/{len(virtual_drives)} OK"

    return f"{len(virtual_drives) - optimal_count} unknown"


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


def _temperature_severity(
    temperature_celsius: int | None,
    *,
    temp_warning: int,
    temp_critical: int,
) -> str:
    return temperature_severity(
        temperature_celsius,
        temp_warning=temp_warning,
        temp_critical=temp_critical,
    )


def _temperature_count(
    physical_drives: Sequence[PhysicalDriveSnapshot],
    *,
    threshold: int,
) -> int:
    return sum(
        1
        for drive in physical_drives
        if drive.temperature_celsius is not None and drive.temperature_celsius >= threshold
    )


def _max_temperature(physical_drives: Sequence[PhysicalDriveSnapshot]) -> int | None:
    temperatures = [
        drive.temperature_celsius
        for drive in physical_drives
        if drive.temperature_celsius is not None
    ]
    return max(temperatures) if temperatures else None


def _hottest_drive(
    physical_drives: Sequence[PhysicalDriveSnapshot],
) -> PhysicalDriveSnapshot | None:
    drives_with_temperature = [
        drive for drive in physical_drives if drive.temperature_celsius is not None
    ]
    if not drives_with_temperature:
        return None
    return sorted(
        drives_with_temperature,
        key=lambda drive: (-_require_temperature(drive), drive.enclosure_id, drive.slot_id),
    )[0]


def _require_temperature(drive: PhysicalDriveSnapshot) -> int:
    if drive.temperature_celsius is None:
        msg = "drive temperature is required"
        raise ValueError(msg)
    return drive.temperature_celsius


def _drive_detail_url(drives_url: str, drive: PhysicalDriveSnapshot) -> str:
    return f"{drives_url.rstrip('/')}/{drive.enclosure_id}/{drive.slot_id}"


def format_tb(size_bytes: int) -> str:
    return f"{size_bytes / 10**12:.1f} TB"


def _format_tb(size_bytes: int) -> str:
    return format_tb(size_bytes)


def _worst_severity(current: str, candidate: str) -> str:
    severity_rank = {
        "unknown": 0,
        "neutral": 0,
        "optimal": 1,
        "warning": 2,
        "critical": 3,
    }
    return candidate if severity_rank[candidate] > severity_rank[current] else current


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)
