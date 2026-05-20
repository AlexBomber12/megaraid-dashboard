"""Controller detail page view model."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
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
    _event_severity_to_status,
    _load_system_health,
    _main_page_bbu_status,
    _parse_operation_timestamp,
    _virtual_drive_state_label,
    derive_controller_health,
    format_tb,
)

_DEFAULT_AUTO_REFRESH_SECONDS = 30
_DEFAULT_PATROL_READ_INTERVAL_HOURS = 168
_ROC_HISTORY_CHART_URL = "/controller/roc-temperature/history"
_BUZZER_EXPLAINER_TEXT = (
    "The controller alarm can be silenced for the current condition or disabled until it is "
    "enabled again."
)
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


class _SchedulerJob(Protocol):
    next_run_time: datetime | None


class _Scheduler(Protocol):
    def get_job(self, job_id: str) -> _SchedulerJob | None: ...

    def get_last_collector_run_at(self) -> datetime | None: ...


def load_controller_detail_view_model(
    session: Session,
    *,
    settings: Settings,
    scheduler: _Scheduler | None,
    collector_enabled: bool,
    app_start_time: datetime,
    app_version: str,
) -> ControllerDetailViewModel:
    now = datetime.now(UTC)
    snapshot = get_latest_snapshot(session)
    raw_json = {} if snapshot is None or snapshot.raw_json is None else snapshot.raw_json
    hardware = _build_hardware_identity(snapshot, raw_json)
    return ControllerDetailViewModel(
        page_title="Controller",
        page_subtitle=_page_subtitle(snapshot, now),
        health=_build_health_snapshot(
            session,
            settings=settings,
            snapshot=snapshot,
            app_start_time=app_start_time,
            now=now,
        ),
        live_operations=_build_live_operations(snapshot),
        cachevault=(
            None if snapshot is None else _build_cachevault_detail(snapshot.cachevault, raw_json)
        ),
        roc_history_chart_url=_ROC_HISTORY_CHART_URL,
        raid_config=_build_raid_config(snapshot),
        scheduled_tasks=_build_scheduled_tasks(snapshot=snapshot, scheduler=scheduler, now=now),
        hardware=hardware,
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
    settings: Settings,
    snapshot: ControllerSnapshot | None,
    app_start_time: datetime,
    now: datetime,
) -> ControllerHealthSnapshot:
    del settings
    errors_24h = _count_recent_warning_and_critical_events(session, now=now)
    if snapshot is None:
        return ControllerHealthSnapshot(
            state="UNKNOWN",
            summary_text="Waiting for first controller snapshot.",
            roc_temperature_celsius=None,
            cv_capacitance_percent=None,
            bbu_status="Unknown",
            memory_ecc_errors_total=0,
            errors_24h=errors_24h,
            uptime_text=_format_uptime(now - _require_aware_utc(app_start_time)),
        )

    state = derive_controller_health(
        snapshot,
        snapshot.physical_drives,
        snapshot.virtual_drives,
    ).upper()
    return ControllerHealthSnapshot(
        state=state,
        summary_text=_health_summary(snapshot, state),
        roc_temperature_celsius=snapshot.roc_temperature_celsius,
        cv_capacitance_percent=(
            None if snapshot.cachevault is None else snapshot.cachevault.capacitance_percent
        ),
        bbu_status=_main_page_bbu_status(snapshot),
        memory_ecc_errors_total=_memory_ecc_errors_total(snapshot.raw_json),
        errors_24h=errors_24h,
        uptime_text=_format_uptime(now - _require_aware_utc(app_start_time)),
    )


def _health_summary(snapshot: ControllerSnapshot, state: str) -> str:
    online_count = sum(1 for drive in snapshot.physical_drives if drive.state == "Onln")
    drive_count = len(snapshot.physical_drives)
    raid_text = _raid_summary_text(snapshot.virtual_drives)
    if state == "OPTIMAL":
        prefix = "All systems nominal."
    elif state == "WARNING":
        prefix = "Controller needs attention."
    else:
        prefix = "Critical controller condition."
    return f"{prefix} {online_count}/{drive_count} drives online. {raid_text}."


def _raid_summary_text(virtual_drives: Sequence[VirtualDriveSnapshot]) -> str:
    if not virtual_drives:
        return "RAID status unknown"
    if len(virtual_drives) == 1:
        virtual_drive = virtual_drives[0]
        state = _virtual_drive_state_label(virtual_drive.state).lower()
        return f"{virtual_drive.raid_level} {state}"
    return f"{len(virtual_drives)} virtual drives configured"


def _build_live_operations(snapshot: ControllerSnapshot | None) -> list[LiveOperationCard]:
    cards: list[tuple[int, LiveOperationCard]] = [
        (0, _rebuild_operation_card(snapshot)),
        (1, _consistency_check_operation_card(_load_consistency_check_state(snapshot))),
        (2, _patrol_read_operation_card(_load_patrol_read_state(snapshot))),
    ]
    return [card for _, card in sorted(cards, key=lambda item: item[0])]


def _rebuild_operation_card(snapshot: ControllerSnapshot | None) -> LiveOperationCard:
    rebuilding_drive = None
    if snapshot is not None:
        rebuilding_drive = next(
            (drive for drive in snapshot.physical_drives if drive.state in _REBUILD_STATES),
            None,
        )
    if rebuilding_drive is None:
        return LiveOperationCard(
            name="Rebuild",
            mode_text="Automatic when a replacement drive is inserted.",
            status_text="Idle.",
            progress_percent=None,
            progress_eta_text=None,
            can_start=False,
            can_stop=False,
            can_change_mode=False,
            start_url=None,
            stop_url=None,
            mode_url=None,
        )
    return LiveOperationCard(
        name="Rebuild",
        mode_text="Controller-managed rebuild.",
        status_text=f"Running on drive {rebuilding_drive.enclosure_id}:{rebuilding_drive.slot_id}.",
        progress_percent=None,
        progress_eta_text="ETA unknown",
        can_start=False,
        can_stop=False,
        can_change_mode=False,
        start_url=None,
        stop_url=None,
        mode_url=None,
    )


def _consistency_check_operation_card(status: ConsistencyCheckStatus | None) -> LiveOperationCard:
    mode = "unknown" if status is None else status.mode
    return LiveOperationCard(
        name="Consistency check",
        mode_text=_mode_text(mode, interval_hours=None),
        status_text=_operation_status_text(status, idle_label="Idle."),
        progress_percent=(
            None if status is None or not status.is_running else status.progress_percent
        ),
        progress_eta_text="ETA unknown" if status is not None and status.is_running else None,
        can_start=False if status is None else consistency_check_can_start(status),
        can_stop=False if status is None else consistency_check_can_stop(status),
        can_change_mode=True,
        start_url="/controller/consistency-check/start",
        stop_url="/controller/consistency-check/stop",
        mode_url="/controller/consistency-check/mode",
    )


def _patrol_read_operation_card(status: PatrolReadStatus | None) -> LiveOperationCard:
    mode = "unknown" if status is None else status.mode
    return LiveOperationCard(
        name="Patrol read",
        mode_text=_mode_text(mode, interval_hours=_DEFAULT_PATROL_READ_INTERVAL_HOURS),
        status_text=_operation_status_text(status, idle_label="Idle."),
        progress_percent=(
            None if status is None or not status.is_running else status.progress_percent
        ),
        progress_eta_text="ETA unknown" if status is not None and status.is_running else None,
        can_start=False if status is None else patrol_read_can_start(status),
        can_stop=False if status is None else patrol_read_can_stop(status),
        can_change_mode=True,
        start_url="/controller/patrol-read/start",
        stop_url="/controller/patrol-read/stop",
        mode_url="/controller/patrol-read/mode",
    )


def _mode_text(mode: str, *, interval_hours: int | None) -> str:
    mode_label = {"auto": "Auto", "manual": "Manual", "disable": "Disabled"}.get(
        mode.lower(),
        "Unknown",
    )
    if interval_hours is None:
        return f"{mode_label} mode."
    return f"{mode_label} mode. Interval {interval_hours}h."


def _operation_status_text(
    status: ConsistencyCheckStatus | PatrolReadStatus | None,
    *,
    idle_label: str,
) -> str:
    if status is None:
        return "Status unknown."
    if status.is_running:
        progress = "" if status.progress_percent is None else f" {status.progress_percent}%."
        return f"Running.{progress}".strip()
    completed_at = _parse_operation_timestamp(status.last_run_timestamp)
    if completed_at is None:
        return idle_label
    return f"{idle_label} Last completed {completed_at.strftime('%b %-d, %Y %H:%M')} UTC."


def _build_cachevault_detail(
    cachevault: CacheVaultSnapshot | None,
    raw_json: Mapping[str, Any],
) -> CacheVaultDetail | None:
    if cachevault is None:
        return None
    mfg_date = _parse_date(_find_raw_value(raw_json, "Date of Manufacture", "MfgDate"))
    return CacheVaultDetail(
        model=_first_present(cachevault.type, _find_raw_value(raw_json, "Device Name")),
        state=cachevault.state,
        capacitance_percent=cachevault.capacitance_percent,
        temperature_celsius=cachevault.temperature_celsius,
        mfg_date=mfg_date,
        mfg_date_text=_format_mfg_date_text(mfg_date, datetime.now(UTC).date()),
        fw_cache_size_text=_format_mb_from_raw(
            _find_raw_value(raw_json, "Current Size of FW Cache (MB)")
        ),
    )


def _build_raid_config(snapshot: ControllerSnapshot | None) -> list[RaidConfigRow]:
    if snapshot is None:
        return []
    raw_json = {} if snapshot.raw_json is None else snapshot.raw_json
    return [
        RaidConfigRow(
            vd_id=virtual_drive.vd_id,
            name=virtual_drive.name or f"VD {virtual_drive.vd_id}",
            raid_level=virtual_drive.raid_level,
            size_text=format_tb(virtual_drive.size_bytes),
            state=virtual_drive.state,
            state_severity=_event_severity_to_status(
                virtual_drive_state_severity(virtual_drive.state)
            ),
            access=virtual_drive.access,
            cache_policy=virtual_drive.cache,
            strip_size_text=_first_present(
                _find_raw_value(raw_json, "Strip Size"),
                "N/A",
            ),
        )
        for virtual_drive in sorted(snapshot.virtual_drives, key=lambda item: item.vd_id)
    ]


def _build_scheduled_tasks(
    *,
    snapshot: ControllerSnapshot | None,
    scheduler: _Scheduler | None,
    now: datetime,
) -> list[ScheduledTaskRow]:
    patrol_status = _load_patrol_read_state(snapshot)
    is_enabled = patrol_status is None or patrol_status.mode.lower() != "disable"
    last_run_at = (
        None
        if patrol_status is None
        else _parse_operation_timestamp(patrol_status.last_run_timestamp)
    )
    next_run_at = _next_scheduler_run(scheduler, "patrol_read")
    if next_run_at is None and last_run_at is not None:
        next_run_at = last_run_at + timedelta(hours=_DEFAULT_PATROL_READ_INTERVAL_HOURS)
    return [
        ScheduledTaskRow(
            name="Patrol Read",
            schedule_text=_schedule_text(
                interval_hours=_DEFAULT_PATROL_READ_INTERVAL_HOURS,
                next_run_at=next_run_at,
                now=now,
            ),
            is_enabled=is_enabled,
            configure_url="/controller/patrol-read/mode",
        )
    ]


def _schedule_text(
    *,
    interval_hours: int,
    next_run_at: datetime | None,
    now: datetime,
) -> str:
    days = interval_hours // 24
    prefix = f"Every {interval_hours}h ({days} days)."
    if next_run_at is None:
        return f"{prefix} Next: unknown."
    del now
    return f"{prefix} Next {next_run_at.strftime('%b %-d, %H:%M')}."


def _next_scheduler_run(scheduler: _Scheduler | None, job_id: str) -> datetime | None:
    if scheduler is None:
        return None
    job = scheduler.get_job(job_id)
    if job is None or job.next_run_time is None:
        return None
    return _require_aware_utc(job.next_run_time)


def _build_hardware_identity(
    snapshot: ControllerSnapshot | None,
    raw_json: Mapping[str, Any],
) -> HardwareIdentity:
    return HardwareIdentity(
        model="N/A" if snapshot is None else snapshot.model_name,
        serial="N/A" if snapshot is None else snapshot.serial_number,
        revision=_first_present(_find_raw_value(raw_json, "Revision No"), "N/A"),
        chip_revision=_first_present(_find_raw_value(raw_json, "ChipRevision"), "N/A"),
        manufactured_date_text=_format_optional_date_text(_find_raw_value(raw_json, "Mfg Date")),
        rework_date_text=_format_optional_date_text(_find_raw_value(raw_json, "Rework Date")),
        firmware_version="N/A" if snapshot is None else snapshot.firmware_version,
        bios_version="N/A" if snapshot is None else snapshot.bios_version,
        driver_version="N/A" if snapshot is None else snapshot.driver_version,
        sas_address=_first_present(_find_raw_value(raw_json, "SAS Address"), "N/A"),
        pci_address=_first_present(_find_raw_value(raw_json, "PCI Address"), "N/A"),
        backend_ports=_first_present(_find_raw_value(raw_json, "Backend Port Count"), "N/A"),
        nvram_size_text=_first_present(_find_raw_value(raw_json, "NVRAM Size"), "N/A"),
        flash_size_text=_first_present(_find_raw_value(raw_json, "Flash Size"), "N/A"),
        memory_size_text=_first_present(_find_raw_value(raw_json, "On Board Memory Size"), "N/A"),
        fw_cache_size_text=_format_mb_from_raw(
            _find_raw_value(raw_json, "Current Size of FW Cache (MB)")
        ),
        pending_images_count=_pending_images_count(raw_json),
        alarm_buzzer_text=_first_present(
            None if snapshot is None else snapshot.alarm_state,
            _find_raw_value(raw_json, "Alarm"),
            "N/A",
        ),
    )


def _build_buzzer_control_state(alarm_state: str | None) -> BuzzerControlState:
    setting = _normalize_alarm_state(alarm_state)
    return BuzzerControlState(
        current_setting=setting,
        can_silence=setting == "On",
        can_disable=setting in {"On", "Silenced"},
        can_enable=setting == "Off",
        silence_url="/controller/buzzer/silence",
        disable_url="/controller/buzzer/disable",
        enable_url="/controller/buzzer/enable",
        explainer_text=_BUZZER_EXPLAINER_TEXT,
    )


def _build_foreign_config_state(raw_json: Mapping[str, Any]) -> ForeignConfigState:
    response_data = _first_response_data(raw_json)
    drive_count = _parse_int(_find_raw_value(response_data, "Total foreign drive Count"))
    if drive_count is None:
        foreign_drives = _find_raw_value(response_data, "FOREIGN PD LIST")
        drive_count = len(foreign_drives) if isinstance(foreign_drives, list) else 0
    source_serial = _string_or_none(
        _find_raw_value(response_data, "Source Controller Serial", "Controller Serial Number")
    )
    present = drive_count > 0
    description = (
        f"{drive_count} foreign drive{'s' if drive_count != 1 else ''} detected."
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
        import_url="/foreign-config/import",
        clear_url="/foreign-config/clear",
    )


def _page_subtitle(snapshot: ControllerSnapshot | None, now: datetime) -> str:
    serial = "Unknown" if snapshot is None else snapshot.serial_number
    return f"SN {serial}. Updated {now.strftime('%b %-d, %Y %H:%M UTC')}."


def _count_recent_warning_and_critical_events(session: Session, *, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count(Event.id)).where(
                Event.severity.in_(("warning", "critical")),
                Event.occurred_at > _require_aware_utc(now) - timedelta(hours=24),
            )
        )
        or 0
    )


def _memory_ecc_errors_total(raw_json: Mapping[str, Any] | None) -> int:
    if raw_json is None:
        return 0
    values = (
        _parse_int(_find_raw_value(raw_json, "Memory Correctable Errors")),
        _parse_int(_find_raw_value(raw_json, "Memory Uncorrectable Errors")),
        _parse_int(_find_raw_value(raw_json, "ECC Bucket Count")),
    )
    return sum(value for value in values if value is not None)


def _format_uptime(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m {seconds % 60}s"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def _format_mfg_date_text(value: date | None, today: date) -> str:
    if value is None:
        return "N/A"
    years_old = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
    return f"{value.strftime('%B %-d, %Y')} (~{years_old} years old)"


def _format_optional_date_text(value: Any) -> str:
    parsed = _parse_date(value)
    if parsed is None:
        return "N/A"
    return parsed.strftime("%B %-d, %Y")


def _format_mb_from_raw(value: Any) -> str:
    parsed = _parse_int(value)
    if parsed is None:
        text = _string_or_none(value)
        return "N/A" if text is None or text.upper() in {"NA", "N/A"} else text
    return f"{parsed} MB"


def _parse_date(value: Any) -> date | None:
    text = _string_or_none(value)
    if text is None or text in {"00/00/00", "00/00/0000", "N/A", "NA"}:
        return None
    for format_string in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, format_string).date()
        except ValueError:
            continue
    return None


def _pending_images_count(raw_json: Mapping[str, Any]) -> int:
    value = _find_raw_value(raw_json, "Pending Images in Flash")
    if isinstance(value, Mapping):
        image_name = _string_or_none(value.get("Image name"))
        if image_name is None or image_name.lower() == "no pending images":
            return 0
        return 1
    if isinstance(value, Sequence) and not isinstance(value, str):
        return len(value)
    parsed = _parse_int(value)
    return 0 if parsed is None else parsed


def _normalize_alarm_state(value: str | None) -> str:
    if value is None:
        return "Off"
    normalized = value.strip().lower()
    if normalized in {"on", "enabled", "enable"}:
        return "On"
    if normalized in {"silenced", "silence", "snoozed"}:
        return "Silenced"
    return "Off"


def _find_raw_value(value: Any, *keys: str) -> Any:
    if not keys:
        return None
    for key, candidate in _walk_raw_values(value):
        if key in keys:
            return candidate
    return None


def _walk_raw_values(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        if "Property" in value and "Value" in value:
            yield str(value["Property"]), value["Value"]
        for key, candidate in value.items():
            yield str(key), candidate
            yield from _walk_raw_values(candidate)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_raw_values(item)


def _first_response_data(raw_json: Mapping[str, Any]) -> Mapping[str, Any]:
    controllers = raw_json.get("Controllers")
    if not isinstance(controllers, list) or not controllers:
        return {}
    controller = controllers[0]
    if not isinstance(controller, Mapping):
        return {}
    response_data = controller.get("Response Data")
    return response_data if isinstance(response_data, Mapping) else {}


def _first_present(*values: Any) -> str:
    for value in values:
        text = _string_or_none(value)
        if text is not None:
            return text
    return "N/A"


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _string_or_none(value)
    if text is None:
        return None
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        return None
    return int(digits)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _load_patrol_read_state(snapshot: ControllerSnapshot | None) -> PatrolReadStatus | None:
    del snapshot
    return None


def _load_consistency_check_state(
    snapshot: ControllerSnapshot | None,
) -> ConsistencyCheckStatus | None:
    del snapshot
    return None
