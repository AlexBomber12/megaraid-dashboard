"""Make stale or failed collection visible without inventing a hardware failure."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import ControllerSnapshot, Event


def observation_warning(session: Session, *, settings: Settings, now: datetime) -> str | None:
    """Use the same freshness window and durable failed-poll evidence as Telegram."""
    if not settings.collector_enabled:
        return None
    captured = session.scalar(
        select(ControllerSnapshot.captured_at)
        .where(ControllerSnapshot.captured_at <= now)
        .order_by(ControllerSnapshot.captured_at.desc(), ControllerSnapshot.id.desc())
        .limit(1)
    )
    if captured is None:
        return "No reliable collection yet. RAID state is unknown."
    label = captured.strftime("%Y-%m-%d %H:%M UTC")
    if captured < now - timedelta(seconds=max(60, 2 * settings.metrics_interval_seconds)):
        return f"Monitoring data is stale. RAID state is unknown. Last observation: {label}."
    failure = session.scalar(
        select(Event.id)
        .where(
            Event.category == "system",
            Event.summary.startswith("Collection failed:"),
            Event.occurred_at >= captured,
            Event.occurred_at <= now,
        )
        .limit(1)
    )
    if failure is not None:
        return f"Collection failed. Showing last known data from {label}."
    return None
