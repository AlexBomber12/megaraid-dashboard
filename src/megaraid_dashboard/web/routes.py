from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import Executor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol, cast, runtime_checkable
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from megaraid_dashboard import __version__
from megaraid_dashboard.config import Settings, get_settings
from megaraid_dashboard.db.dao import (
    get_latest_snapshot,
    get_maintenance_state,
    record_event,
    set_maintenance_state,
)
from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
)
from megaraid_dashboard.services.audit import record_operator_action
from megaraid_dashboard.services.drive_actions import (
    LocateAction,
    ReplaceStep,
    build_foreign_config_clear_command,
    build_foreign_config_import_command,
    build_foreign_config_show_command,
    build_insert_replacement_command,
    build_locate_command,
    build_rebuild_status_command,
    build_set_missing_command,
    build_set_offline_command,
    build_show_drive_command,
    can_transition,
    can_transition_step3,
    parse_rebuild_status,
    validate_enclosure_slot,
)
from megaraid_dashboard.services.drive_history import (
    DriveErrorSeries,
    DriveHistoryPointKey,
    DriveReplacementMarker,
    DriveTemperatureSeries,
    load_drive_error_series,
    load_drive_temperature_series,
)
from megaraid_dashboard.services.event_detector import physical_drive_state_severity
from megaraid_dashboard.services.events import (
    EVENTS_PAGE_SIZE,
    EventsFragmentViewModel,
    EventsPageViewModel,
    load_events_fragment,
    load_events_page,
)
from megaraid_dashboard.services.overview import (
    DriveListViewModel,
    OverviewViewModel,
    format_tb,
    load_drive_list_view_model,
    load_overview_view_model,
    temperature_severity,
)
from megaraid_dashboard.storcli import (
    DriveShow,
    ForeignConfig,
    StorcliError,
    StorcliParseError,
    ensure_command_succeeded,
    parse_drive_show,
    parse_foreign_config,
    run_storcli,
)
from megaraid_dashboard.web.templates import create_templates

LOGGER = structlog.get_logger(__name__)
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = create_templates(_PACKAGE_ROOT / "templates")
STATIC_ASSET_VERSION = ""
_DEFAULT_CHART_RANGE_DAYS = 7
_ALLOWED_CHART_RANGE_DAYS = (7, 30, 365)
_EVENT_SEVERITY_FILTERS = ("info", "warning", "critical")
_EVENT_CATEGORY_FILTERS = (
    "controller",
    "pd_state",
    "vd_state",
    "cv_state",
    "smart_alert",
    "media_errors",
    "other_errors",
    "predictive_failures",
    "temperature",
    "controller_temperature",
    "disk_space",
    "system",
    "operator_action",
    "foreign_config_detected",
)

HealthStatus = Literal["ok", "degraded"]
DatabaseHealth = Literal["ok", "error"]
CollectorHealth = Literal["ok", "idle", "lock_held"]

router = APIRouter()


@dataclass(frozen=True)
class DriveAttribute:
    label: str
    value: str
    mono: bool = False
    severity: str | None = None


@dataclass(frozen=True)
class RangeTab:
    label: str
    range_days: int
    active: bool
    hx_get: str


@dataclass(frozen=True)
class TemperatureFallbackRow:
    timestamp: str
    average_celsius: str
    minimum_celsius: str
    maximum_celsius: str


@dataclass(frozen=True)
class ErrorFallbackRow:
    timestamp: str
    media_errors: str
    other_errors: str
    predictive_failures: str


@dataclass(frozen=True)
class FilterChip:
    label: str
    value: str
    active: bool
    href: str


@dataclass(frozen=True)
class DriveChartsViewModel:
    enclosure_id: int
    slot_id: int
    active_range_days: int
    temperature_chart: dict[str, Any]
    error_chart: dict[str, Any]
    temperature_rows: tuple[TemperatureFallbackRow, ...]
    error_rows: tuple[ErrorFallbackRow, ...]
    raw_point_count: int
    hourly_point_count: int
    daily_point_count: int


@dataclass(frozen=True)
class DriveDetailViewModel:
    enclosure_id: int
    slot_id: int
    title: str
    model: str
    serial_number: str
    captured_at: datetime
    captured_at_iso: str
    attributes: tuple[DriveAttribute, ...]
    range_tabs: tuple[RangeTab, ...]
    charts: DriveChartsViewModel


@dataclass(frozen=True)
class _ChartPointKey:
    timestamp: datetime
    serial_number: str
    history_point_key: DriveHistoryPointKey


@runtime_checkable
class _TaskLike(Protocol):
    def done(self) -> bool: ...


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


class MaintenanceStartRequest(BaseModel):
    duration_minutes: int = Field(ge=1, le=1440)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        reason = value.strip()
        if not reason:
            msg = "reason must not be blank"
            raise ValueError(msg)
        return reason


@router.post("/maintenance/start")
async def maintenance_start(body: MaintenanceStartRequest, request: Request) -> JSONResponse:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=body.duration_minutes)
    username = str(request.scope.get("user_username", "unknown"))
    with _session(request) as session, session.begin():
        set_maintenance_state(
            session,
            active=True,
            expires_at=expires_at,
            started_by=username,
        )
        record_operator_action(
            session,
            username=username,
            message=(
                f"maintenance start duration {body.duration_minutes} min reason: {body.reason}"
            ),
            occurred_at=now,
        )
    return JSONResponse(
        {
            "active": True,
            "expires_at": expires_at.isoformat(),
            "started_by": username,
        }
    )


@router.post("/maintenance/stop")
async def maintenance_stop(request: Request) -> JSONResponse:
    now = datetime.now(UTC)
    username = str(request.scope.get("user_username", "unknown"))
    with _session(request) as session, session.begin():
        prior = get_maintenance_state(session, now=now)
        set_maintenance_state(session, active=False, expires_at=None, started_by=None)
        if prior.active:
            record_operator_action(
                session,
                username=username,
                message="maintenance stop",
                occurred_at=now,
            )
    return JSONResponse({"active": False})


@router.get("/maintenance")
async def maintenance_get(request: Request) -> JSONResponse:
    now = datetime.now(UTC)
    with _session(request) as session:
        state = get_maintenance_state(session, now=now)
    remaining_seconds = None
    if state.active and state.expires_at is not None:
        remaining = int((state.expires_at - now).total_seconds())
        if remaining > 0:
            remaining_seconds = remaining
    return JSONResponse(
        {
            "active": state.active,
            "expires_at": state.expires_at.isoformat() if state.expires_at else None,
            "started_by": state.started_by,
            "remaining_seconds": remaining_seconds,
        }
    )


