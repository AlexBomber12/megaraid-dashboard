"""Controller detail page view model."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import get_latest_snapshot
from megaraid_dashboard.db.models import (
    CacheVaultSnapshot,
    ControllerSnapshot,
    Event,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.drive_actions import (
    ConsistencyCheckStatus,
    PatrolReadStatus,
    consistency_check_can_start,
    consistency_check_can_stop,
    patrol_read_can_start,
    patrol_read_can_stop,
)
from megaraid_dashboard.services.event_detector import virtual_drive_state_severity
from megaraid_dashboard.services.overview import (
    SystemHealthViewModel,
    _load_system_health,
    derive_controller_health,
    temperature_severity,
)
from megaraid_dashboard.storcli import StorcliError, parse_foreign_config

_DEFAULT_AUTO_REFRESH_SECONDS = 30
_ROC_HISTORY_CHART_URL = "/controller/roc-temperature/history"
_BUZZER_EXPLAINER = (
    "The controller buzzer can be silenced for the current condition or disabled until it is "
    "enabled again."
)
_UNKNOWN_HARDWARE_TEXT = "N/A"
_VD_OPTIMAL_STATES = {"Optl", "Optimal"}
_REBUILD_STATES = {"Rbld", "Rebld", "Rebuild", "Rebuilding"}


@dataclass(frozen=True)
class ControllerHealthSnapshot:
    state: str
    summary_text: str
    roc_temperature_celsius: int | None
    cv_capacitance_percent: int | None
    bbu_status: str
    memory_ecc_errors_total: int
    errors_24h: int
    uptime_text: str


@dataclass(frozen=True)
class LiveOperationCard:
    name: str
    mode_text: str
    status_text: str
    progress_percent: int | None
    progress_eta_text: str | None
    can_start: bool
    can_stop: bool
    can_change_mode: bool
    start_url: str | None
    stop_url: str | None
    mode_url: str | None


@dataclass(frozen=True)
class CacheVaultDetail:
    model: str
    state: str
    capacitance_percent: int | None
    temperature_celsius: int | None
    mfg_date: date | None
    mfg_date_text: str
    fw_cache_size_text: str


@dataclass(frozen=True)
class RaidConfigRow:
    vd_id: int
    name: str
    raid_level: str
    size_text: str
    state: str
    state_severity: str
    access: str
    cache_policy: str
    strip_size_text: str


@dataclass(frozen=True)
class ScheduledTaskRow:
    name: str
    schedule_text: str
    is_enabled: bool
    configure_url: str | None


@dataclass(frozen=True)
class HardwareIdentity:
    model: str
    serial: str
    revision: str
    chip_revision: str
    manufactured_date_text: str
    rework_date_text: str
    firmware_version: str
    bios_version: str
    driver_version: str
    sas_address: str
    pci_address: str
    backend_ports: str
    nvram_size_text: str
    flash_size_text: str
    memory_size_text: str
    fw_cache_size_text: str
    pending_images_count: int
    alarm_buzzer_text: str


@dataclass(frozen=True)
class BuzzerControlState:
    current_setting: str
    can_silence: bool
    can_disable: bool
    can_enable: bool
    silence_url: str
    disable_url: str
    enable_url: str
    explainer_text: str


@dataclass(frozen=True)
class ForeignConfigState:
    present: bool
    drive_count: int
    source_controller_serial: str | None
    description_text: str
    can_import: bool
    can_clear: bool
    import_url: str
    clear_url: str


@dataclass(frozen=True)
class ControllerDetailViewModel:
    page_title: str
    page_subtitle: str
    health: ControllerHealthSnapshot
    live_operations: list[LiveOperationCard]
    cachevault: CacheVaultDetail | None
    roc_history_chart_url: str
    raid_config: list[RaidConfigRow]
    scheduled_tasks: list[ScheduledTaskRow]
    hardware: HardwareIdentity
    buzzer: BuzzerControlState
    foreign_config: ForeignConfigState
    system_health: SystemHealthViewModel
    updated_at: datetime
    auto_refresh_seconds: int


class _CollectorStateProvider(Protocol):
    def get_last_collector_run_at(self) -> datetime | None: ...


class _OperationState(Protocol):
    @property
    def mode(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def progress_percent(self) -> int | None: ...

    @property
    def last_run_timestamp(self) -> str | None: ...

    @property
    def is_running(self) -> bool: ...


def load_controller_detail_view_model(
    session: Session,
    *,
    settings: Settings,
    scheduler: _CollectorStateProvider | None,
    collector_enabled: bool,
    app_start_time: datetime,
    app_version: str,
) -> ControllerDetailViewModel:
    now = datetime.now(UTC)
    start_time = _require_aware_utc(app_start_time)
    snapshot = get_latest_snapshot(session)
    raw_json = {} if snapshot is None or snapshot.raw_json is None else snapshot.raw_json
    return ControllerDetailViewModel(
        page_title="Controller",
        page_subtitle=_page_subtitle(snapshot, now=now),
        health=_build_health_snapshot(
            session,
            snapshot=snapshot,
            settings=settings,
            app_start_time=start_time,
            now=now,
        ),
        live_operations=_build_live_operations(snapshot, now=now),
        cachevault=None
        if snapshot is None
        else _build_cachevault_detail(snapshot.cachevault, raw_json, now=now),
        roc_history_chart_url=_ROC_HISTORY_CHART_URL,
        raid_config=[] if snapshot is None else _build_raid_config(snapshot, raw_json),
        scheduled_tasks=_build_scheduled_tasks(raw_json, now=now),
        hardware=_build_hardware_identity(snapshot, raw_json),
        buzzer=_build_buzzer_control_state(None if snapshot is None else snapshot.alarm_state),
        foreign_config=_build_foreign_config_state(raw_json),
        system_health=_load_system_health(
            settings=settings,
            scheduler=scheduler,
            collector_enabled=collector_enabled,
            app_version=app_version,
            now=now,
        ),
        updated_at=now,
        auto_refresh_seconds=getattr(
            settings,
            "auto_refresh_seconds",
            _DEFAULT_AUTO_REFRESH_SECONDS,
        ),
    )


def _build_health_snapshot(
    session: Session,
    *,
    snapshot: ControllerSnapshot | None,
    settings: Settings,
    app_start_time: datetime,
    now: datetime,
) -> ControllerHealthSnapshot:
    if snapshot is None:
        return ControllerHealthSnapshot(
            state="UNKNOWN",
            summary_text="Waiting for first controller snapshot.",
            roc_temperature_celsius=None,
            cv_capacitance_percent=None,
            bbu_status="Unknown",
            memory_ecc_errors_total=0,
            errors_24h=_count_recent_warning_and_critical_events(session, now=now),
            uptime_text=_format_uptime(now - app_start_time),
        )

    health: str = derive_controller_health(
        snapshot,
        snapshot.physical_drives,
        snapshot.virtual_drives,
    )
    roc_severity = temperature_severity(
        snapshot.roc_temperature_celsius,
        temp_warning=settings.roc_temp_warning_celsius,
        temp_critical=settings.roc_temp_critical_celsius,
    )
    if roc_severity in {"warning", "critical"}:
        health = _worst_health(health, roc_severity)
    return ControllerHealthSnapshot(
        state=health.upper(),
        summary_text=_health_summary(snapshot, health=health),
        roc_temperature_celsius=snapshot.roc_temperature_celsius,
        cv_capacitance_percent=(
            None if snapshot.cachevault is None else snapshot.cachevault.capacitance_percent
        ),
        bbu_status=_bbu_status(snapshot),
        memory_ecc_errors_total=_memory_ecc_errors_total(snapshot.raw_json),
        errors_24h=_count_recent_warning_and_critical_events(session, now=now),
        uptime_text=_format_uptime(now - app_start_time),
    )


def _build_live_operations(
    snapshot: ControllerSnapshot | None,
    *,
    now: datetime,
) -> list[LiveOperationCard]:
    if snapshot is None:
        return []

    cards: list[tuple[int, LiveOperationCard]] = []
    rebuild_card = _build_rebuild_operation_card(snapshot)
    if rebuild_card is not None:
        cards.append((0, rebuild_card))
    consistency_check = _load_consistency_check_state(snapshot)
    if consistency_check is not None:
        cards.append((1, _consistency_check_card(consistency_check, now=now)))
    patrol_read = _load_patrol_read_state(snapshot)
    if patrol_read is not None:
        cards.append((2, _patrol_read_card(patrol_read, now=now)))
    return [card for _, card in sorted(cards, key=lambda item: item[0])]


def _build_cachevault_detail(
    cachevault: CacheVaultSnapshot | None,
    raw_json: Mapping[str, Any],
    *,
    now: datetime,
) -> CacheVaultDetail | None:
    if cachevault is None:
        return None
    mfg_date = _parse_date(_first_raw_text(raw_json, "MfgDate", "Date of Manufacture"))
    return CacheVaultDetail(
        model=cachevault.type,
        state=cachevault.state,
        capacitance_percent=cachevault.capacitance_percent,
        temperature_celsius=cachevault.temperature_celsius,
        mfg_date=mfg_date,
        mfg_date_text=_format_manufacture_date(mfg_date, now=now),
        fw_cache_size_text=_format_size_text(
            _first_raw_text(raw_json, "Current Size of FW Cache (MB)", "CacheVault Flash Size"),
            default_unit="MB",
        ),
    )


def _build_raid_config(
    snapshot: ControllerSnapshot,
    raw_json: Mapping[str, Any],
) -> list[RaidConfigRow]:
    return [
        RaidConfigRow(
            vd_id=virtual_drive.vd_id,
            name=virtual_drive.name or f"VD {virtual_drive.vd_id}",
            raid_level=virtual_drive.raid_level,
            size_text=_format_bytes(virtual_drive.size_bytes),
            state=virtual_drive.state,
            state_severity=_vd_state_severity(virtual_drive.state),
            access=virtual_drive.access,
            cache_policy=virtual_drive.cache,
            strip_size_text=_strip_size_text(raw_json, virtual_drive.vd_id),
        )
        for virtual_drive in sorted(snapshot.virtual_drives, key=lambda row: row.vd_id)
    ]


def _build_scheduled_tasks(raw_json: Mapping[str, Any], *, now: datetime) -> list[ScheduledTaskRow]:
    patrol_interval = _parse_interval_hours(_first_raw_text(raw_json, "Patrol Read Reoccurrence"))
    patrol_last_run = _parse_timestamp(_first_raw_text(raw_json, "Patrol Read Last Run"))
    patrol_next = _parse_timestamp(_first_raw_text(raw_json, "Next Patrol Read launch"))
    return [
        ScheduledTaskRow(
            name="Patrol Read",
            schedule_text=_schedule_text(patrol_interval, patrol_last_run, patrol_next, now=now),
            is_enabled=patrol_interval is not None,
            configure_url="/controller/patrol-read/mode",
        )
    ]


def _build_hardware_identity(
    snapshot: ControllerSnapshot | None,
    raw_json: Mapping[str, Any],
) -> HardwareIdentity:
    return HardwareIdentity(
        model=_snapshot_text(snapshot, "model_name"),
        serial=_snapshot_text(snapshot, "serial_number"),
        revision=_first_raw_text(raw_json, "Revision No") or _UNKNOWN_HARDWARE_TEXT,
        chip_revision=_first_raw_text(raw_json, "ChipRevision") or _UNKNOWN_HARDWARE_TEXT,
        manufactured_date_text=_first_raw_text(raw_json, "Mfg Date") or _UNKNOWN_HARDWARE_TEXT,
        rework_date_text=_first_raw_text(raw_json, "Rework Date") or _UNKNOWN_HARDWARE_TEXT,
        firmware_version=_snapshot_text(snapshot, "firmware_version"),
        bios_version=_snapshot_text(snapshot, "bios_version"),
        driver_version=_snapshot_text(snapshot, "driver_version"),
        sas_address=_first_raw_text(raw_json, "SAS Address") or _UNKNOWN_HARDWARE_TEXT,
        pci_address=_first_raw_text(raw_json, "PCI Address") or _UNKNOWN_HARDWARE_TEXT,
        backend_ports=_first_raw_text(raw_json, "Backend Port Count") or _UNKNOWN_HARDWARE_TEXT,
        nvram_size_text=_format_size_text(_first_raw_text(raw_json, "NVRAM Size")),
        flash_size_text=_format_size_text(_first_raw_text(raw_json, "Flash Size")),
        memory_size_text=_format_size_text(_first_raw_text(raw_json, "On Board Memory Size")),
        fw_cache_size_text=_format_size_text(
            _first_raw_text(raw_json, "Current Size of FW Cache (MB)"),
            default_unit="MB",
        ),
        pending_images_count=_pending_images_count(raw_json),
        alarm_buzzer_text=(
            _first_raw_text(raw_json, "Alarm") or _snapshot_text(snapshot, "alarm_state")
        ),
    )


def _build_buzzer_control_state(alarm_state: str | None) -> BuzzerControlState:
    current = _normalize_alarm_state(alarm_state)
    return BuzzerControlState(
        current_setting=current,
        can_silence=current == "On",
        can_disable=current in {"On", "Silenced"},
        can_enable=current == "Off",
        silence_url="/controller/buzzer/silence",
        disable_url="/controller/buzzer/disable",
        enable_url="/controller/buzzer/enable",
        explainer_text=_BUZZER_EXPLAINER,
    )


def _build_foreign_config_state(raw_json: Mapping[str, Any]) -> ForeignConfigState:
    foreign = raw_json.get("foreign_config")
    foreign_mapping = foreign if isinstance(foreign, Mapping) else {}
    parsed = None
    if "present" not in foreign_mapping and foreign_mapping:
        try:
            parsed = parse_foreign_config(dict(foreign_mapping))
        except StorcliError:
            parsed = None
    present = parsed.present if parsed is not None else _truthy(foreign_mapping.get("present"))
    drive_count = (
        parsed.drive_count
        if parsed is not None
        else _int_or_zero(foreign_mapping.get("drive_count"))
    )
    source_serial = _optional_text(
        foreign_mapping.get("source_controller_serial")
        or foreign_mapping.get("source_serial")
        or foreign_mapping.get("controller_serial")
    )
    description = (
        f"Foreign configuration detected on {drive_count} drive(s)."
        if present
        else "No foreign configuration detected."
    )
    return ForeignConfigState(
        present=present,
        drive_count=drive_count,
        source_controller_serial=source_serial,
        description_text=description,
        can_import=present,
        can_clear=present,
        import_url="/controller/foreign-config/import",
        clear_url="/controller/foreign-config/clear",
    )


def _page_subtitle(snapshot: ControllerSnapshot | None, *, now: datetime) -> str:
    serial = "Unknown" if snapshot is None else snapshot.serial_number
    return f"SN {serial}. Updated {now.strftime('%B %-d, %Y %H:%M %Z')}."


def _health_summary(snapshot: ControllerSnapshot, *, health: str) -> str:
    online = sum(1 for drive in snapshot.physical_drives if drive.state == "Onln")
    total = len(snapshot.physical_drives)
    raid = _dominant_raid_level(snapshot.virtual_drives)
    if health == "optimal":
        return f"All systems nominal. {online}/{total} drives online. {raid} healthy."
    return f"Controller requires attention. {online}/{total} drives online. {raid} state degraded."


def _dominant_raid_level(virtual_drives: Sequence[VirtualDriveSnapshot]) -> str:
    if not virtual_drives:
        return "RAID unknown"
    return sorted(virtual_drives, key=lambda virtual_drive: virtual_drive.vd_id)[0].raid_level


def _bbu_status(snapshot: ControllerSnapshot) -> str:
    if snapshot.cachevault is not None and snapshot.cv_present:
        return snapshot.cachevault.state
    if not snapshot.bbu_present:
        return "N/A"
    return "Unknown"


def _memory_ecc_errors_total(raw_json: Mapping[str, Any] | None) -> int:
    if raw_json is None:
        return 0
    return sum(
        _int_or_zero(_first_raw_text(raw_json, key))
        for key in ("Memory Correctable Errors", "Memory Uncorrectable Errors", "ECC Bucket Count")
    )


def _build_rebuild_operation_card(snapshot: ControllerSnapshot) -> LiveOperationCard | None:
    rebuilding = [drive for drive in snapshot.physical_drives if drive.state in _REBUILD_STATES]
    if not rebuilding:
        return None
    drive = sorted(rebuilding, key=lambda item: (item.enclosure_id, item.slot_id))[0]
    return LiveOperationCard(
        name="Rebuild",
        mode_text="Automatic rebuild.",
        status_text=f"Running on drive {drive.enclosure_id}:{drive.slot_id}.",
        progress_percent=None,
        progress_eta_text=None,
        can_start=False,
        can_stop=False,
        can_change_mode=False,
        start_url=None,
        stop_url=None,
        mode_url=None,
    )


def _patrol_read_card(status: PatrolReadStatus, *, now: datetime) -> LiveOperationCard:
    return LiveOperationCard(
        name="Patrol Read",
        mode_text=_mode_text(status.mode, interval_hours=168),
        status_text=_operation_status_text(status, now=now),
        progress_percent=status.progress_percent if status.is_running else None,
        progress_eta_text=None if not status.is_running else "ETA unknown",
        can_start=patrol_read_can_start(status),
        can_stop=patrol_read_can_stop(status),
        can_change_mode=True,
        start_url="/controller/patrol-read/start",
        stop_url="/controller/patrol-read/stop",
        mode_url="/controller/patrol-read/mode",
    )


def _consistency_check_card(status: ConsistencyCheckStatus, *, now: datetime) -> LiveOperationCard:
    return LiveOperationCard(
        name="Consistency Check",
        mode_text=_mode_text(status.mode, interval_hours=None),
        status_text=_operation_status_text(status, now=now),
        progress_percent=status.progress_percent if status.is_running else None,
        progress_eta_text=None if not status.is_running else "ETA unknown",
        can_start=consistency_check_can_start(status),
        can_stop=consistency_check_can_stop(status),
        can_change_mode=True,
        start_url="/controller/consistency-check/start",
        stop_url="/controller/consistency-check/stop",
        mode_url="/controller/consistency-check/mode",
    )


def _operation_status_text(status: _OperationState, *, now: datetime) -> str:
    label = status.state.title()
    completed_at = _parse_timestamp(status.last_run_timestamp)
    if status.is_running:
        progress = "" if status.progress_percent is None else f" {status.progress_percent}%."
        return f"{label}.{progress}".strip()
    if completed_at is None:
        return f"{label}. Last completion unknown."
    elapsed = _format_uptime(now - completed_at)
    return f"{label}. Last completed {completed_at.strftime('%b %-d')} ({elapsed} ago)."


def _mode_text(mode: str, *, interval_hours: int | None) -> str:
    normalized = mode.strip().lower() or "unknown"
    label = normalized.replace("_", " ").title()
    if interval_hours is None:
        return f"{label} mode."
    return f"{label} mode. Interval {interval_hours}h."


def _schedule_text(
    interval_hours: int | None,
    last_run_at: datetime | None,
    next_run_at: datetime | None,
    *,
    now: datetime,
) -> str:
    if interval_hours is None:
        return "Next: unknown."
    resolved_next = next_run_at
    if resolved_next is None and last_run_at is not None:
        resolved_next = last_run_at + timedelta(hours=interval_hours)
    days = interval_hours // 24
    prefix = f"Every {interval_hours}h"
    if days:
        prefix = f"{prefix} ({days} days)"
    if resolved_next is None:
        return f"{prefix}. Next: unknown."
    del now
    return f"{prefix}. Next {resolved_next.strftime('%b %-d, %H:%M')}."


def _strip_size_text(raw_json: Mapping[str, Any], vd_id: int) -> str:
    properties = _find_raw_value(raw_json, f"VD{vd_id} Properties")
    if isinstance(properties, Mapping):
        value = _first_raw_text(properties, "Strip Size")
        if value is not None:
            return _format_size_text(value, default_unit="KB")
    specific = _first_raw_text(raw_json, f"VD{vd_id} Strip Size")
    if specific is not None:
        return _format_size_text(specific, default_unit="KB")
    value = _optional_text(raw_json.get("Strip Size"))
    return _format_size_text(value, default_unit="KB")


def _vd_state_severity(state: str) -> str:
    if state in _VD_OPTIMAL_STATES:
        return "optimal"
    severity = virtual_drive_state_severity(state)
    if severity == "critical":
        return "critical"
    return "warning"


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


def _load_patrol_read_state(snapshot: ControllerSnapshot) -> PatrolReadStatus | None:
    del snapshot
    return None


def _load_consistency_check_state(snapshot: ControllerSnapshot) -> ConsistencyCheckStatus | None:
    del snapshot
    return None


def _worst_health(current: str, candidate: str) -> str:
    rank = {"optimal": 0, "warning": 1, "critical": 2, "unknown": 0}
    return candidate if rank[candidate] > rank[current] else current


def _format_uptime(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_bytes(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1000 or unit == "PB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.2f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")  # pragma: no cover


def _format_size_text(value: str | None, *, default_unit: str | None = None) -> str:
    text = _optional_text(value)
    if text is None:
        return _UNKNOWN_HARDWARE_TEXT
    if default_unit is not None and re.fullmatch(r"\d+(?:\.\d+)?", text):
        return f"{text} {default_unit}"
    return text


def _format_manufacture_date(value: date | None, *, now: datetime) -> str:
    if value is None:
        return _UNKNOWN_HARDWARE_TEXT
    had_anniversary = (now.date().month, now.date().day) >= (value.month, value.day)
    years = max(0, now.date().year - value.year - (0 if had_anniversary else 1))
    return f"{value.strftime('%B %-d, %Y')} (~{years} years old)"


def _first_raw_text(raw_json: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _find_raw_value(raw_json, key)
        text = _optional_text(value)
        if text is not None:
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


def _pending_images_count(raw_json: Mapping[str, Any]) -> int:
    value = _find_raw_value(raw_json, "Image name")
    if isinstance(value, list):
        return len(value)
    text = _optional_text(value)
    return 0 if text in {None, "-", "None", "N/A"} else 1


def _parse_interval_hours(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"(\d+)", value)
    if match is None:
        return None
    amount = int(match.group(1))
    lowered = value.lower()
    if "day" in lowered:
        return amount * 24
    return amount


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.split("(", maxsplit=1)[0].strip()
    for format_string in (
        "%m/%d/%Y, %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%b %d, %Y %H:%M",
    ):
        try:
            return datetime.strptime(text, format_string).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    text = value.strip()
    for format_string in ("%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, format_string).date()
        except ValueError:
            continue
    return None


def _snapshot_text(snapshot: ControllerSnapshot | None, attribute: str) -> str:
    if snapshot is None:
        return _UNKNOWN_HARDWARE_TEXT
    return str(getattr(snapshot, attribute) or _UNKNOWN_HARDWARE_TEXT)


def _normalize_alarm_state(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"on", "enabled", "enable"}:
        return "On"
    if normalized in {"silenced", "silence", "mute", "muted"}:
        return "Silenced"
    return "Off"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "present"}
    return False


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match is not None:
            return int(match.group(0))
    return 0


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
