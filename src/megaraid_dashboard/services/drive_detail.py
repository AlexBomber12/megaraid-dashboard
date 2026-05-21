"""Drive detail page view model assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import ControllerSnapshot, Event, PhysicalDriveSnapshot
from megaraid_dashboard.services.drive_history import (
    DriveTemperatureSeries,
    load_drive_temperature_series,
)
from megaraid_dashboard.services.event_detector import physical_drive_state_severity
from megaraid_dashboard.services.overview import (
    SystemHealthViewModel,
    _event_severity_to_status,
    _load_system_health,
    compute_drive_tile_severity,
    format_tb,
    temperature_severity,
)

_DEFAULT_REFRESH_SECONDS = 30
_BACKPLANE_SLOT_COUNT = 8
_REPLACE_WARNING_TEXT = (
    "Replacing a drive changes RAID array state. Confirm the affected slot and serial number "
    "before continuing."
)
_OPTIMAL_STATES = {"Onln", "Online", "ONLINE", "UGood"}
_CRITICAL_STATES = {"Failed", "MISSING", "Missing", "UBad"}
_REBUILD_STATES = {"Rbld", "Rebld", "Rebuild", "Rebuilding"}


@dataclass(frozen=True)
class DriveHealthSnapshot:
    state: str
    state_severity: str
    state_subtitle: str
    summary_text: str
    temperature_celsius: int | None
    temperature_severity: str
    smart_status: str
    smart_severity: str
    predictive_failure_count: int
    can_locate_start: bool
    can_locate_stop: bool
    locate_active: bool
    locate_start_url: str
    locate_stop_url: str


@dataclass(frozen=True)
class ErrorSparklinePoint:
    date: date
    total_count: int
    incremental_delta: int


@dataclass(frozen=True)
class ErrorSparkline:
    current_total: int
    media_errors: int
    other_errors: int
    bbm_errors: int
    shield_counter: int
    points: list[ErrorSparklinePoint]
    meta_text: str


@dataclass(frozen=True)
class BackplaneSlot:
    slot_label: str
    enclosure_id: int
    slot_id: int
    is_this: bool
    severity: str
    detail_url: str


@dataclass(frozen=True)
class PhysicalPosition:
    enclosure: int
    slot: int
    dg_span_row_text: str
    port_text: str
    backplane_layout: list[BackplaneSlot]


@dataclass(frozen=True)
class DriveIdentity:
    model: str
    serial_number: str
    manufacturer_text: str
    firmware_revision: str
    wwn: str
    media_type: str
    raw_size_text: str
    coerced_size_text: str
    logical_sector_size_text: str


@dataclass(frozen=True)
class DriveConnection:
    interface_text: str
    device_speed_text: str
    link_speed_text: str
    link_speed_is_degraded: bool
    ncq_enabled_text: str
    sas_address: str
    connector_text: str
    sequence_number: str
    wide_port_text: str


@dataclass(frozen=True)
class AdvancedActionButton:
    label: str
    url: str
    is_destructive: bool
    is_enabled: bool
    disabled_reason: str | None


@dataclass(frozen=True)
class ReplaceWizardState:
    can_begin: bool
    current_step: int | None
    begin_url: str
    resume_url: str | None
    warning_text: str


@dataclass(frozen=True)
class DriveDetailViewModel:
    page_title: str
    page_subtitle: str
    prev_drive_url: str | None
    next_drive_url: str | None
    health: DriveHealthSnapshot
    error_sparkline: ErrorSparkline
    position: PhysicalPosition
    temperature_chart: DriveTemperatureSeries
    identity: DriveIdentity
    connection: DriveConnection
    advanced_actions: list[AdvancedActionButton]
    replace: ReplaceWizardState
    system_health: SystemHealthViewModel
    updated_at: datetime
    auto_refresh_seconds: int


@dataclass(frozen=True)
class _DailyErrorSnapshot:
    day: date
    captured_at: datetime
    media_errors: int
    other_errors: int
    bbm_errors: int
    shield_counter: int

    @property
    def total(self) -> int:
        return self.media_errors + self.other_errors + self.bbm_errors + self.shield_counter


def load_drive_detail_view_model(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    settings: Settings,
    app_version: str,
    range_days: int = 30,
) -> DriveDetailViewModel:
    if range_days <= 0:
        msg = "range_days must be positive"
        raise ValueError(msg)

    snapshot = _load_latest_snapshot(session)
    if snapshot is None:
        msg = "no controller snapshot available"
        raise LookupError(msg)
    drive = _find_drive(snapshot, enclosure_id=enclosure_id, slot_id=slot_id)
    if drive is None:
        msg = f"drive {enclosure_id}:{slot_id} not found"
        raise LookupError(msg)

    now = _require_aware_utc(snapshot.captured_at)
    return DriveDetailViewModel(
        page_title=f"Slot {drive.enclosure_id}:{drive.slot_id} (S{drive.slot_id})",
        page_subtitle=f"{drive.model} / {drive.serial_number}",
        prev_drive_url=_neighbor_drive_url(snapshot.physical_drives, drive, offset=-1),
        next_drive_url=_neighbor_drive_url(snapshot.physical_drives, drive, offset=1),
        health=_build_health_snapshot(session, drive=drive, snapshot=snapshot, settings=settings),
        error_sparkline=_load_error_sparkline(
            session,
            serial_number=drive.serial_number,
            days=range_days,
        ),
        position=PhysicalPosition(
            enclosure=drive.enclosure_id,
            slot=drive.slot_id,
            dg_span_row_text=_dg_span_row_text(drive),
            port_text=_port_text(drive),
            backplane_layout=_load_backplane_layout(
                session,
                this_enclosure=drive.enclosure_id,
                this_slot=drive.slot_id,
                settings=settings,
            ),
        ),
        temperature_chart=load_drive_temperature_series(
            session,
            enclosure_id=drive.enclosure_id,
            slot_id=drive.slot_id,
            current_serial_number=drive.serial_number,
            range_days=range_days,
            now_utc=now,
        ),
        identity=_build_identity(drive),
        connection=_build_connection(drive),
        advanced_actions=_advanced_actions(drive),
        replace=_replace_wizard_state(session, drive),
        system_health=_load_system_health(
            settings=settings,
            scheduler=None,
            collector_enabled=settings.collector_enabled,
            app_version=app_version,
            now=now,
        ),
        updated_at=now,
        auto_refresh_seconds=getattr(settings, "auto_refresh_seconds", _DEFAULT_REFRESH_SECONDS),
    )


def _load_error_sparkline(
    session: Session,
    *,
    serial_number: str,
    days: int,
) -> ErrorSparkline:
    if days <= 0:
        msg = "days must be positive"
        raise ValueError(msg)

    latest_captured_at = session.scalar(
        select(ControllerSnapshot.captured_at)
        .join(PhysicalDriveSnapshot)
        .where(PhysicalDriveSnapshot.serial_number == serial_number)
        .order_by(ControllerSnapshot.captured_at.desc())
        .limit(1)
    )
    end_day = _require_aware_utc(latest_captured_at).date() if latest_captured_at else _today_utc()
    start_day = end_day - timedelta(days=days - 1)
    latest_by_day: dict[date, _DailyErrorSnapshot] = {}

    rows = session.execute(
        select(
            ControllerSnapshot.captured_at,
            PhysicalDriveSnapshot.media_errors,
            PhysicalDriveSnapshot.other_errors,
            PhysicalDriveSnapshot.predictive_failures,
        )
        .join(PhysicalDriveSnapshot)
        .where(PhysicalDriveSnapshot.serial_number == serial_number)
        .where(
            ControllerSnapshot.captured_at >= datetime.combine(start_day, datetime.min.time(), UTC)
        )
        .where(
            ControllerSnapshot.captured_at
            < datetime.combine(end_day + timedelta(days=1), datetime.min.time(), UTC)
        )
        .order_by(ControllerSnapshot.captured_at.asc(), PhysicalDriveSnapshot.id.asc())
    )
    for captured_at, media_errors, other_errors, predictive_failures in rows:
        captured_at_utc = _require_aware_utc(captured_at)
        daily_snapshot = _DailyErrorSnapshot(
            day=captured_at_utc.date(),
            captured_at=captured_at_utc,
            media_errors=int(media_errors),
            other_errors=int(other_errors),
            bbm_errors=0,
            shield_counter=int(predictive_failures),
        )
        previous = latest_by_day.get(daily_snapshot.day)
        if (
            previous is None or daily_snapshot.captured_at >= previous.captured_at
        ):  # pragma: no branch
            latest_by_day[daily_snapshot.day] = daily_snapshot

    points: list[ErrorSparklinePoint] = []
    previous_total = 0
    latest = _DailyErrorSnapshot(start_day, datetime.min.replace(tzinfo=UTC), 0, 0, 0, 0)
    for day_offset in range(days):
        current_day = start_day + timedelta(days=day_offset)
        latest = latest_by_day.get(current_day, latest)
        delta = max(0, latest.total - previous_total)
        points.append(
            ErrorSparklinePoint(
                date=current_day,
                total_count=latest.total,
                incremental_delta=delta,
            )
        )
        previous_total = latest.total

    return ErrorSparkline(
        current_total=latest.total,
        media_errors=latest.media_errors,
        other_errors=latest.other_errors,
        bbm_errors=latest.bbm_errors,
        shield_counter=latest.shield_counter,
        points=points,
        meta_text=(
            f"Media {latest.media_errors} / Other {latest.other_errors} / "
            f"BBM {latest.bbm_errors} / Shield {latest.shield_counter}"
        ),
    )


def _load_backplane_layout(
    session: Session,
    *,
    this_enclosure: int,
    this_slot: int,
    settings: Settings | None = None,
) -> list[BackplaneSlot]:
    resolved_settings = settings
    if resolved_settings is None:
        from megaraid_dashboard.config import get_settings

        resolved_settings = get_settings()
    snapshot = _load_latest_snapshot(session)
    drives_by_slot = {
        drive.slot_id: drive
        for drive in (() if snapshot is None else snapshot.physical_drives)
        if drive.enclosure_id == this_enclosure
    }
    return [
        BackplaneSlot(
            slot_label=f"S{slot_id}",
            enclosure_id=this_enclosure,
            slot_id=slot_id,
            is_this=slot_id == this_slot,
            severity=_backplane_slot_severity(
                drives_by_slot.get(slot_id),
                settings=resolved_settings,
            ),
            detail_url=f"/drives/{this_enclosure}:{slot_id}",
        )
        for slot_id in range(_BACKPLANE_SLOT_COUNT)
    ]


def _load_latest_snapshot(session: Session) -> ControllerSnapshot | None:
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


def _find_drive(
    snapshot: ControllerSnapshot,
    *,
    enclosure_id: int,
    slot_id: int,
) -> PhysicalDriveSnapshot | None:
    for drive in snapshot.physical_drives:
        if drive.enclosure_id == enclosure_id and drive.slot_id == slot_id:
            return drive
    return None


def _build_health_snapshot(
    session: Session,
    *,
    drive: PhysicalDriveSnapshot,
    snapshot: ControllerSnapshot,
    settings: Settings,
) -> DriveHealthSnapshot:
    state_severity = _drive_state_severity(drive.state)
    smart_status = "ALERT" if drive.smart_alert else "OK"
    latest_locate_action = _latest_locate_action(session, drive)
    locate_active = latest_locate_action == "start"
    return DriveHealthSnapshot(
        state=drive.state.upper(),
        state_severity=state_severity,
        state_subtitle=_state_subtitle(drive, snapshot),
        summary_text=_summary_text(drive),
        temperature_celsius=drive.temperature_celsius,
        temperature_severity=temperature_severity(
            drive.temperature_celsius,
            temp_warning=settings.temp_warning_celsius,
            temp_critical=settings.temp_critical_celsius,
        ),
        smart_status=smart_status,
        smart_severity="critical" if drive.smart_alert else "optimal",
        predictive_failure_count=drive.predictive_failures,
        can_locate_start=not locate_active,
        can_locate_stop=locate_active,
        locate_active=locate_active,
        locate_start_url=f"/drives/{drive.enclosure_id}:{drive.slot_id}/locate/start",
        locate_stop_url=f"/drives/{drive.enclosure_id}:{drive.slot_id}/locate/stop",
    )


def _build_identity(drive: PhysicalDriveSnapshot) -> DriveIdentity:
    return DriveIdentity(
        model=drive.model,
        serial_number=drive.serial_number,
        manufacturer_text=_manufacturer_text(drive.interface),
        firmware_revision=drive.firmware_version,
        wwn=_drive_extra_text(drive, "wwn", "Unknown"),
        media_type=drive.media_type,
        raw_size_text=_drive_extra_text(drive, "raw_size_text", format_tb(drive.size_bytes)),
        coerced_size_text=_drive_extra_text(
            drive, "coerced_size_text", format_tb(drive.size_bytes)
        ),
        logical_sector_size_text=_drive_extra_text(drive, "logical_sector_size_text", "Unknown"),
    )


def _build_connection(drive: PhysicalDriveSnapshot) -> DriveConnection:
    device_speed = _drive_extra_text(drive, "device_speed_text", "Unknown")
    link_speed = _drive_extra_text(drive, "link_speed_text", device_speed)
    return DriveConnection(
        interface_text=_interface_text(drive, device_speed),
        device_speed_text=device_speed,
        link_speed_text=link_speed,
        link_speed_is_degraded=_speed_value(link_speed) < _speed_value(device_speed),
        ncq_enabled_text=_drive_extra_text(drive, "ncq_enabled_text", "Unknown"),
        sas_address=drive.sas_address,
        connector_text=_drive_extra_text(drive, "connector_text", "Unknown"),
        sequence_number=_drive_extra_text(drive, "sequence_number", "Unknown"),
        wide_port_text=_drive_extra_text(drive, "wide_port_text", "Unknown"),
    )


def _advanced_actions(drive: PhysicalDriveSnapshot) -> list[AdvancedActionButton]:
    state = drive.state
    slot_ref = f"{drive.enclosure_id}:{drive.slot_id}"
    return [
        _action_button(
            label="Mark as UBad",
            url=f"/drives/{slot_ref}/actions/mark-ubad",
            destructive=True,
            enabled=state == "UGood",
            reason="Only UGood drives can be marked UBad.",
        ),
        _action_button(
            label="Mark as UGood",
            url=f"/drives/{slot_ref}/actions/mark-ugood",
            destructive=False,
            enabled=state in {"UBad", "Failed", "Missing"},
            reason="Drive state does not allow Mark as UGood.",
        ),
        _action_button(
            label="Spin Down",
            url=f"/drives/{slot_ref}/actions/spindown",
            destructive=False,
            enabled=state in {"Onln", "Online", "UGood"},
            reason="Only online or unconfigured good drives can be spun down.",
        ),
        _action_button(
            label="Hot Spare",
            url=f"/drives/{slot_ref}/actions/hotspare",
            destructive=False,
            enabled=state == "UGood",
            reason="Only UGood drives can become hot spares.",
        ),
    ]


def _replace_wizard_state(session: Session, drive: PhysicalDriveSnapshot) -> ReplaceWizardState:
    current_step = _latest_replace_step(session, drive)
    return ReplaceWizardState(
        can_begin=current_step is None,
        current_step=current_step,
        begin_url=f"/drives/{drive.enclosure_id}:{drive.slot_id}/replace/offline",
        resume_url=None
        if current_step is None
        else f"/drives/{drive.enclosure_id}:{drive.slot_id}/replace/resume",
        warning_text=_REPLACE_WARNING_TEXT,
    )


def _latest_replace_step(session: Session, drive: PhysicalDriveSnapshot) -> int | None:
    slot_ref = f"{drive.enclosure_id}:{drive.slot_id}"
    event = session.scalars(
        select(Event)
        .where(
            (Event.category.in_(_REPLACE_EVENT_STEP_BY_CATEGORY))
            | (
                (Event.category == "operator_action")
                & Event.summary.like(f"%replace step%drive {slot_ref}%")
            )
        )
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(1)
    ).one_or_none()
    if event is None:
        return None
    if event.category == "drive_replace_completed" or "replace step insert" in event.summary:
        return None
    if event.category in _REPLACE_EVENT_STEP_BY_CATEGORY:
        return _REPLACE_EVENT_STEP_BY_CATEGORY[event.category]
    if "replace step missing" in event.summary or "replace step offline" in event.summary:
        return 2
    return None


_REPLACE_EVENT_STEP_BY_CATEGORY = {
    "drive_replace_begin": 1,
    "drive_replace_offline": 2,
    "drive_replace_missing": 2,
    "drive_replace_insert_pending": 3,
}


def _latest_locate_action(session: Session, drive: PhysicalDriveSnapshot) -> str | None:
    slot_ref = f"{drive.enclosure_id}:{drive.slot_id}"
    event = session.scalars(
        select(Event)
        .where(Event.category == "operator_action")
        .where(
            Event.summary.in_((f"locate start drive {slot_ref}", f"locate stop drive {slot_ref}"))
        )
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(1)
    ).one_or_none()
    if event is None:
        return None
    return "start" if "locate start" in event.summary else "stop"


def _backplane_slot_severity(
    drive: PhysicalDriveSnapshot | None,
    *,
    settings: Settings,
) -> str:
    if drive is None:
        return "neutral"
    return compute_drive_tile_severity(
        drive,
        temp_warning=settings.temp_warning_celsius,
        temp_critical=settings.temp_critical_celsius,
    )


def _neighbor_drive_url(
    drives: list[PhysicalDriveSnapshot],
    current: PhysicalDriveSnapshot,
    *,
    offset: int,
) -> str | None:
    sorted_drives = sorted(drives, key=lambda drive: (drive.enclosure_id, drive.slot_id))
    current_index = next(
        (
            index
            for index, drive in enumerate(sorted_drives)
            if drive.enclosure_id == current.enclosure_id and drive.slot_id == current.slot_id
        ),
        None,
    )
    if current_index is None:
        return None
    neighbor_index = current_index + offset
    if neighbor_index < 0 or neighbor_index >= len(sorted_drives):
        return None
    neighbor = sorted_drives[neighbor_index]
    return f"/drives/{neighbor.enclosure_id}:{neighbor.slot_id}"


def _drive_state_severity(state: str) -> str:
    if state in _OPTIMAL_STATES:
        return "optimal"
    if state in _CRITICAL_STATES:
        return "critical"
    if state in _REBUILD_STATES:
        return "warning"
    severity = _event_severity_to_status(physical_drive_state_severity("Onln", state))
    return "warning" if severity in {"unknown", "optimal"} else severity


def _state_subtitle(drive: PhysicalDriveSnapshot, snapshot: ControllerSnapshot) -> str:
    if drive.disk_group_id is None:
        return "Unconfigured drive."
    virtual_drive = min(snapshot.virtual_drives, key=lambda item: item.vd_id, default=None)
    raid_text = "Unknown RAID" if virtual_drive is None else virtual_drive.raid_level
    vd_text = "unknown" if virtual_drive is None else str(virtual_drive.vd_id)
    return f"Member of VD {vd_text}. {raid_text}. DG {drive.disk_group_id}."


def _summary_text(drive: PhysicalDriveSnapshot) -> str:
    total_errors = drive.media_errors + drive.other_errors + drive.predictive_failures
    if drive.state in _OPTIMAL_STATES and total_errors == 0 and not drive.smart_alert:
        return "Drive functioning normally."
    parts: list[str] = []
    if drive.state not in _OPTIMAL_STATES:
        parts.append(f"Drive state is {drive.state}.")
    if drive.media_errors:
        parts.append(
            f"{drive.media_errors} historical media {_pluralize(drive.media_errors, 'error')}."
        )
    if drive.other_errors:
        parts.append(f"{drive.other_errors} other {_pluralize(drive.other_errors, 'error')}.")
    if drive.predictive_failures:
        parts.append(
            f"{drive.predictive_failures} predictive "
            f"{_pluralize(drive.predictive_failures, 'failure')}."
        )
    if drive.smart_alert:
        parts.append("S.M.A.R.T. alert is active.")
    return " ".join(parts)


def _dg_span_row_text(drive: PhysicalDriveSnapshot) -> str:
    dg_text = "-" if drive.disk_group_id is None else str(drive.disk_group_id)
    return f"{dg_text} : 0 : {drive.slot_id}"


def _port_text(drive: PhysicalDriveSnapshot) -> str:
    connector = _drive_extra_text(drive, "connector_text", "Port unknown")
    return f"{connector} (device {drive.device_id})"


def _manufacturer_text(interface: str) -> str:
    normalized = interface.upper()
    if normalized == "SATA":
        return "ATA (SATA)"
    if normalized == "SAS":
        return "SAS"
    return interface


def _interface_text(drive: PhysicalDriveSnapshot, device_speed: str) -> str:
    return drive.interface if device_speed == "Unknown" else f"{drive.interface} {device_speed}"


def _drive_extra_text(drive: PhysicalDriveSnapshot, name: str, default: str) -> str:
    value = getattr(drive, name, None)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _speed_value(text: str) -> float:
    if text == "Unknown":
        return 0.0
    numeric = "".join(character for character in text if character.isdigit() or character == ".")
    if not numeric:
        return 0.0
    try:
        return float(numeric)
    except ValueError:
        return 0.0


def _action_button(
    *,
    label: str,
    url: str,
    destructive: bool,
    enabled: bool,
    reason: str,
) -> AdvancedActionButton:
    return AdvancedActionButton(
        label=label,
        url=url,
        is_destructive=destructive,
        is_enabled=enabled,
        disabled_reason=None if enabled else reason,
    )


def _pluralize(count: int, singular: str) -> str:
    return singular if count == 1 else f"{singular}s"


def _today_utc() -> date:
    return datetime.now(UTC).date()


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)