@router.get("/healthz", name="healthz")
async def healthz(request: Request) -> JSONResponse:
    database_status = await _database_health_for_request(request)
    collector_status = _collector_health(request)
    status: HealthStatus = "ok" if database_status == "ok" else "degraded"
    response = JSONResponse(
        status_code=200 if status == "ok" else 503,
        content={
            "status": status,
            "database": database_status,
            "collector": collector_status,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/", name="overview")
def overview(request: Request) -> Response:
    started_at = perf_counter()
    view_model = _load_overview(request)
    response = TEMPLATES.TemplateResponse(
        request=request,
        name="pages/overview.html",
        context={
            "active_nav": "overview",
            "current_utc_label": _current_utc_label(),
            "static_asset_version": _static_asset_version(),
            "view_model": view_model,
        },
    )
    _log_overview_rendered(view_model=view_model, elapsed_ms=_elapsed_ms(started_at), partial=False)
    return response


@router.get("/partials/overview", name="overview_partial")
def overview_partial(request: Request) -> Response:
    started_at = perf_counter()
    view_model = _load_overview(request)
    response = TEMPLATES.TemplateResponse(
        request=request,
        name="partials/overview_data.html",
        context={"view_model": view_model},
    )
    _log_overview_rendered(view_model=view_model, elapsed_ms=_elapsed_ms(started_at), partial=True)
    return response


@router.get("/drives", name="drives")
def drives(request: Request) -> Response:
    view_model = _load_drive_list(request)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="pages/drives.html",
        context={
            "active_nav": "drives",
            "current_utc_label": _current_utc_label(),
            "static_asset_version": _static_asset_version(),
            "view_model": view_model,
        },
    )


@router.get("/drives/{enclosure_id}/{slot_id}", name="drive_detail")
def drive_detail(request: Request, enclosure_id: int, slot_id: int) -> Response:
    started_at = perf_counter()
    with _session(request) as session:
        snapshot, drive = _latest_drive_or_404(
            session,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
        )
        view_model = _drive_detail_view_model(
            request=request,
            session=session,
            snapshot=snapshot,
            drive=drive,
            range_days=_DEFAULT_CHART_RANGE_DAYS,
        )
    _log_drive_detail_rendered(
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        range_days=view_model.charts.active_range_days,
        raw_point_count=view_model.charts.raw_point_count,
        hourly_point_count=view_model.charts.hourly_point_count,
        daily_point_count=view_model.charts.daily_point_count,
        elapsed_ms=_elapsed_ms(started_at),
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="pages/drive_detail.html",
        context={
            "active_nav": "drives",
            "current_utc_label": _current_utc_label(),
            "static_asset_version": _static_asset_version(),
            "view_model": view_model,
        },
    )


@router.get("/drives/{slot_ref}", name="drive_detail_slot_ref")
def drive_detail_slot_ref(request: Request, slot_ref: str) -> Response:
    enclosure_text, separator, slot_text = slot_ref.partition(":")
    if separator == "":
        raise HTTPException(status_code=404)
    try:
        enclosure_id = int(enclosure_text)
        slot_id = int(slot_text)
    except ValueError as exc:
        raise HTTPException(status_code=404) from exc
    return drive_detail(request, enclosure_id=enclosure_id, slot_id=slot_id)


@router.post("/drives/{enclosure}:{slot}/locate/start", name="drive_locate_start")
async def drive_locate_start(enclosure: str, slot: str, request: Request) -> JSONResponse:
    """Start the locate LED for one physical drive URL-addressed as enclosure:slot."""
    return await _run_locate(enclosure, slot, "start", request)


@router.post("/drives/{enclosure}:{slot}/locate/stop", name="drive_locate_stop")
async def drive_locate_stop(enclosure: str, slot: str, request: Request) -> JSONResponse:
    """Stop the locate LED for one physical drive URL-addressed as enclosure:slot."""
    return await _run_locate(enclosure, slot, "stop", request)


async def _run_locate(
    enclosure: str,
    slot: str,
    action: LocateAction,
    request: Request,
) -> JSONResponse:
    try:
        enclosure_id = int(enclosure)
        slot_id = int(slot)
        argv = build_locate_command(enclosure_id, slot_id, action)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    settings: Settings = request.app.state.settings
    result = await run_storcli(
        argv,
        use_sudo=settings.storcli_use_sudo,
        binary_path=settings.storcli_path,
    )
    await run_in_threadpool(
        _record_locate_operator_action_sync,
        request=request,
        action=action,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
    )
    return JSONResponse(
        {
            "action": action,
            "enclosure": enclosure_id,
            "slot": slot_id,
            "result": result,
        }
    )


def _record_locate_operator_action_sync(
    *,
    request: Request,
    action: LocateAction,
    enclosure_id: int,
    slot_id: int,
) -> None:
    try:
        with _session(request) as session, session.begin():
            record_operator_action(
                session,
                username=str(request.scope.get("user_username", "unknown")),
                message=f"locate {action} drive {enclosure_id}:{slot_id}",
            )
    except SQLAlchemyError:
        LOGGER.exception(
            "operator_action_audit_failed",
            action=action,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
        )


class ReplaceRequest(BaseModel):
    serial_number: str
    dry_run: bool = False


@router.post("/drives/{enclosure}:{slot}/replace/offline", name="drive_replace_offline")
async def drive_replace_offline(enclosure: str, slot: str, request: Request) -> JSONResponse:
    """Mark a physical drive offline as Step 1a of the replace procedure."""
    return await _run_replace_step(enclosure, slot, "offline", request)


@router.post("/drives/{enclosure}:{slot}/replace/missing", name="drive_replace_missing")
async def drive_replace_missing(enclosure: str, slot: str, request: Request) -> JSONResponse:
    """Mark a physical drive missing as Step 1b of the replace procedure."""
    return await _run_replace_step(enclosure, slot, "missing", request)


async def _run_replace_step(
    enclosure: str,
    slot: str,
    step: ReplaceStep,
    request: Request,
) -> JSONResponse:
    try:
        enclosure_id = int(enclosure)
        slot_id = int(slot)
    except ValueError:
        return JSONResponse({"error": "enclosure and slot must be integers"}, status_code=400)

    try:
        validate_enclosure_slot(enclosure_id, slot_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    query_dry_run = _parse_query_dry_run(request)
    if isinstance(query_dry_run, JSONResponse):
        return query_dry_run

    body = await _parse_replace_request_body(request)
    if isinstance(body, JSONResponse):
        return body
    dry_run = body.dry_run or query_dry_run

    drive = await run_in_threadpool(
        _load_latest_drive_for_slot,
        request=request,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
    )
    if drive is None:
        return JSONResponse(
            {"error": "no snapshot for slot", "enclosure": enclosure_id, "slot": slot_id},
            status_code=404,
        )
    if drive.serial_number != body.serial_number:
        # Do not echo the canonical serial: returning it would let an
        # operator probe with a placeholder value, read back the true serial,
        # and replay the destructive call with that value.
        return JSONResponse(
            {"error": "serial mismatch"},
            status_code=409,
        )

    try:
        if step == "offline":
            argv = build_set_offline_command(enclosure_id, slot_id)
        else:
            argv = build_set_missing_command(enclosure_id, slot_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if dry_run:
        # Dry-run gates on the persisted snapshot to avoid an extra storcli call.
        if not can_transition(drive.state, step):
            return JSONResponse(
                {
                    "error": f"cannot {step} drive currently in state {drive.state}",
                    "state": drive.state,
                    "step": step,
                },
                status_code=409,
            )
        return JSONResponse(
            {
                "dry_run": True,
                "step": step,
                "enclosure": enclosure_id,
                "slot": slot_id,
                "serial_number": body.serial_number,
                "argv": argv,
            }
        )

    settings: Settings = request.app.state.settings
    if not settings.maintenance_mode or not settings.destructive_mode:
        return JSONResponse(
            {
                "error": "destructive operations require maintenance_mode and destructive_mode",
                "maintenance_mode": settings.maintenance_mode,
                "destructive_mode": settings.destructive_mode,
            },
            status_code=403,
        )

    # Re-confirm live drive identity and state before any destructive command:
    # the persisted snapshot can lag, so the typed serial confirmation must be
    # validated against the disk currently in the slot, not the snapshot.
    try:
        live = await _query_live_drive_show(
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            settings=settings,
        )
    except StorcliError as exc:
        return JSONResponse(
            {
                "error": "storcli precheck failed",
                "step": step,
                "enclosure": enclosure_id,
                "slot": slot_id,
                "serial_number": body.serial_number,
                "detail": str(exc),
            },
            status_code=502,
        )
    if live.serial_number != body.serial_number:
        # Same reasoning as the snapshot mismatch above: do not return the
        # live drive's serial in the response.
        return JSONResponse(
            {"error": "live serial mismatch"},
            status_code=409,
        )
    if not can_transition(live.state, step):
        return JSONResponse(
            {
                "error": f"cannot {step} drive currently in state {live.state}",
                "state": live.state,
                "step": step,
            },
            status_code=409,
        )

    result: dict[str, Any] | None = None
    storcli_error: StorcliError | None = None
    try:
        result = await run_storcli(
            argv,
            use_sudo=settings.storcli_use_sudo,
            binary_path=settings.storcli_path,
        )
        outcome = "succeeded"
    except StorcliError as exc:
        storcli_error = exc
        outcome = f"failed: {type(exc).__name__}: {_truncate_audit_detail(str(exc))}"

    try:
        await run_in_threadpool(
            _record_replace_operator_action_sync,
            request=request,
            step=step,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            serial_number=body.serial_number,
            outcome=outcome,
        )
    except SQLAlchemyError:
        audit_failure_body: dict[str, Any] = {
            "error": "audit persistence failed",
            "step": step,
            "enclosure": enclosure_id,
            "slot": slot_id,
            "serial_number": body.serial_number,
            "argv": argv,
        }
        if result is not None:
            audit_failure_body["result"] = result
        if storcli_error is not None:
            audit_failure_body["storcli_error"] = str(storcli_error)
        return JSONResponse(audit_failure_body, status_code=500)

    if storcli_error is not None:
        return JSONResponse(
            {
                "error": "storcli command failed",
                "step": step,
                "enclosure": enclosure_id,
                "slot": slot_id,
                "serial_number": body.serial_number,
                "argv": argv,
                "detail": str(storcli_error),
            },
            status_code=502,
        )

    return JSONResponse(
        {
            "step": step,
            "enclosure": enclosure_id,
            "slot": slot_id,
            "serial_number": body.serial_number,
            "argv": argv,
            "result": result,
        }
    )


_DRY_RUN_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_DRY_RUN_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _parse_query_dry_run(request: Request) -> bool | JSONResponse:
    raw = request.query_params.get("dry_run")
    if raw is None:
        return False
    # Empty values (e.g. "?dry_run=" or "?dry_run") are ambiguous for a
    # destructive safety flag; fail closed by rejecting them rather than
    # silently treating them as False.
    normalized = raw.strip().lower()
    if normalized in _DRY_RUN_TRUE_VALUES:
        return True
    if normalized in _DRY_RUN_FALSE_VALUES:
        return False
    return JSONResponse(
        {"error": "dry_run query parameter must be a boolean"},
        status_code=400,
    )


async def _parse_replace_request_body(request: Request) -> ReplaceRequest | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
    try:
        return ReplaceRequest.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid request body", "detail": exc.errors()},
            status_code=400,
        )


async def _query_live_drive_show(
    *,
    enclosure_id: int,
    slot_id: int,
    settings: Settings,
) -> DriveShow:
    argv = build_show_drive_command(enclosure_id, slot_id)
    payload = await run_storcli(
        argv,
        use_sudo=settings.storcli_use_sudo,
        binary_path=settings.storcli_path,
    )
    return parse_drive_show(payload)


def _load_latest_drive_for_slot(
    *,
    request: Request,
    enclosure_id: int,
    slot_id: int,
) -> PhysicalDriveSnapshot | None:
    with _session(request) as session:
        return session.scalars(
            select(PhysicalDriveSnapshot)
            .join(ControllerSnapshot, PhysicalDriveSnapshot.snapshot_id == ControllerSnapshot.id)
            .where(PhysicalDriveSnapshot.enclosure_id == enclosure_id)
            .where(PhysicalDriveSnapshot.slot_id == slot_id)
            .order_by(ControllerSnapshot.captured_at.desc(), PhysicalDriveSnapshot.id.desc())
            .limit(1)
        ).one_or_none()


_AUDIT_DETAIL_MAX_LEN = 200


def _truncate_audit_detail(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _AUDIT_DETAIL_MAX_LEN:
        return collapsed
    return collapsed[: _AUDIT_DETAIL_MAX_LEN - 1] + "…"


def _record_replace_operator_action_sync(
    *,
    request: Request,
    step: ReplaceStep,
    enclosure_id: int,
    slot_id: int,
    serial_number: str,
    outcome: str,
) -> None:
    try:
        with _session(request) as session, session.begin():
            record_operator_action(
                session,
                username=str(request.scope.get("user_username", "unknown")),
                message=(
                    f"replace step {step} drive {enclosure_id}:{slot_id} "
                    f"serial {serial_number} {outcome}"
                ),
            )
    except SQLAlchemyError:
        LOGGER.exception(
            "operator_action_audit_failed",
            action=f"replace_{step}",
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            outcome=outcome,
        )
        raise


class InsertRequest(BaseModel):
    serial_number: str
    dry_run: bool = False


@dataclass(frozen=True)
class _SlotTopology:
    dg: int
    array: int
    row: int


@router.get("/drives/{enclosure}:{slot}/replace/topology", name="drive_replace_topology")
async def drive_replace_topology(enclosure: str, slot: str, request: Request) -> JSONResponse:
    """Server-derived DG / array / row for the replacement insert step.

    The values come from the latest persisted snapshot so the operator never
    types them. The hand-verification log on real hardware is the final check
    that the derivation matches actual storcli topology output.
    """
    try:
        enclosure_id = int(enclosure)
        slot_id = int(slot)
    except ValueError:
        return JSONResponse({"error": "enclosure and slot must be integers"}, status_code=400)

    try:
        validate_enclosure_slot(enclosure_id, slot_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    topology = await run_in_threadpool(
        _compute_slot_topology,
        request=request,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
    )
    if topology is None:
        return JSONResponse(
            {"error": "no snapshot for slot", "enclosure": enclosure_id, "slot": slot_id},
            status_code=404,
        )
    return JSONResponse(
        {
            "enclosure": enclosure_id,
            "slot": slot_id,
            "dg": topology.dg,
            "array": topology.array,
            "row": topology.row,
        }
    )


@router.get("/drives/{enclosure}:{slot}/replace/rebuild-status", name="drive_rebuild_status")
async def drive_rebuild_status(enclosure: str, slot: str, request: Request) -> Response:
    try:
        enclosure_id = int(enclosure)
        slot_id = int(slot)
        argv = build_rebuild_status_command(enclosure_id, slot_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    settings: Settings = request.app.state.settings
    try:
        payload = await run_storcli(
            argv,
            use_sudo=settings.storcli_use_sudo,
            binary_path=settings.storcli_path,
        )
        status = parse_rebuild_status(payload)
    except StorcliParseError as exc:
        if _is_htmx_request(request):
            return _rebuild_status_error_partial(
                request=request,
                error="storcli parse failed",
                detail=str(exc),
            )
        return JSONResponse({"error": "storcli parse failed", "detail": str(exc)}, status_code=502)
    except StorcliError as exc:
        if _is_htmx_request(request):
            return _rebuild_status_error_partial(
                request=request,
                error="storcli command failed",
                detail=str(exc),
            )
        return JSONResponse(
            {"error": "storcli command failed", "detail": str(exc)},
            status_code=502,
        )

    if 0 <= status.percent_complete < 100 and status.state == "In progress":
        await run_in_threadpool(
            _record_rebuild_progress_observed_once_sync,
            request=request,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            percent_complete=status.percent_complete,
            state=status.state,
        )
    if status.percent_complete >= 100 or status.state == "Complete":
        await run_in_threadpool(
            _record_rebuild_complete_once_sync,
            request=request,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            require_replacement_cycle=False,
        )
    elif status.state == "Not in progress":
        await run_in_threadpool(
            _record_rebuild_complete_once_sync,
            request=request,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            require_replacement_cycle=True,
        )

    if _accepts_html(request):
        return TEMPLATES.TemplateResponse(
            request=request,
            name="partials/rebuild_progress.html",
            context={"status": status},
        )

    return JSONResponse(
        {
            "enclosure": enclosure_id,
            "slot": slot_id,
            "percent_complete": status.percent_complete,
            "state": status.state,
            "time_remaining_minutes": status.time_remaining_minutes,
        }
    )


def _rebuild_status_error_partial(*, request: Request, error: str, detail: str) -> Response:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="partials/rebuild_progress_error.html",
        context={"error": error, "detail": detail},
        status_code=200,
    )


@router.post("/drives/{enclosure}:{slot}/replace/insert", name="drive_replace_insert")
async def drive_replace_insert(enclosure: str, slot: str, request: Request) -> JSONResponse:
    """Step 3: insert the replacement drive into the missing slot, kicking off rebuild."""
    try:
        enclosure_id = int(enclosure)
        slot_id = int(slot)
    except ValueError:
        return JSONResponse({"error": "enclosure and slot must be integers"}, status_code=400)

    try:
        validate_enclosure_slot(enclosure_id, slot_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    query_dry_run = _parse_query_dry_run(request)
    if isinstance(query_dry_run, JSONResponse):
        return query_dry_run

    body = await _parse_insert_request_body(request)
    if isinstance(body, JSONResponse):
        return body
    dry_run = body.dry_run or query_dry_run

    drive = await run_in_threadpool(
        _load_latest_drive_for_slot,
        request=request,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
    )
    if drive is None:
        return JSONResponse(
            {"error": "no snapshot for slot", "enclosure": enclosure_id, "slot": slot_id},
            status_code=404,
        )

    last_audit = await run_in_threadpool(
        _load_last_operator_action_for_slot,
        request=request,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
    )
    last_audit_message = last_audit.summary if last_audit is not None else None
    if not can_transition_step3(last_audit_message):
        return JSONResponse(
            {
                "error": "must complete replace step missing before insert",
                "last_audit": last_audit_message,
            },
            status_code=409,
        )
    outgoing_serial = _extract_serial_from_audit(last_audit_message or "")
    if outgoing_serial is not None and outgoing_serial == body.serial_number:
        return JSONResponse(
            {
                "error": (
                    "supplied serial matches the OUTGOING drive; "
                    "expected the new replacement drive's serial"
                )
            },
            status_code=409,
        )
    # The snapshot is allowed to lag the physical swap: after the operator
    # pulls the failed drive and seats the replacement, the next collector
    # poll may not have happened yet, so the persisted serial can still be
    # the outgoing drive's. Accept either the typed replacement serial
    # (snapshot already refreshed) or the outgoing serial (snapshot still
    # pre-swap); the live storcli precheck below is the authoritative
    # identity check before any destructive command. Reject only when the
    # snapshot serial matches neither — that signals an unrelated drive in
    # the slot or a mistyped value.
    if drive.serial_number != body.serial_number and (
        outgoing_serial is None or drive.serial_number != outgoing_serial
    ):
        # Same reasoning as Step 1: do not echo the canonical serial back.
        return JSONResponse(
            {"error": "serial mismatch (replacement drive)"},
            status_code=409,
        )

    topology = await run_in_threadpool(
        _compute_slot_topology,
        request=request,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
    )
    if topology is None:
        return JSONResponse(
            {
                "error": "unable to derive insert topology for slot",
                "enclosure": enclosure_id,
                "slot": slot_id,
            },
            status_code=409,
        )

    try:
        argv = build_insert_replacement_command(
            enclosure_id, slot_id, topology.dg, topology.array, topology.row
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if dry_run:
        return JSONResponse(
            {
                "dry_run": True,
                "step": "insert",
                "enclosure": enclosure_id,
                "slot": slot_id,
                "serial_number": body.serial_number,
                "dg": topology.dg,
                "array": topology.array,
                "row": topology.row,
                "argv": argv,
            }
        )

    settings: Settings = request.app.state.settings
    if not settings.maintenance_mode or not settings.destructive_mode:
        return JSONResponse(
            {
                "error": "destructive operations require maintenance_mode and destructive_mode",
                "maintenance_mode": settings.maintenance_mode,
                "destructive_mode": settings.destructive_mode,
            },
            status_code=403,
        )

    # Re-confirm live drive identity before the destructive insert: the
    # persisted snapshot can lag, so the typed replacement-serial confirmation
    # must be validated against the disk currently in the slot, not the
    # snapshot. Without this, a slot that swapped again after the last poll
    # could pass snapshot/topology checks and have ``insert`` execute against
    # a different live device.
    try:
        live = await _query_live_drive_show(
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            settings=settings,
        )
    except StorcliError as exc:
        return JSONResponse(
            {
                "error": "storcli precheck failed",
                "step": "insert",
                "enclosure": enclosure_id,
                "slot": slot_id,
                "serial_number": body.serial_number,
                "detail": str(exc),
            },
            status_code=502,
        )
    if live.serial_number != body.serial_number:
        # Same reasoning as the snapshot mismatch above: do not return the
        # live drive's serial in the response.
        return JSONResponse(
            {"error": "live serial mismatch (replacement drive)"},
            status_code=409,
        )

    result: dict[str, Any] | None = None
    storcli_error: StorcliError | None = None
    try:
        result = await run_storcli(
            argv,
            use_sudo=settings.storcli_use_sudo,
            binary_path=settings.storcli_path,
        )
        outcome = "succeeded"
    except StorcliError as exc:
        storcli_error = exc
        outcome = f"failed: {type(exc).__name__}: {_truncate_audit_detail(str(exc))}"

    try:
        await run_in_threadpool(
            _record_insert_operator_action_sync,
            request=request,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            serial_number=body.serial_number,
            dg=topology.dg,
            array=topology.array,
            row=topology.row,
            outcome=outcome,
        )
    except SQLAlchemyError:
        audit_failure_body: dict[str, Any] = {
            "error": "audit persistence failed",
            "step": "insert",
            "enclosure": enclosure_id,
            "slot": slot_id,
            "serial_number": body.serial_number,
            "argv": argv,
        }
        if result is not None:
            audit_failure_body["result"] = result
        if storcli_error is not None:
            audit_failure_body["storcli_error"] = str(storcli_error)
        return JSONResponse(audit_failure_body, status_code=500)

    if storcli_error is not None:
        return JSONResponse(
            {
                "error": "storcli command failed",
                "step": "insert",
                "enclosure": enclosure_id,
                "slot": slot_id,
                "serial_number": body.serial_number,
                "argv": argv,
                "detail": str(storcli_error),
            },
            status_code=502,
        )

    return JSONResponse(
        {
            "step": "insert",
            "enclosure": enclosure_id,
            "slot": slot_id,
            "serial_number": body.serial_number,
            "dg": topology.dg,
            "array": topology.array,
            "row": topology.row,
            "argv": argv,
            "result": result,
        }
    )


async def _parse_insert_request_body(request: Request) -> InsertRequest | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
    try:
        return InsertRequest.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid request body", "detail": exc.errors()},
            status_code=400,
        )


def _load_last_operator_action_for_slot(
    *,
    request: Request,
    enclosure_id: int,
    slot_id: int,
) -> Event | None:
    # Use word-boundary matching so e.g. ``drive 2:1`` does not match
    # ``drive 2:10``. Operator-action summaries always render the slot as
    # ``drive {enc}:{slot}`` followed by either whitespace (further fields)
    # or end-of-string (e.g. ``locate start drive 2:0``).
    slot_token = f"drive {enclosure_id}:{slot_id}"
    with _session(request) as session:
        return session.scalars(
            select(Event)
            .where(Event.category == "operator_action")
            .where(
                or_(
                    Event.summary.like(f"%{slot_token} %"),
                    Event.summary.like(f"%{slot_token}"),
                )
            )
            .order_by(Event.occurred_at.desc(), Event.id.desc())
            .limit(1)
        ).one_or_none()


def _load_replacement_cycle_marker_for_slot(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
) -> Event | None:
    slot_token = f"drive {enclosure_id}:{slot_id}"
    successful_rebuild_start = (
        Event.summary.like(f"replace step insert {slot_token} %")
        & Event.summary.like("% succeeded")
        & ~Event.summary.like("% failed%")
    )
    return session.scalars(
        select(Event)
        .where(Event.category == "operator_action")
        .where(successful_rebuild_start)
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(1)
    ).one_or_none()


def _load_rebuild_complete_operator_action_for_slot(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    cycle_marker: Event | None,
) -> Event | None:
    query = (
        select(Event)
        .where(Event.category == "operator_action")
        .where(Event.summary == f"rebuild complete drive {enclosure_id}:{slot_id}")
    )
    if cycle_marker is not None:
        query = query.where(
            or_(
                Event.occurred_at > cycle_marker.occurred_at,
                (Event.occurred_at == cycle_marker.occurred_at) & (Event.id > cycle_marker.id),
            )
        )
    return session.scalars(
        query.order_by(Event.occurred_at.desc(), Event.id.desc()).limit(1)
    ).one_or_none()


def _load_rebuild_progress_marker_for_slot(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    cycle_marker: Event | None,
) -> Event | None:
    query = (
        select(Event)
        .where(Event.category == "system")
        .where(Event.summary == f"rebuild progress observed drive {enclosure_id}:{slot_id}")
    )
    if cycle_marker is not None:
        query = query.where(
            or_(
                Event.occurred_at > cycle_marker.occurred_at,
                (Event.occurred_at == cycle_marker.occurred_at) & (Event.id > cycle_marker.id),
            )
        )
    return session.scalars(
        query.order_by(Event.occurred_at.desc(), Event.id.desc()).limit(1)
    ).one_or_none()


def _extract_serial_from_audit(message: str) -> str | None:
    tokens = message.split()
    for index, token in enumerate(tokens):
        if token == "serial" and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def _record_rebuild_progress_observed_once_sync(
    *,
    request: Request,
    enclosure_id: int,
    slot_id: int,
    percent_complete: int,
    state: str,
) -> None:
    try:
        with _session(request) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                session.begin()
            if session.get_bind().dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                    {"lock_key": f"rebuild-progress:{enclosure_id}:{slot_id}"},
                )

            cycle_marker = _load_replacement_cycle_marker_for_slot(
                session,
                enclosure_id=enclosure_id,
                slot_id=slot_id,
            )
            observed = _load_rebuild_progress_marker_for_slot(
                session,
                enclosure_id=enclosure_id,
                slot_id=slot_id,
                cycle_marker=cycle_marker,
            )
            if observed is not None:
                session.rollback()
                return
            record_event(
                session,
                severity="info",
                category="system",
                subject="Controller",
                summary=f"rebuild progress observed drive {enclosure_id}:{slot_id}",
                after={"percent_complete": percent_complete, "state": state},
            )
            session.commit()
    except SQLAlchemyError:
        LOGGER.exception(
            "rebuild_progress_marker_failed",
            enclosure_id=enclosure_id,
            slot_id=slot_id,
        )
        raise


def _record_rebuild_complete_once_sync(
    *,
    request: Request,
    enclosure_id: int,
    slot_id: int,
    require_replacement_cycle: bool = False,
) -> None:
    try:
        with _session(request) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                session.begin()
            if session.get_bind().dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                    {"lock_key": f"rebuild-complete:{enclosure_id}:{slot_id}"},
                )

            cycle_marker = _load_replacement_cycle_marker_for_slot(
                session,
                enclosure_id=enclosure_id,
                slot_id=slot_id,
            )
            if require_replacement_cycle and cycle_marker is None:
                session.rollback()
                return
            if require_replacement_cycle:
                progress_marker = _load_rebuild_progress_marker_for_slot(
                    session,
                    enclosure_id=enclosure_id,
                    slot_id=slot_id,
                    cycle_marker=cycle_marker,
                )
                if progress_marker is None:
                    session.rollback()
                    return
            completed = _load_rebuild_complete_operator_action_for_slot(
                session,
                enclosure_id=enclosure_id,
                slot_id=slot_id,
                cycle_marker=cycle_marker,
            )
            if completed is not None:
                session.rollback()
                return
            record_operator_action(
                session,
                username=str(request.scope.get("user_username", "unknown")),
                message=f"rebuild complete drive {enclosure_id}:{slot_id}",
            )
            session.commit()
    except SQLAlchemyError:
        LOGGER.exception(
            "operator_action_audit_failed",
            action="rebuild_complete",
            enclosure_id=enclosure_id,
            slot_id=slot_id,
        )
        raise


def _accepts_html(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    if "text/html" not in accept:
        return False
    return "application/json" not in accept


_ARRAY_MEMBER_STATES: frozenset[str] = frozenset(
    {"Onln", "Offln", "Failed", "Rbld", "Cpybck", "Msng", "Missing"}
)


def _compute_slot_topology(
    *,
    request: Request,
    enclosure_id: int,
    slot_id: int,
) -> _SlotTopology | None:
    """Derive ``dg`` / ``array`` / ``row`` for a slot from snapshot DG membership.

    ``dg`` is the target slot's last-known disk group, looked up in the most
    recent snapshot where this slot had a non-null ``disk_group_id`` (after a
    physical swap the current snapshot may show a fresh ``UGood`` drive with
    no DG, so we walk back to find the configured DG the slot belonged to).

    ``row`` is the target slot's position among peer slots in the SAME DG
    whose state marks them as actual array members (``Onln`` / ``Offln`` /
    ``Failed`` / ``Rbld`` / ``Cpybck`` / ``Msng``), sorted by ``(enclosure,
    slot)``. Hot spares (``DHS`` / ``GHS``) are associated to a DG but do not
    occupy an array row, so they must not shift the index. The target slot is
    always included so a UGood replacement drive still occupies the failed
    member's row.

    ``array`` defaults to ``0`` (single-span arrays) — the production
    deployment uses single-span RAID, and hand-verification on real hardware
    against ``storcli /c0/eX/sY show all J`` is the final check.
    """
    with _session(request) as session:
        latest_snapshot_id = session.scalar(
            select(ControllerSnapshot.id).order_by(ControllerSnapshot.captured_at.desc()).limit(1)
        )
        if latest_snapshot_id is None:
            return None
        physical_slots = session.scalars(
            select(PhysicalDriveSnapshot).where(
                PhysicalDriveSnapshot.snapshot_id == latest_snapshot_id
            )
        ).all()
        if not physical_slots:
            return None
        target = (enclosure_id, slot_id)
        if target not in {(pd.enclosure_id, pd.slot_id) for pd in physical_slots}:
            return None
        target_dg = session.scalar(
            select(PhysicalDriveSnapshot.disk_group_id)
            .join(ControllerSnapshot, PhysicalDriveSnapshot.snapshot_id == ControllerSnapshot.id)
            .where(PhysicalDriveSnapshot.enclosure_id == enclosure_id)
            .where(PhysicalDriveSnapshot.slot_id == slot_id)
            .where(PhysicalDriveSnapshot.disk_group_id.is_not(None))
            .order_by(ControllerSnapshot.captured_at.desc(), PhysicalDriveSnapshot.id.desc())
            .limit(1)
        )
        if target_dg is None:
            # No history of DG membership for this slot. Fall back to the
            # current snapshot's DGs only when there is EXACTLY ONE — the
            # single-DG production case. With multiple DGs present we have
            # no safe way to pick, so refuse to derive rather than guess a
            # destructive ``dg=`` argument.
            distinct_dgs = {
                pd.disk_group_id for pd in physical_slots if pd.disk_group_id is not None
            }
            if len(distinct_dgs) != 1:
                return None
            target_dg = next(iter(distinct_dgs))
        member_keys = {
            (pd.enclosure_id, pd.slot_id)
            for pd in physical_slots
            if pd.disk_group_id == target_dg and pd.state in _ARRAY_MEMBER_STATES
        }
        member_keys.add(target)
        ordered = sorted(member_keys)
        row = ordered.index(target)
        return _SlotTopology(dg=int(target_dg), array=0, row=row)


def _record_insert_operator_action_sync(
    *,
    request: Request,
    enclosure_id: int,
    slot_id: int,
    serial_number: str,
    dg: int,
    array: int,
    row: int,
    outcome: str,
) -> None:
    try:
        with _session(request) as session, session.begin():
            record_operator_action(
                session,
                username=str(request.scope.get("user_username", "unknown")),
                message=(
                    f"replace step insert drive {enclosure_id}:{slot_id} "
                    f"serial {serial_number} dg={dg} array={array} row={row} {outcome}"
                ),
            )
    except SQLAlchemyError:
        LOGGER.exception(
            "operator_action_audit_failed",
            action="replace_insert",
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            outcome=outcome,
        )
        raise


_FOREIGN_CONFIG_CLEAR_CONFIRMATION = "CLEAR FOREIGN CONFIG"
_REBUILD_DRIVE_STATES: frozenset[str] = frozenset({"Rbld"})


class ForeignConfigImportRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)
    dry_run: bool = False

    @field_validator("confirmation")
    @classmethod
    def confirmation_must_not_be_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            msg = "confirmation must not be blank"
            raise ValueError(msg)
        return text


class ForeignConfigClearRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)
    dry_run: bool = False


@router.get("/controller/foreign-config", name="controller_foreign_config")
async def controller_foreign_config(request: Request) -> Response:
    """Read the current foreign-config state directly from storcli.

    Returns HTML when ``Accept: text/html`` is preferred, JSON otherwise.
    """
    settings: Settings = request.app.state.settings
    foreign_config: ForeignConfig | None = None
    error: str | None = None
    detail: str | None = None
    status_code = 200
    try:
        foreign_config = await _query_live_foreign_config(settings=settings)
    except StorcliParseError as exc:
        error = "storcli parse failed"
        detail = str(exc)
        status_code = 502
    except StorcliError as exc:
        error = "storcli command failed"
        detail = str(exc)
        status_code = 502

    if _accepts_html(request):
        context: dict[str, Any] = {
            "active_nav": "overview",
            "current_utc_label": _current_utc_label(),
            "static_asset_version": _static_asset_version(),
            "foreign_config": (
                _foreign_config_response_body(foreign_config)
                if foreign_config is not None
                else None
            ),
            "error": error,
            "detail": detail,
            "clear_confirmation_phrase": _FOREIGN_CONFIG_CLEAR_CONFIRMATION,
        }
        return TEMPLATES.TemplateResponse(
            request=request,
            name="pages/foreign_config.html",
            context=context,
            status_code=status_code if error is not None else 200,
        )

    if error is not None:
        body: dict[str, Any] = {"error": error}
        if detail is not None:
            body["detail"] = detail
        return JSONResponse(body, status_code=status_code)
    assert foreign_config is not None
    return JSONResponse(_foreign_config_response_body(foreign_config))


@router.post(
    "/controller/foreign-config/import",
    name="controller_foreign_config_import",
)
async def controller_foreign_config_import(request: Request) -> JSONResponse:
    """Import the foreign configuration on the controller. HIGH RISK."""
    body = await _parse_foreign_config_import_body(request)
    if isinstance(body, JSONResponse):
        return body
    query_dry_run = _parse_query_dry_run(request)
    if isinstance(query_dry_run, JSONResponse):
        return query_dry_run
    dry_run = body.dry_run or query_dry_run

    settings: Settings = request.app.state.settings
    try:
        live_foreign_config = await _query_live_foreign_config(settings=settings)
    except StorcliParseError as exc:
        return await _reject_foreign_config_destructive(
            request=request,
            action="import",
            digest="",
            reason=f"storcli parse failed: {_truncate_audit_detail(str(exc))}",
            rejection_body={"error": "storcli parse failed", "detail": str(exc)},
            status_code=502,
        )
    except StorcliError as exc:
        return await _reject_foreign_config_destructive(
            request=request,
            action="import",
            digest="",
            reason=f"storcli command failed: {_truncate_audit_detail(str(exc))}",
            rejection_body={"error": "storcli command failed", "detail": str(exc)},
            status_code=502,
        )

    if not live_foreign_config.present:
        return await _reject_foreign_config_destructive(
            request=request,
            action="import",
            digest=live_foreign_config.digest,
            reason="no foreign configuration present",
            rejection_body={"error": "no foreign configuration present"},
            status_code=409,
        )

    if body.confirmation != live_foreign_config.digest:
        # Do not echo the canonical digest: returning it would let an
        # operator probe with a placeholder, read back the digest, and
        # replay the destructive call. The operator must obtain the
        # digest from GET /controller/foreign-config.
        return await _reject_foreign_config_destructive(
            request=request,
            action="import",
            digest=live_foreign_config.digest,
            reason="confirmation mismatch",
            rejection_body={"error": "confirmation mismatch"},
            status_code=409,
        )

    rebuild_state = await run_in_threadpool(
        _any_drive_rebuilding,
        request=request,
    )
    if rebuild_state is None:
        return await _reject_foreign_config_destructive(
            request=request,
            action="import",
            digest=live_foreign_config.digest,
            reason="rebuild state unknown",
            rejection_body={
                "error": (
                    "cannot import foreign config while rebuild state is unknown "
                    "(no controller snapshot available)"
                ),
            },
            status_code=409,
        )
    if rebuild_state:
        return await _reject_foreign_config_destructive(
            request=request,
            action="import",
            digest=live_foreign_config.digest,
            reason="rebuild in progress",
            rejection_body={
                "error": "cannot import foreign config while a rebuild is in progress",
            },
            status_code=409,
        )

    argv = build_foreign_config_import_command()

    if dry_run:
        return JSONResponse(
            {
                "dry_run": True,
                "action": "import",
                "argv": argv,
                "foreign_config": _foreign_config_response_body(live_foreign_config),
            }
        )

    if not settings.maintenance_mode or not settings.destructive_mode:
        return await _reject_foreign_config_destructive(
            request=request,
            action="import",
            digest=live_foreign_config.digest,
            reason="maintenance_mode and destructive_mode required",
            rejection_body={
                "error": "destructive operations require maintenance_mode and destructive_mode",
                "maintenance_mode": settings.maintenance_mode,
                "destructive_mode": settings.destructive_mode,
            },
            status_code=403,
        )

    return await _run_foreign_config_destructive(
        request=request,
        action="import",
        argv=argv,
        digest=live_foreign_config.digest,
        settings=settings,
    )


@router.post(
    "/controller/foreign-config/clear",
    name="controller_foreign_config_clear",
)
async def controller_foreign_config_clear(request: Request) -> JSONResponse:
    """Delete the foreign configuration on the controller. HIGH RISK."""
    body = await _parse_foreign_config_clear_body(request)
    if isinstance(body, JSONResponse):
        return body
    query_dry_run = _parse_query_dry_run(request)
    if isinstance(query_dry_run, JSONResponse):
        return query_dry_run
    dry_run = body.dry_run or query_dry_run

    if body.confirmation != _FOREIGN_CONFIG_CLEAR_CONFIRMATION:
        # No probe yet, so there is no digest to record.
        return await _reject_foreign_config_destructive(
            request=request,
            action="clear",
            digest="",
            reason="confirmation phrase mismatch",
            rejection_body={
                "error": f"confirmation must be exactly '{_FOREIGN_CONFIG_CLEAR_CONFIRMATION}'",
            },
            status_code=409,
        )

    settings: Settings = request.app.state.settings
    try:
        live_foreign_config = await _query_live_foreign_config(settings=settings)
    except StorcliParseError as exc:
        return await _reject_foreign_config_destructive(
            request=request,
            action="clear",
            digest="",
            reason=f"storcli parse failed: {_truncate_audit_detail(str(exc))}",
            rejection_body={"error": "storcli parse failed", "detail": str(exc)},
            status_code=502,
        )
    except StorcliError as exc:
        return await _reject_foreign_config_destructive(
            request=request,
            action="clear",
            digest="",
            reason=f"storcli command failed: {_truncate_audit_detail(str(exc))}",
            rejection_body={"error": "storcli command failed", "detail": str(exc)},
            status_code=502,
        )

    if not live_foreign_config.present:
        return await _reject_foreign_config_destructive(
            request=request,
            action="clear",
            digest=live_foreign_config.digest,
            reason="no foreign configuration present",
            rejection_body={"error": "no foreign configuration present"},
            status_code=409,
        )

    rebuild_state = await run_in_threadpool(
        _any_drive_rebuilding,
        request=request,
    )
    if rebuild_state is None:
        return await _reject_foreign_config_destructive(
            request=request,
            action="clear",
            digest=live_foreign_config.digest,
            reason="rebuild state unknown",
            rejection_body={
                "error": (
                    "cannot clear foreign config while rebuild state is unknown "
                    "(no controller snapshot available)"
                ),
            },
            status_code=409,
        )
    if rebuild_state:
        return await _reject_foreign_config_destructive(
            request=request,
            action="clear",
            digest=live_foreign_config.digest,
            reason="rebuild in progress",
            rejection_body={
                "error": "cannot clear foreign config while a rebuild is in progress",
            },
            status_code=409,
        )

    argv = build_foreign_config_clear_command()

    if dry_run:
        return JSONResponse(
            {
                "dry_run": True,
                "action": "clear",
                "argv": argv,
                "foreign_config": _foreign_config_response_body(live_foreign_config),
            }
        )

    if not settings.maintenance_mode or not settings.destructive_mode:
        return await _reject_foreign_config_destructive(
            request=request,
            action="clear",
            digest=live_foreign_config.digest,
            reason="maintenance_mode and destructive_mode required",
            rejection_body={
                "error": "destructive operations require maintenance_mode and destructive_mode",
                "maintenance_mode": settings.maintenance_mode,
                "destructive_mode": settings.destructive_mode,
            },
            status_code=403,
        )

    return await _run_foreign_config_destructive(
        request=request,
        action="clear",
        argv=argv,
        digest=live_foreign_config.digest,
        settings=settings,
    )


async def _reject_foreign_config_destructive(
    *,
    request: Request,
    action: Literal["import", "clear"],
    digest: str,
    reason: str,
    rejection_body: dict[str, Any],
    status_code: int,
) -> JSONResponse:
    """Audit a rejected destructive request and return the rejection response.

    The security model in AGENTS.md requires every destructive attempt to
    leave an audit trail, including ones rejected before storcli runs. If
    the audit write fails we surface a 500 instead of the original
    rejection so the missing forensic record is observable rather than
    silently dropped.
    """
    outcome = f"rejected: {reason}"
    try:
        await run_in_threadpool(
            _record_foreign_config_operator_action_sync,
            request=request,
            action=action,
            digest=digest,
            outcome=outcome,
        )
    except SQLAlchemyError:
        return JSONResponse(
            {
                "error": "audit persistence failed",
                "action": action,
                "rejection_reason": reason,
            },
            status_code=500,
        )
    return JSONResponse(rejection_body, status_code=status_code)


async def _run_foreign_config_destructive(
    *,
    request: Request,
    action: Literal["import", "clear"],
    argv: list[str],
    digest: str,
    settings: Settings,
) -> JSONResponse:
    result: dict[str, Any] | None = None
    storcli_error: StorcliError | None = None
    try:
        result = await run_storcli(
            argv,
            use_sudo=settings.storcli_use_sudo,
            binary_path=settings.storcli_path,
        )
        ensure_command_succeeded(result)
        outcome = "succeeded"
    except StorcliError as exc:
        storcli_error = exc
        outcome = f"failed: {type(exc).__name__}: {_truncate_audit_detail(str(exc))}"

    try:
        await run_in_threadpool(
            _record_foreign_config_operator_action_sync,
            request=request,
            action=action,
            digest=digest,
            outcome=outcome,
        )
    except SQLAlchemyError:
        audit_failure_body: dict[str, Any] = {
            "error": "audit persistence failed",
            "action": action,
            "argv": argv,
        }
        if result is not None:
            audit_failure_body["result"] = result
        if storcli_error is not None:
            audit_failure_body["storcli_error"] = str(storcli_error)
        return JSONResponse(audit_failure_body, status_code=500)

    if storcli_error is not None:
        return JSONResponse(
            {
                "error": "storcli command failed",
                "action": action,
                "argv": argv,
                "detail": str(storcli_error),
            },
            status_code=502,
        )

    return JSONResponse(
        {
            "action": action,
            "argv": argv,
            "result": result,
        }
    )


def _foreign_config_response_body(foreign_config: ForeignConfig) -> dict[str, Any]:
    return {
        "present": foreign_config.present,
        "dg_count": foreign_config.dg_count,
        "drive_count": foreign_config.drive_count,
        "total_size_bytes": foreign_config.total_size_bytes,
        "digest": foreign_config.digest,
        "disk_groups": [
            {
                "dg_id": dg.dg_id,
                "drive_count": dg.drive_count,
                "size_bytes": dg.size_bytes,
            }
            for dg in foreign_config.disk_groups
        ],
    }


async def _query_live_foreign_config(*, settings: Settings) -> ForeignConfig:
    argv = build_foreign_config_show_command()
    payload = await run_storcli(
        argv,
        use_sudo=settings.storcli_use_sudo,
        binary_path=settings.storcli_path,
    )
    return parse_foreign_config(payload)


async def _parse_foreign_config_import_body(
    request: Request,
) -> ForeignConfigImportRequest | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
    try:
        return ForeignConfigImportRequest.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid request body", "detail": exc.errors()},
            status_code=400,
        )


async def _parse_foreign_config_clear_body(
    request: Request,
) -> ForeignConfigClearRequest | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
    try:
        return ForeignConfigClearRequest.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid request body", "detail": exc.errors()},
            status_code=400,
        )


