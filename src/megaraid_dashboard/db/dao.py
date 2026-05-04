from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.util import identity_key

from megaraid_dashboard.db.event_metrics import stage_event_metric
from megaraid_dashboard.db.models import (
    AlertSent,
    AuditLog,
    CacheVaultSnapshot,
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    PhysicalDriveTempState,
    SystemState,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.storcli import StorcliSnapshot


def insert_snapshot(
    session: Session,
    snapshot: StorcliSnapshot,
    *,
    store_raw: bool = False,
    raw_payload: dict[str, Any] | None = None,
) -> ControllerSnapshot:
    controller = snapshot.controller
    controller_snapshot = ControllerSnapshot(
        captured_at=_require_aware_utc(snapshot.captured_at),
        model_name=controller.model_name,
        serial_number=controller.serial_number,
        firmware_version=controller.firmware_version,
        bios_version=controller.bios_version,
        driver_version=controller.driver_version,
        alarm_state=controller.alarm_state,
        cv_present=controller.cv_present,
        bbu_present=controller.bbu_present,
        roc_temperature_celsius=controller.roc_temperature_celsius,
        raw_json=raw_payload if store_raw else None,
    )
    controller_snapshot.virtual_drives = [
        VirtualDriveSnapshot(
            vd_id=virtual_drive.vd_id,
            name=virtual_drive.name,
            raid_level=virtual_drive.raid_level,
            size_bytes=virtual_drive.size_bytes,
            state=virtual_drive.state,
            access=virtual_drive.access,
            cache=virtual_drive.cache,
        )
        for virtual_drive in snapshot.virtual_drives
    ]
    controller_snapshot.physical_drives = [
        PhysicalDriveSnapshot(
            enclosure_id=physical_drive.enclosure_id,
            slot_id=physical_drive.slot_id,
            device_id=physical_drive.device_id,
            model=physical_drive.model,
            serial_number=physical_drive.serial_number,
            firmware_version=physical_drive.firmware_version,
            size_bytes=physical_drive.size_bytes,
            interface=physical_drive.interface,
            media_type=physical_drive.media_type,
            state=physical_drive.state,
            disk_group_id=physical_drive.disk_group_id,
            temperature_celsius=physical_drive.temperature_celsius,
            media_errors=physical_drive.media_errors,
            other_errors=physical_drive.other_errors,
            predictive_failures=physical_drive.predictive_failures,
            smart_alert=physical_drive.smart_alert,
            sas_address=physical_drive.sas_address,
        )
        for physical_drive in snapshot.physical_drives
    ]
    if snapshot.cachevault is not None:
        controller_snapshot.cachevault = CacheVaultSnapshot(
            type=snapshot.cachevault.type,
            state=snapshot.cachevault.state,
            temperature_celsius=snapshot.cachevault.temperature_celsius,
            pack_energy=snapshot.cachevault.pack_energy,
            capacitance_percent=snapshot.cachevault.capacitance_percent,
            replacement_required=snapshot.cachevault.replacement_required,
            next_learn_cycle=_storcli_datetime_to_utc(snapshot.cachevault.next_learn_cycle),
        )

    session.add(controller_snapshot)
    session.flush()
    return controller_snapshot


def get_latest_snapshot(session: Session) -> ControllerSnapshot | None:
    return session.scalars(
        select(ControllerSnapshot)
        .options(
            selectinload(ControllerSnapshot.virtual_drives),
            selectinload(ControllerSnapshot.physical_drives),
            selectinload(ControllerSnapshot.cachevault),
        )
        .order_by(ControllerSnapshot.captured_at.desc())
        .limit(1)
    ).one_or_none()


def list_recent_snapshots(session: Session, *, limit: int = 100) -> list[ControllerSnapshot]:
    return list(
        session.scalars(
            select(ControllerSnapshot).order_by(ControllerSnapshot.captured_at.desc()).limit(limit)
        )
    )


def record_event(
    session: Session,
    *,
    severity: str,
    category: str,
    subject: str,
    summary: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> Event:
    event = Event(
        occurred_at=datetime.now(UTC),
        severity=severity,
        category=category,
        subject=subject,
        summary=summary,
        before_json=before,
        after_json=after,
    )
    session.add(event)
    session.flush()
    stage_event_metric(session, severity=severity, category=category)
    return event


_SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}


def iter_pending_events(
    session: Session,
    *,
    severity_threshold: str,
    since: datetime,
) -> Iterator[Event]:
    _require_aware_utc(since)
    allowed = _severities_at_or_above(severity_threshold)
    statement = (
        select(Event)
        .where(
            Event.severity.in_(allowed),
            Event.notified_at.is_(None),
            Event.occurred_at >= since,
        )
        .order_by(Event.occurred_at.asc(), Event.id.asc())
    )
    yield from session.execute(statement).scalars()


def _severities_at_or_above(threshold: str) -> set[str]:
    if threshold not in _SEVERITY_RANK:
        msg = f"unknown severity threshold: {threshold!r}"
        raise ValueError(msg)
    minimum = _SEVERITY_RANK[threshold]
    return {name for name, rank in _SEVERITY_RANK.items() if rank >= minimum}


def mark_event_notified(session: Session, event_id: int, sent_at: datetime) -> None:
    _require_aware_utc(sent_at)
    event = session.get(Event, event_id)
    if event is None:
        msg = f"event {event_id} not found"
        raise LookupError(msg)
    event.notified_at = sent_at.astimezone(UTC)
    session.flush()


def count_events_notified_since(session: Session, *, since: datetime) -> int:
    _require_aware_utc(since)
    result = session.execute(
        select(func.count()).select_from(Event).where(Event.notified_at >= since)
    ).scalar_one()
    return int(result or 0)


def get_temp_state(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    serial_number: str,
) -> PhysicalDriveTempState | None:
    return session.scalars(
        select(PhysicalDriveTempState)
        .where(PhysicalDriveTempState.enclosure_id == enclosure_id)
        .where(PhysicalDriveTempState.slot_id == slot_id)
        .where(PhysicalDriveTempState.serial_number == serial_number)
    ).one_or_none()


def upsert_temp_state(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
    serial_number: str,
    state: str,
) -> PhysicalDriveTempState:
    now = datetime.now(UTC)
    values = {
        "enclosure_id": enclosure_id,
        "slot_id": slot_id,
        "serial_number": serial_number,
        "state": state,
        "updated_at": now,
    }
    insert_statement = sqlite_insert(PhysicalDriveTempState).values(**values)
    upsert_statement = insert_statement.on_conflict_do_update(
        index_elements=[
            PhysicalDriveTempState.enclosure_id,
            PhysicalDriveTempState.slot_id,
            PhysicalDriveTempState.serial_number,
        ],
        set_={
            "state": insert_statement.excluded.state,
            "updated_at": insert_statement.excluded.updated_at,
        },
    ).returning(PhysicalDriveTempState.id)

    state_id = session.execute(upsert_statement).scalar_one()
    temp_state = session.get(PhysicalDriveTempState, state_id, populate_existing=True)
    if temp_state is None:
        msg = "temperature state upsert did not return a persisted row"
        raise RuntimeError(msg)
    return temp_state


def clear_temp_state_for_slot(
    session: Session,
    *,
    enclosure_id: int,
    slot_id: int,
) -> int:
    result = session.execute(
        delete(PhysicalDriveTempState)
        .where(PhysicalDriveTempState.enclosure_id == enclosure_id)
        .where(PhysicalDriveTempState.slot_id == slot_id)
    )
    session.flush()
    return int(getattr(result, "rowcount", 0) or 0)


def record_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    target: str,
    command_argv: list[str],
    exit_code: int | None,
    stdout_tail: str | None,
    stderr_tail: str | None,
    duration_seconds: float | None,
    success: bool,
) -> AuditLog:
    audit_log = AuditLog(
        occurred_at=datetime.now(UTC),
        actor=actor,
        action=action,
        target=target,
        command_argv=list(command_argv),
        exit_code=exit_code,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        duration_seconds=duration_seconds,
        success=success,
    )
    session.add(audit_log)
    session.flush()
    return audit_log


