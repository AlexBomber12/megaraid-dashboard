from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from megaraid_dashboard.db.models import (
    AlertSent,
    AuditLog,
    CacheVaultSnapshot,
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
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
        select(ControllerSnapshot).order_by(ControllerSnapshot.captured_at.desc()).limit(1)
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
    return event


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
    alert = get_alert_by_fingerprint(session, fingerprint)
    sent_at = datetime.now(UTC)
    normalized_suppressed_until = (
        _require_aware_utc(suppressed_until) if suppressed_until is not None else None
    )
    if alert is None:
        alert = AlertSent(
            sent_at=sent_at,
            severity=severity,
            category=category,
            subject=subject,
            fingerprint=fingerprint,
            recipient=recipient,
            smtp_message_id=smtp_message_id,
            suppressed_until=normalized_suppressed_until,
        )
        session.add(alert)
    else:
        alert.sent_at = sent_at
        alert.severity = severity
        alert.category = category
        alert.subject = subject
        alert.recipient = recipient
        alert.smtp_message_id = smtp_message_id
        alert.suppressed_until = normalized_suppressed_until

    session.flush()
    return alert


def get_alert_by_fingerprint(session: Session, fingerprint: str) -> AlertSent | None:
    return session.scalars(
        select(AlertSent).where(AlertSent.fingerprint == fingerprint)
    ).one_or_none()


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "naive datetimes are not allowed; use timezone-aware UTC datetimes"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _storcli_datetime_to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