def _any_drive_rebuilding(*, request: Request) -> bool | None:
    """Return True if any drive is rebuilding, False if none, None if unknown.

    ``None`` means the rebuild state cannot be determined: the snapshot table
    is empty (fresh install, post-cleanup, or collector outage). The
    foreign-config destructive routes treat ``None`` as fail-closed because
    "no snapshot" must not be confused with "no rebuild active" — that would
    let the gate fail open while a rebuild is actually in progress on hardware.
    """
    with _session(request) as session:
        latest_id = session.scalar(
            select(ControllerSnapshot.id).order_by(ControllerSnapshot.captured_at.desc()).limit(1)
        )
        if latest_id is None:
            return None
        rebuilding = session.scalar(
            select(PhysicalDriveSnapshot.id)
            .where(PhysicalDriveSnapshot.snapshot_id == latest_id)
            .where(PhysicalDriveSnapshot.state.in_(_REBUILD_DRIVE_STATES))
            .limit(1)
        )
        return rebuilding is not None


def _record_foreign_config_operator_action_sync(
    *,
    request: Request,
    action: Literal["import", "clear"],
    digest: str,
    outcome: str,
) -> None:
    # Some rejection paths (e.g. clear's confirmation phrase mismatch) fail
    # before the foreign-config probe runs, so no digest is available; render
    # those as ``unknown`` to keep the audit message shape stable.
    rendered_digest = digest or "unknown"
    try:
        with _session(request) as session, session.begin():
            record_operator_action(
                session,
                username=str(request.scope.get("user_username", "unknown")),
                message=f"foreign config {action} digest={rendered_digest} {outcome}",
            )
    except SQLAlchemyError:
        LOGGER.exception(
            "operator_action_audit_failed",
            action=f"foreign_config_{action}",
            outcome=outcome,
        )
        raise


