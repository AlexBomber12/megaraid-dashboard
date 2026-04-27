from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import get_latest_snapshot
from megaraid_dashboard.db.models import (
    CacheVaultSnapshot,
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.event_detector import (
    _physical_drive_state_severity,
    _virtual_drive_state_severity,
)

_CONTROLLER_LABEL = "LSI MegaRAID SAS9270CV-8i"
_VD_OPTIMAL_STATES = {"Optl", "Optimal"}
_PD_OPTIMAL_STATES = {"Onln"}
_CACHEVAULT_OPTIMAL_STATES = {"Optl", "Optimal"}


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
class OverviewViewModel:
    has_snapshot: bool
    controller_label: str
    captured_at: datetime | None
    cards: tuple[StatCard, ...]
    physical_drives: tuple[PhysicalDriveRow, ...]
    max_temperature_celsius: int | None
    elevated_drive_count: int
    critical_drive_count: int
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
) -> OverviewViewModel:
    settings = get_settings()
    snapshot = get_latest_snapshot(session)
    if snapshot is None:
        return OverviewViewModel(
            has_snapshot=False,
            controller_label=_CONTROLLER_LABEL,
            captured_at=None,
            cards=(),
            physical_drives=(),
            max_temperature_celsius=None,
            elevated_drive_count=0,
            critical_drive_count=0,
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
        empty_title="Waiting for first metrics collection",
        empty_body="The collector has not yet completed its first run.",
        empty_next_run="",
    )


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
                _event_severity_to_status(_virtual_drive_state_severity(virtual_drive.state)),
            )

    for physical_drive in snapshot.physical_drives:
        if physical_drive.state not in _PD_OPTIMAL_STATES:
            severity = _worst_severity(
                severity,
                _event_severity_to_status(
                    _physical_drive_state_severity("Onln", physical_drive.state)
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
        severity=_event_severity_to_status(_virtual_drive_state_severity(virtual_drive.state)),
    )


def _raid_type_card(virtual_drive: VirtualDriveSnapshot | None) -> StatCard:
    value = "Unknown" if virtual_drive is None else virtual_drive.raid_level
    severity = "unknown" if virtual_drive is None else "neutral"
    return StatCard(label="RAID Type", value=value, severity=severity)


def _size_card(virtual_drive: VirtualDriveSnapshot | None) -> StatCard:
    value = "Unknown" if virtual_drive is None else _format_tb(virtual_drive.size_bytes)
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
        severity=_temperature_severity(
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
) -> PhysicalDriveRow:
    smart_alert = drive.smart_alert
    return PhysicalDriveRow(
        slot=f"e{drive.enclosure_id}:s{drive.slot_id}",
        model=drive.model,
        serial_number=drive.serial_number,
        temperature="Unknown"
        if drive.temperature_celsius is None
        else f"{drive.temperature_celsius} C",
        temperature_severity=_temperature_severity(
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


def _temperature_severity(
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


def _format_tb(size_bytes: int) -> str:
    return f"{size_bytes / 10**12:.1f} TB"


def _worst_severity(current: str, candidate: str) -> str:
    severity_rank = {
        "unknown": 0,
        "neutral": 0,
        "optimal": 1,
        "warning": 2,
        "critical": 3,
    }
    return candidate if severity_rank[candidate] > severity_rank[current] else current
