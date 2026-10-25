"""Revalidate queued Telegram incidents against saved, fresh collector data.

This module never polls hardware. Unknown data retains the delivery for a later cycle;
positive evidence of recovery/replacement makes it obsolete. The scheduler serializes
collection and notification transactions, so the snapshot and temperature state agree.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import (
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    PhysicalDriveTempState,
)
from megaraid_dashboard.services.notifier import _to_aware_utc
from megaraid_dashboard.services.telegram_messages import incident_key

DRIVE_SUBJECT = re.compile(r"PD e(\d+):s(\d+)")


def drive_serial_at(session: Session, event: Event) -> str | None:
    after = event.after_json or {}
    serial = after.get("serial_number")
    if isinstance(serial, str):
        return serial or None
    match = DRIVE_SUBJECT.fullmatch(event.subject)
    if match is None:
        return None
    # Select the snapshot first: a missing slot must not match an older disk snapshot.
    snapshot_id = session.scalar(
        select(ControllerSnapshot.id)
        .where(ControllerSnapshot.captured_at <= event.occurred_at)
        .order_by(ControllerSnapshot.captured_at.desc(), ControllerSnapshot.id.desc())
        .limit(1)
    )
    return (
        session.scalar(
            select(PhysicalDriveSnapshot.serial_number).where(
                PhysicalDriveSnapshot.snapshot_id == snapshot_id,
                PhysicalDriveSnapshot.enclosure_id == int(match.group(1)),
                PhysicalDriveSnapshot.slot_id == int(match.group(2)),
            )
        )
        or None
    )


def incident_superseded(session: Session, event: Event, *, before_id: int | None = None) -> bool:
    """A clear/change ends an incident, even if the same state recurs later.

    CacheVault state, presence and replacement-required flags are independent dimensions;
    a change of one must not cancel delivery of another still-active failure.
    """
    statement = select(Event).where(
        Event.category == event.category,
        Event.subject == event.subject,
        Event.id > event.id,
    )
    if before_id is not None:
        statement = statement.where(Event.id < before_id)
    fields = set(event.after_json or {}) - {"serial_number"}
    return any(
        fields.intersection(other.after_json or {})
        and (other.severity != event.severity or incident_key(other) != incident_key(event))
        for other in session.scalars(statement)
    )


def delivery_relevance(session: Session, event: Event, *, settings: Settings, now: datetime) -> str:
    """Return active, obsolete, or a diagnostic explaining why delivery must wait."""
    snapshot = session.scalar(
        select(ControllerSnapshot)
        .where(ControllerSnapshot.captured_at <= now)
        .order_by(ControllerSnapshot.captured_at.desc(), ControllerSnapshot.id.desc())
        .limit(1)
    )
    if event.category == "system":
        recovered = snapshot is not None and snapshot.captured_at >= event.occurred_at
        return "obsolete" if recovered else "active"
    freshness = timedelta(seconds=max(60, 2 * settings.metrics_interval_seconds))
    if snapshot is None or _to_aware_utc(snapshot.captured_at) < now - freshness:
        return "awaiting_fresh_data"
    if incident_superseded(session, event):
        return "obsolete"
    after = event.after_json or {}
    if event.category == "controller_temperature":
        return _temperature_relevance(
            snapshot.roc_temperature_celsius,
            severity=event.severity,
            warning=settings.roc_temp_warning_celsius,
            critical=settings.roc_temp_critical_celsius,
            hysteresis=settings.roc_temp_hysteresis_celsius,
            clear_at_boundary=True,
        )
    if event.category == "vd_state":
        drive = next((d for d in snapshot.virtual_drives if event.subject == f"VD {d.vd_id}"), None)
        state = after.get("state")
        if not isinstance(state, str):
            return "awaiting_incident_state"
        active = drive is not None and drive.state.strip().casefold() == state.strip().casefold()
        return "active" if active else "obsolete"
    if event.category == "cv_state":
        cv = snapshot.cachevault
        if "present" in after:
            active = (cv is not None) == after["present"]
        elif cv is None:
            active = False
        elif "state" in after:
            active = cv.state == after["state"]
        else:
            active = cv.replacement_required and after.get("replacement_required") is True
        return "active" if active else "obsolete"
    match = DRIVE_SUBJECT.fullmatch(event.subject)
    if match is None:
        return "awaiting_drive_identity"
    drive_pd = next(
        (
            d
            for d in snapshot.physical_drives
            if (d.enclosure_id, d.slot_id) == (int(match.group(1)), int(match.group(2)))
        ),
        None,
    )
    if drive_pd is None:
        return "obsolete"
    serial = drive_serial_at(session, event)
    if serial is None or not drive_pd.serial_number:
        return "awaiting_drive_identity"
    if drive_pd.serial_number != serial:
        return "obsolete"
    if event.category == "temperature":
        state = session.scalar(
            select(PhysicalDriveTempState.state).where(
                PhysicalDriveTempState.enclosure_id == drive_pd.enclosure_id,
                PhysicalDriveTempState.slot_id == drive_pd.slot_id,
                PhysicalDriveTempState.serial_number == serial,
            )
        )
        if state is not None and state != event.severity:
            return "obsolete"
        return _temperature_relevance(
            drive_pd.temperature_celsius,
            severity=event.severity,
            warning=settings.temp_warning_celsius,
            critical=settings.temp_critical_celsius,
            hysteresis=settings.temp_hysteresis_celsius,
            clear_at_boundary=False,
        )
    if event.category == "pd_state":
        active = drive_pd.state == after.get("state")
    elif event.category == "smart_alert":
        active = drive_pd.smart_alert
    else:
        value = after.get(event.category)
        if not isinstance(value, int):
            return "awaiting_incident_state"
        active = getattr(drive_pd, event.category) >= max(1, value)
    return "active" if active else "obsolete"


def _temperature_relevance(
    temperature: int | None,
    *,
    severity: str,
    warning: int,
    critical: int,
    hysteresis: int,
    clear_at_boundary: bool,
) -> str:
    if temperature is None:
        return "awaiting_temperature"
    threshold = critical if severity == "critical" else warning
    boundary = threshold - hysteresis
    # Drive states clear strictly below the boundary; RoC events clear at it.
    above_boundary = temperature > boundary if clear_at_boundary else temperature >= boundary
    active = above_boundary and (severity == "critical" or temperature < critical)
    return "active" if active else "obsolete"