@router.get("/drives/{enclosure_id}/{slot_id}/charts", name="drive_charts")
def drive_charts(
    request: Request,
    enclosure_id: int,
    slot_id: int,
    range_days: int = _DEFAULT_CHART_RANGE_DAYS,
    serial_number: str | None = None,
    captured_at: datetime | None = None,
) -> Response:
    started_at = perf_counter()
    resolved_range_days = _validate_range_days(range_days)
    with _session(request) as session:
        chart_serial_number, chart_captured_at = _chart_identity_or_404(
            session,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            serial_number=serial_number,
            captured_at=captured_at,
        )
        view_model = _drive_charts_view_model(
            session=session,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
            serial_number=chart_serial_number,
            range_days=resolved_range_days,
            now_utc=chart_captured_at,
        )
    _log_drive_detail_rendered(
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        range_days=view_model.active_range_days,
        raw_point_count=view_model.raw_point_count,
        hourly_point_count=view_model.hourly_point_count,
        daily_point_count=view_model.daily_point_count,
        elapsed_ms=_elapsed_ms(started_at),
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="partials/drive_charts.html",
        context={"view_model": view_model},
    )


@router.get("/events", name="events")
def events(
    request: Request,
    before_occurred_at: str | None = None,
    before_id: str | None = None,
    since: str | None = None,
) -> Response:
    started_at = perf_counter()
    if (
        _is_htmx_request(request)
        or before_occurred_at is not None
        or before_id is not None
        or since is not None
    ):
        return _events_fragment_response(
            request=request,
            started_at=started_at,
            before_occurred_at=before_occurred_at,
            before_id=before_id,
            since=since,
        )

    categories, severities = _event_filter_values(request)
    view_model = _load_events_page(request, categories=categories, severities=severities)
    response = TEMPLATES.TemplateResponse(
        request=request,
        name="pages/events.html",
        context={
            "active_nav": "events",
            "current_utc_label": _current_utc_label(),
            "static_asset_version": _static_asset_version(),
            "view_model": view_model,
            **_events_filter_context(request=request, categories=categories, severities=severities),
            **_events_empty_context(request=request, view_model=view_model),
        },
    )
    _log_events_rendered(view_model=view_model, elapsed_ms=_elapsed_ms(started_at), partial=False)
    return response