def upsert_alert_sent(
    session: Session,
    *,
    severity: str,
    category: str,
    subject: str,
    fingerprint: str,
    recipient: str,
    smtp_message_id: str | None = None,
    suppressed_until: datetime | None = None,
) -> AlertSent:
    sent_at = datetime.now(UTC)
    normalized_suppressed_until = (
        _require_aware_utc(suppressed_until) if suppressed_until is not None else None
    )
    values = {
        "sent_at": sent_at,
        "severity": severity,
        "category": category,
        "subject": subject,
        "fingerprint": fingerprint,
        "recipient": recipient,
        "smtp_message_id": smtp_message_id,
        "suppressed_until": normalized_suppressed_until,
    }
    insert_statement = sqlite_insert(AlertSent).values(**values)
    upsert_statement = insert_statement.on_conflict_do_update(
        index_elements=[AlertSent.fingerprint],
        set_={
            "sent_at": insert_statement.excluded.sent_at,
            "severity": insert_statement.excluded.severity,
            "category": insert_statement.excluded.category,
            "subject": insert_statement.excluded.subject,
            "recipient": insert_statement.excluded.recipient,
            "smtp_message_id": insert_statement.excluded.smtp_message_id,
            "suppressed_until": insert_statement.excluded.suppressed_until,
        },
    ).returning(AlertSent.id)

    alert_id = session.execute(upsert_statement).scalar_one()
    alert = session.get(AlertSent, alert_id, populate_existing=True)
    if alert is None:
        msg = "alert upsert did not return a persisted row"
        raise RuntimeError(msg)
    return alert