@router.get("/audit", name="audit_log")
async def audit_log(request: Request) -> RedirectResponse:
    target = _events_query_path(
        request=request,
        route_name="events",
        categories=("operator_action",),
        severities=(),
    )
    return RedirectResponse(url=target, status_code=302)


@router.get("/partials/events", name="events_partial")
def events_partial(
    request: Request,
    before_occurred_at: str | None = None,
    before_id: str | None = None,
    since: str | None = None,
) -> Response:
    started_at = perf_counter()
    return _events_fragment_response(
        request=request,
        started_at=started_at,
        before_occurred_at=before_occurred_at,
        before_id=before_id,
        since=since,
    )


def _events_fragment_response(
    *,
    request: Request,
    started_at: float,
    before_occurred_at: str | None,
    before_id: str | None,
    since: str | None,
) -> Response:
    cursor = _parse_events_cursor(
        before_occurred_at=before_occurred_at,
        before_id=before_id,
    )
    since_id = _parse_events_since(since)
    categories, severities = _event_filter_values(request)
    view_model: EventsPageViewModel | EventsFragmentViewModel
    render_events_data_oob = False
    render_events_load_more_oob = cursor is None and since_id is None
    render_events_page_items = cursor is not None
    render_events_poller_oob = False
    render_events_since_oob = since_id is not None
    with _session(request) as session:
        if since_id is not None:
            if since_id == 0:
                page_view_model = load_events_page(
                    session,
                    page_size=EVENTS_PAGE_SIZE,
                    categories=categories,
                    severities=severities,
                )
                if page_view_model.events:
                    view_model = page_view_model
                    render_events_data_oob = True
                    render_events_load_more_oob = True
                    render_events_poller_oob = True
                    render_events_since_oob = False
                else:
                    view_model = load_events_fragment(
                        session,
                        page_size=EVENTS_PAGE_SIZE,
                        categories=categories,
                        severities=severities,
                        since=since_id,
                    )
            else:
                view_model = load_events_fragment(
                    session,
                    page_size=EVENTS_PAGE_SIZE,
                    categories=categories,
                    severities=severities,
                    since=since_id,
                )
            template_name = "partials/events_data.html"
        elif cursor is None:
            view_model = load_events_page(
                session,
                page_size=EVENTS_PAGE_SIZE,
                categories=categories,
                severities=severities,
            )
            template_name = "partials/events_data.html"
        else:
            cursor_occurred_at, cursor_id = cursor
            view_model = load_events_fragment(
                session,
                page_size=EVENTS_PAGE_SIZE,
                before_occurred_at=cursor_occurred_at,
                before_id=cursor_id,
                categories=categories,
                severities=severities,
            )
            template_name = "partials/events_data.html"
    response = TEMPLATES.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "events_poll_since": max(since_id or 0, view_model.latest_event_id),
            "render_events_data_oob": render_events_data_oob,
            "render_events_load_more_oob": render_events_load_more_oob,
            "render_events_poller_oob": render_events_poller_oob,
            "render_events_since_oob": render_events_since_oob,
            "render_events_page_fragment": cursor is None and since_id is None,
            "render_events_page_items": render_events_page_items,
            "view_model": view_model,
            **_events_filter_context(request=request, categories=categories, severities=severities),
            **_events_empty_context(request=request, view_model=view_model),
        },
    )
    _log_events_rendered(view_model=view_model, elapsed_ms=_elapsed_ms(started_at), partial=True)
    return response


def _session(request: Request) -> Session:
    session_factory = cast(sessionmaker[Session], request.app.state.session_factory)
    return session_factory()


async def _database_health_for_request(request: Request) -> DatabaseHealth:
    probe_lock = cast(asyncio.Lock, request.app.state.health_probe_lock)
    async with probe_lock:
        engine = cast(Engine, request.app.state.health_engine)
        executor = cast(Executor, request.app.state.health_executor)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, _database_health, engine)


def _database_health(engine: Engine) -> DatabaseHealth:
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        LOGGER.exception("healthz_database_check_failed")
        return "error"
    return "ok"


def _collector_health(request: Request) -> CollectorHealth:
    settings = cast(Settings, request.app.state.settings)
    if not settings.collector_enabled:
        return "idle"

    collector = getattr(request.app.state, "collector", None)
    lock_fd = getattr(request.app.state, "collector_lock_fd", None)
    if collector is not None and lock_fd is not None:
        return "ok"

    retry_task = getattr(request.app.state, "collector_retry_task", None)
    if _task_is_alive(retry_task):
        return "lock_held"

    return "idle"


def _task_is_alive(task: object) -> bool:
    if not isinstance(task, _TaskLike):
        return False
    return not task.done()


def _load_overview(request: Request) -> OverviewViewModel:
    scheduler = getattr(request.app.state, "scheduler", None)
    with _session(request) as session:
        return load_overview_view_model(
            session,
            scheduler=scheduler,
            overview_url=str(request.url_for("overview").path),
            drives_url=str(request.url_for("drives").path),
        )


def _load_drive_list(request: Request) -> DriveListViewModel:
    scheduler = getattr(request.app.state, "scheduler", None)

    def slot_url(enclosure_id: int, slot_id: int) -> str:
        return str(
            request.url_for(
                "drive_detail_slot_ref",
                slot_ref=f"{enclosure_id}:{slot_id}",
            ).path
        )

    with _session(request) as session:
        return load_drive_list_view_model(
            session,
            scheduler=scheduler,
            slot_url_factory=slot_url,
        )


def _load_events_page(
    request: Request,
    *,
    categories: tuple[str, ...],
    severities: tuple[str, ...],
) -> EventsPageViewModel:
    with _session(request) as session:
        return load_events_page(
            session,
            page_size=EVENTS_PAGE_SIZE,
            categories=categories,
            severities=severities,
        )


def _parse_events_cursor(
    *,
    before_occurred_at: str | None,
    before_id: str | None,
) -> tuple[datetime, int] | None:
    if (before_occurred_at is None) != (before_id is None):
        raise HTTPException(
            status_code=400,
            detail="before_occurred_at and before_id must be provided together",
        )
    if before_occurred_at is None or before_id is None:
        return None

    try:
        parsed_before_occurred_at = datetime.fromisoformat(before_occurred_at)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="before_occurred_at must be a valid ISO 8601 datetime",
        ) from exc
    if parsed_before_occurred_at.tzinfo is None or parsed_before_occurred_at.utcoffset() is None:
        raise HTTPException(
            status_code=400,
            detail="before_occurred_at must include a timezone",
        )

    try:
        parsed_before_id = int(before_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="before_id must be an integer") from exc

    return parsed_before_occurred_at.astimezone(UTC), parsed_before_id


def _parse_events_since(since: str | None) -> int | None:
    if since is None:
        return None
    try:
        parsed_since = int(since)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="since must be an integer") from exc
    if parsed_since < 0:
        raise HTTPException(status_code=400, detail="since must be non-negative")
    return parsed_since


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _event_filter_values(request: Request) -> tuple[tuple[str, ...], tuple[str, ...]]:
    categories = _normalize_query_values(tuple(request.query_params.getlist("category")))
    severities = _normalize_query_values(tuple(request.query_params.getlist("severity")))
    return categories, severities