def get_alert_by_fingerprint(session: Session, fingerprint: str) -> AlertSent | None:
    return session.scalars(
        select(AlertSent).where(AlertSent.fingerprint == fingerprint)
    ).one_or_none()


_MAINTENANCE_MODE_KEY = "maintenance_mode"


def get_state(session: Session, key: str) -> str | None:
    row = session.get(SystemState, key)
    return row.value if row is not None else None


def set_state(session: Session, key: str, value: str) -> None:
    now = datetime.now(UTC)
    insert_statement = sqlite_insert(SystemState).values(key=key, value=value, updated_at=now)
    upsert_statement = insert_statement.on_conflict_do_update(
        index_elements=[SystemState.key],
        set_={
            "value": insert_statement.excluded.value,
            "updated_at": insert_statement.excluded.updated_at,
        },
    )
    session.execute(upsert_statement)
    existing = session.identity_map.get(identity_key(SystemState, key))
    if existing is not None:
        session.expire(existing)
    session.flush()


def delete_state(session: Session, key: str) -> None:
    row = session.get(SystemState, key)
    if row is not None:
        session.delete(row)
        session.flush()


@dataclass(frozen=True)
class MaintenanceState:
    active: bool
    expires_at: datetime | None
    started_by: str | None


def get_maintenance_state(session: Session, *, now: datetime) -> MaintenanceState:
    _require_aware_utc(now)
    raw = get_state(session, _MAINTENANCE_MODE_KEY)
    if raw is None:
        return MaintenanceState(active=False, expires_at=None, started_by=None)

    payload = json.loads(raw)
    expires_at = _parse_optional_datetime(payload.get("expires_at"))
    started_by = _optional_string(payload.get("started_by"))
    if expires_at is not None and expires_at <= now:
        return MaintenanceState(active=False, expires_at=expires_at, started_by=started_by)

    return MaintenanceState(
        active=bool(payload.get("active")),
        expires_at=expires_at,
        started_by=started_by,
    )


def set_maintenance_state(
    session: Session,
    *,
    active: bool,
    expires_at: datetime | None,
    started_by: str | None,
) -> None:
    if expires_at is not None:
        _require_aware_utc(expires_at)
    if not active:
        delete_state(session, _MAINTENANCE_MODE_KEY)
        return

    payload = {
        "active": True,
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
        "started_by": started_by,
    }
    set_state(session, _MAINTENANCE_MODE_KEY, json.dumps(payload))


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "naive datetimes are not allowed; use timezone-aware UTC datetimes"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _parse_optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = "expires_at must be an ISO datetime string or null"
        raise ValueError(msg)
    return _require_aware_utc(datetime.fromisoformat(value))


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = "started_by must be a string or null"
        raise ValueError(msg)
    return value


def _storcli_datetime_to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