def _normalize_query_values(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped and stripped not in normalized:
            normalized.append(stripped)
    return tuple(normalized)


def _events_filter_context(
    *,
    request: Request,
    categories: tuple[str, ...],
    severities: tuple[str, ...],
) -> dict[str, object]:
    return {
        "event_filter_state": _events_query_path(
            request=request,
            route_name="events",
            categories=categories,
            severities=severities,
        ),
        "events_partial_filter_state": _events_query_path(
            request=request,
            route_name="events_partial",
            categories=categories,
            severities=severities,
        ),
        "severity_filter_chips": tuple(
            _filter_chip(
                request=request,
                label=severity.capitalize(),
                value=severity,
                filter_name="severity",
                categories=categories,
                severities=severities,
            )
            for severity in _EVENT_SEVERITY_FILTERS
        ),
        "category_filter_chips": tuple(
            _filter_chip(
                request=request,
                label=category.replace("_", " "),
                value=category,
                filter_name="category",
                categories=categories,
                severities=severities,
            )
            for category in _EVENT_CATEGORY_FILTERS
        ),
    }


def _filter_chip(
    *,
    request: Request,
    label: str,
    value: str,
    filter_name: Literal["category", "severity"],
    categories: tuple[str, ...],
    severities: tuple[str, ...],
) -> FilterChip:
    updated_categories = categories
    updated_severities = severities
    active_values = categories if filter_name == "category" else severities
    active = value in active_values
    if filter_name == "category":
        updated_categories = _toggle_filter_value(categories, value)
    else:
        updated_severities = _toggle_filter_value(severities, value)
    return FilterChip(
        label=label,
        value=value,
        active=active,
        href=_events_query_path(
            request=request,
            route_name="events",
            categories=updated_categories,
            severities=updated_severities,
        ),
    )


def _toggle_filter_value(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    if value in values:
        return tuple(existing for existing in values if existing != value)
    return (*values, value)


def _events_query_path(
    *,
    request: Request,
    route_name: str,
    categories: tuple[str, ...],
    severities: tuple[str, ...],
    extra: tuple[tuple[str, str | int], ...] = (),
) -> str:
    query_items: list[tuple[str, str | int]] = []
    query_items.extend(("severity", severity) for severity in severities)
    query_items.extend(("category", category) for category in categories)
    query_items.extend(extra)
    path = str(request.url_for(route_name).path)
    if not query_items:
        return path
    return f"{path}?{urlencode(query_items)}"


def _events_empty_next_run_text(request: Request) -> str:
    settings = get_settings()
    if not settings.collector_enabled:
        return "Metrics collection is disabled; no collection run is scheduled."

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return "No collection run is currently scheduled."
    metrics_job = scheduler.get_job("metrics_collector")
    if metrics_job is None or metrics_job.next_run_time is None:
        return "No collection run is currently scheduled."

    next_run_time = metrics_job.next_run_time
    if next_run_time.tzinfo is None or next_run_time.utcoffset() is None:
        next_run_utc = next_run_time.replace(tzinfo=UTC)
    else:
        next_run_utc = next_run_time.astimezone(UTC)
    seconds = max(0, int((next_run_utc - datetime.now(UTC)).total_seconds()))
    return f"Next scheduled run in {seconds} seconds."


def _events_empty_context(
    *,
    request: Request,
    view_model: EventsPageViewModel | EventsFragmentViewModel,
) -> dict[str, str]:
    if not isinstance(view_model, EventsPageViewModel) or view_model.latest_captured_at is not None:
        return {}
    return {
        "events_empty_title": "Waiting for first metrics collection",
        "events_empty_body": "The collector has not yet completed its first run.",
        "events_empty_next_run": _events_empty_next_run_text(request),
        "events_empty_detail": "No events recorded yet.",
    }


def _latest_drive_or_404(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
) -> tuple[ControllerSnapshot, PhysicalDriveSnapshot]:
    snapshot = _latest_snapshot_or_404(session)
    drive = _find_physical_drive(snapshot, enclosure_id=enclosure_id, slot_id=slot_id)
    if drive is None:
        raise HTTPException(status_code=404, detail="Physical drive not found in latest snapshot")
    return snapshot, drive


def _latest_snapshot_or_404(session: Session) -> ControllerSnapshot:
    snapshot = get_latest_snapshot(session)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No controller snapshot exists")
    return snapshot


def _chart_identity_or_404(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    serial_number: str | None,
    captured_at: datetime | None,
) -> tuple[str, datetime]:
    if serial_number is None and captured_at is None:
        snapshot, drive = _latest_drive_or_404(
            session,
            enclosure_id=enclosure_id,
            slot_id=slot_id,
        )
        return drive.serial_number, snapshot.captured_at
    if serial_number is None or captured_at is None:
        raise HTTPException(
            status_code=400,
            detail="serial_number and captured_at must be provided together",
        )
    if not serial_number:
        raise HTTPException(status_code=400, detail="serial_number must not be empty")
    _latest_snapshot_or_404(session)
    return serial_number, _require_aware_utc_query(captured_at)


def _require_aware_utc_query(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=400, detail="captured_at must include a timezone")
    return value.astimezone(UTC)


def _find_physical_drive(
    snapshot: ControllerSnapshot,
    *,
    enclosure_id: int,
    slot_id: int,
) -> PhysicalDriveSnapshot | None:
    for drive in snapshot.physical_drives:
        if drive.enclosure_id == enclosure_id and drive.slot_id == slot_id:
            return drive
    return None


def _drive_detail_view_model(
    *,
    request: Request,
    session: Session,
    snapshot: ControllerSnapshot,
    drive: PhysicalDriveSnapshot,
    range_days: int,
) -> DriveDetailViewModel:
    settings = get_settings()
    chart_url = str(
        request.url_for(
            "drive_charts",
            enclosure_id=drive.enclosure_id,
            slot_id=drive.slot_id,
        ).path
    )
    return DriveDetailViewModel(
        enclosure_id=drive.enclosure_id,
        slot_id=drive.slot_id,
        title=f"Drive {drive.enclosure_id}:{drive.slot_id}",
        model=drive.model,
        serial_number=drive.serial_number,
        captured_at=snapshot.captured_at,
        captured_at_iso=snapshot.captured_at.isoformat(),
        attributes=_drive_attributes(
            drive,
            temp_warning=settings.temp_warning_celsius,
            temp_critical=settings.temp_critical_celsius,
        ),
        range_tabs=_range_tabs(active_range_days=range_days, chart_url=chart_url),
        charts=_drive_charts_view_model(
            session=session,
            enclosure_id=drive.enclosure_id,
            slot_id=drive.slot_id,
            serial_number=drive.serial_number,
            range_days=range_days,
            now_utc=snapshot.captured_at,
        ),
    )


def _drive_attributes(
    drive: PhysicalDriveSnapshot,
    *,
    temp_warning: int,
    temp_critical: int,
) -> tuple[DriveAttribute, ...]:
    return (
        DriveAttribute(label="Model", value=drive.model),
        DriveAttribute(label="Serial Number", value=drive.serial_number, mono=True),
        DriveAttribute(label="Firmware Revision", value=drive.firmware_version, mono=True),
        DriveAttribute(label="Interface", value=drive.interface),
        DriveAttribute(label="Media Type", value=drive.media_type),
        DriveAttribute(label="Size", value=format_tb(drive.size_bytes)),
        DriveAttribute(label="SAS Address", value=drive.sas_address, mono=True),
        DriveAttribute(
            label="State",
            value=drive.state,
            severity=_event_severity_to_status(
                physical_drive_state_severity(drive.state, drive.state)
            ),
        ),
        DriveAttribute(
            label="Current Temperature",
            value=(
                "Unknown" if drive.temperature_celsius is None else f"{drive.temperature_celsius} C"
            ),
            severity=temperature_severity(
                drive.temperature_celsius,
                temp_warning=temp_warning,
                temp_critical=temp_critical,
            ),
        ),
    )


def _range_tabs(*, active_range_days: int, chart_url: str) -> tuple[RangeTab, ...]:
    return tuple(
        RangeTab(
            label=f"{range_days} days",
            range_days=range_days,
            active=range_days == active_range_days,
            hx_get=chart_url,
        )
        for range_days in _ALLOWED_CHART_RANGE_DAYS
    )


def _drive_charts_view_model(
    *,
    session: Session,
    enclosure_id: int,
    slot_id: int,
    serial_number: str,
    range_days: int,
    now_utc: datetime | None,
) -> DriveChartsViewModel:
    settings = get_settings()
    resolved_range_days = _validate_range_days(range_days)
    temperature_series = load_drive_temperature_series(
        session,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        current_serial_number=serial_number,
        range_days=resolved_range_days,
        now_utc=now_utc,
    )
    error_series = load_drive_error_series(
        session,
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        current_serial_number=serial_number,
        range_days=resolved_range_days,
        now_utc=now_utc,
    )
    temperature_keys = _chart_point_keys(
        temperature_series.timestamps,
        temperature_series.serial_numbers,
        temperature_series.point_keys,
    )
    error_keys = _chart_point_keys(
        error_series.timestamps,
        error_series.serial_numbers,
        error_series.point_keys,
    )
    point_keys = _merge_chart_point_keys(
        temperature_keys=temperature_keys,
        error_keys=error_keys,
    )
    labels = tuple(
        _chart_timestamp_label(point_key.timestamp, range_days=resolved_range_days)
        for point_key in point_keys
    )
    temperature_by_key = dict(
        zip(temperature_keys, temperature_series.average_celsius, strict=True)
    )
    media_errors_by_key = dict(zip(error_keys, error_series.media_errors, strict=True))
    other_errors_by_key = dict(zip(error_keys, error_series.other_errors, strict=True))
    predictive_failures_by_key = dict(
        zip(error_keys, error_series.predictive_failures, strict=True)
    )
    temperature_values = tuple(temperature_by_key.get(point_key) for point_key in point_keys)
    max_temperature = max(
        (value for value in temperature_values if value is not None),
        default=0.0,
    )
    replacement_markers = _chart_replacement_markers(
        temperature_series=temperature_series,
        error_series=error_series,
        range_days=resolved_range_days,
        point_keys=point_keys,
    )
    temperature_chart = _temperature_chart_data(
        labels=labels,
        values=temperature_values,
        warning_celsius=settings.temp_warning_celsius,
        critical_celsius=settings.temp_critical_celsius,
        max_temperature=max_temperature,
        replacement_markers=replacement_markers,
    )
    error_chart = _error_chart_data(
        labels=labels,
        media_errors=tuple(media_errors_by_key.get(point_key) for point_key in point_keys),
        other_errors=tuple(other_errors_by_key.get(point_key) for point_key in point_keys),
        predictive_failures=tuple(
            predictive_failures_by_key.get(point_key) for point_key in point_keys
        ),
        replacement_markers=replacement_markers,
    )
    return DriveChartsViewModel(
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        active_range_days=resolved_range_days,
        temperature_chart=temperature_chart,
        error_chart=error_chart,
        temperature_rows=_temperature_fallback_rows(
            temperature_series,
            range_days=resolved_range_days,
        ),
        error_rows=_error_fallback_rows(error_series, range_days=resolved_range_days),
        raw_point_count=max(temperature_series.raw_point_count, error_series.raw_point_count),
        hourly_point_count=max(
            temperature_series.hourly_point_count,
            error_series.hourly_point_count,
        ),
        daily_point_count=max(temperature_series.daily_point_count, error_series.daily_point_count),
    )


def _temperature_chart_data(
    *,
    labels: tuple[str, ...],
    values: tuple[float | None, ...],
    warning_celsius: int,
    critical_celsius: int,
    max_temperature: float,
    replacement_markers: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "labels": labels,
        "datasets": (
            {
                "label": "Average Temperature",
                "data": values,
                "borderColor": "#37d6ff",
                "backgroundColor": "rgba(55, 214, 255, 0.16)",
                "pointRadius": 2,
                "spanGaps": True,
                "tension": 0.2,
            },
        ),
        "thresholds": {
            "warning": warning_celsius,
            "critical": critical_celsius,
        },
        "thresholdDatasets": (
            _threshold_dataset(
                "Warning Threshold",
                warning_celsius,
                "rgba(240, 180, 77, 0.52)",
                len(labels),
            ),
            _threshold_dataset(
                "Critical Threshold",
                critical_celsius,
                "rgba(255, 92, 108, 0.52)",
                len(labels),
            ),
        ),
        "replacementMarkers": replacement_markers,
        "yMin": 20,
        "yMax": max(70, ceil(max_temperature + 5), warning_celsius + 5, critical_celsius + 5),
    }


def _threshold_dataset(
    label: str,
    value: int,
    color: str,
    point_count: int,
) -> dict[str, Any]:
    return {
        "label": label,
        "data": tuple(value for _ in range(point_count)),
        "borderColor": color,
        "borderDash": (6, 6),
        "borderWidth": 1,
        "pointRadius": 0,
        "spanGaps": True,
        "tension": 0,
    }


def _error_chart_data(
    *,
    labels: tuple[str, ...],
    media_errors: tuple[int | None, ...],
    other_errors: tuple[int | None, ...],
    predictive_failures: tuple[int | None, ...],
    replacement_markers: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "labels": labels,
        "datasets": (
            {
                "label": "Media Errors",
                "data": media_errors,
                "borderColor": "#ff5c6c",
                "pointRadius": 2,
                "spanGaps": True,
                "tension": 0.2,
            },
            {
                "label": "Other Errors",
                "data": other_errors,
                "borderColor": "#f0b44d",
                "pointRadius": 2,
                "spanGaps": True,
                "tension": 0.2,
            },
            {
                "label": "Predictive Failures",
                "data": predictive_failures,
                "borderColor": "#ff5c6c",
                "borderDash": (6, 6),
                "pointRadius": 2,
                "spanGaps": True,
                "tension": 0.2,
            },
        ),
        "replacementMarkers": replacement_markers,
    }


def _temperature_fallback_rows(
    series: DriveTemperatureSeries,
    *,
    range_days: int,
) -> tuple[TemperatureFallbackRow, ...]:
    rows = tuple(
        TemperatureFallbackRow(
            timestamp=_chart_timestamp_label(timestamp, range_days=range_days),
            average_celsius=f"{average:.1f}",
            minimum_celsius="Unknown" if minimum is None else f"{minimum:.1f}",
            maximum_celsius="Unknown" if maximum is None else f"{maximum:.1f}",
        )
        for timestamp, average, minimum, maximum in zip(
            series.timestamps,
            series.average_celsius,
            series.minimum_celsius,
            series.maximum_celsius,
            strict=True,
        )
    )
    return rows[-24:]


def _error_fallback_rows(
    series: DriveErrorSeries,
    *,
    range_days: int,
) -> tuple[ErrorFallbackRow, ...]:
    rows = tuple(
        ErrorFallbackRow(
            timestamp=_chart_timestamp_label(timestamp, range_days=range_days),
            media_errors=str(media_errors),
            other_errors=str(other_errors),
            predictive_failures=str(predictive_failures),
        )
        for timestamp, media_errors, other_errors, predictive_failures in zip(
            series.timestamps,
            series.media_errors,
            series.other_errors,
            series.predictive_failures,
            strict=True,
        )
    )
    return rows[-24:]


def _chart_replacement_markers(
    *,
    temperature_series: DriveTemperatureSeries,
    error_series: DriveErrorSeries,
    range_days: int,
    point_keys: tuple[_ChartPointKey, ...],
) -> tuple[dict[str, Any], ...]:
    marker_indexes: dict[tuple[datetime, str], int] = {}
    for index, point_key in enumerate(point_keys):
        marker_indexes.setdefault((point_key.timestamp, point_key.serial_number), index)
    return tuple(
        {
            "pointIndex": marker_indexes[(marker.timestamp, marker.current_serial_number)],
            "timestamp": _chart_timestamp_label(marker.timestamp, range_days=range_days),
            "label": marker.label,
            "previousSerialNumber": marker.previous_serial_number,
            "currentSerialNumber": marker.current_serial_number,
        }
        for marker in _unique_replacement_markers(
            temperature_series=temperature_series,
            error_series=error_series,
        )
        if (marker.timestamp, marker.current_serial_number) in marker_indexes
    )


def _unique_replacement_markers(
    *,
    temperature_series: DriveTemperatureSeries,
    error_series: DriveErrorSeries,
) -> tuple[DriveReplacementMarker, ...]:
    seen_markers: set[DriveReplacementMarker] = set()
    markers: list[DriveReplacementMarker] = []
    for marker in (*temperature_series.replacement_markers, *error_series.replacement_markers):
        if marker in seen_markers:
            continue
        seen_markers.add(marker)
        markers.append(marker)
    return tuple(markers)


def _chart_point_keys(
    timestamps: tuple[datetime, ...],
    serial_numbers: tuple[str, ...],
    history_point_keys: tuple[DriveHistoryPointKey, ...],
) -> tuple[_ChartPointKey, ...]:
    point_keys: list[_ChartPointKey] = []
    for timestamp, serial_number, history_point_key in zip(
        timestamps,
        serial_numbers,
        history_point_keys,
        strict=True,
    ):
        point_keys.append(
            _ChartPointKey(
                timestamp=timestamp,
                serial_number=serial_number,
                history_point_key=history_point_key,
            )
        )
    return tuple(point_keys)


def _merge_chart_point_keys(
    *,
    temperature_keys: tuple[_ChartPointKey, ...],
    error_keys: tuple[_ChartPointKey, ...],
) -> tuple[_ChartPointKey, ...]:
    ordering_source = (*error_keys, *temperature_keys)
    order_by_key: dict[_ChartPointKey, int] = {}
    for index, point_key in enumerate(ordering_source):
        order_by_key.setdefault(point_key, index)
    return tuple(
        sorted(order_by_key, key=lambda point_key: (point_key.timestamp, order_by_key[point_key]))
    )


def _chart_timestamp_label(timestamp: datetime, *, range_days: int) -> str:
    timestamp_utc = timestamp.astimezone(UTC)
    if range_days == 365:
        return timestamp_utc.strftime("%Y-%m-%d")
    if range_days == 30:
        return timestamp_utc.strftime("%Y-%m-%d %H:00")
    return timestamp_utc.strftime("%Y-%m-%d %H:%M")


def _validate_range_days(range_days: int) -> int:
    if range_days not in _ALLOWED_CHART_RANGE_DAYS:
        raise HTTPException(status_code=400, detail="range_days must be one of 7, 30, or 365")
    return range_days


def _event_severity_to_status(severity: str) -> str:
    if severity == "info":
        return "optimal"
    if severity == "critical":
        return "critical"
    if severity == "warning":
        return "warning"
    return "unknown"


def _current_utc_label() -> str:
    return datetime.now(UTC).strftime("UTC %H:%M:%S")


def _static_asset_version() -> str:
    global STATIC_ASSET_VERSION
    if STATIC_ASSET_VERSION:
        return STATIC_ASSET_VERSION

    digest = hashlib.sha256()
    for path in (
        _PACKAGE_ROOT / "static" / "css" / "app.css",
        _PACKAGE_ROOT / "static" / "icons.svg",
        _PACKAGE_ROOT / "static" / "js" / "local-time.js",
        _PACKAGE_ROOT / "static" / "vendor" / "chart.min.js",
        _PACKAGE_ROOT / "static" / "vendor" / "htmx.min.js",
    ):
        digest.update(path.read_bytes())
    STATIC_ASSET_VERSION = digest.hexdigest()[:12]
    return STATIC_ASSET_VERSION


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)


def _log_overview_rendered(
    *,
    view_model: OverviewViewModel,
    elapsed_ms: float,
    partial: bool,
) -> None:
    captured_at = view_model.captured_at.isoformat() if view_model.captured_at is not None else None
    LOGGER.info(
        "ui_overview_rendered",
        captured_at=captured_at,
        elapsed_ms=elapsed_ms,
        partial=partial,
    )


def _log_events_rendered(
    *,
    view_model: EventsPageViewModel | EventsFragmentViewModel,
    elapsed_ms: float,
    partial: bool,
) -> None:
    LOGGER.info(
        "ui_events_rendered",
        elapsed_ms=elapsed_ms,
        partial=partial,
        event_count=len(view_model.events),
        has_next_page=view_model.next_cursor is not None,
    )


def _log_drive_detail_rendered(
    *,
    enclosure_id: int,
    slot_id: int,
    range_days: int,
    raw_point_count: int,
    hourly_point_count: int,
    daily_point_count: int,
    elapsed_ms: float,
) -> None:
    LOGGER.info(
        "ui_drive_detail_rendered",
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        range_days=range_days,
        raw_point_count=raw_point_count,
        hourly_point_count=hourly_point_count,
        daily_point_count=daily_point_count,
        elapsed_ms=elapsed_ms,
    )
