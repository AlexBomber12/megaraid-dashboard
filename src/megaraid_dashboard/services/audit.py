from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from megaraid_dashboard.db.models import Event
from megaraid_dashboard.web.metrics import EVENTS_TOTAL


def record_operator_action(
    session: Session,
    *,
    username: str,
    message: str,
    occurred_at: datetime | None = None,
) -> Event:
    """Record an info-level operator event, below the default notifier threshold."""
    resolved_occurred_at = occurred_at or datetime.now(UTC)
    if resolved_occurred_at.tzinfo is None or resolved_occurred_at.utcoffset() is None:
        msg = "occurred_at must be a timezone-aware UTC datetime"
        raise ValueError(msg)
    event = Event(
        category="operator_action",
        severity="info",
        subject="Operator action",
        summary=message,
        occurred_at=resolved_occurred_at.astimezone(UTC),
        operator_username=username,
    )
    session.add(event)
    session.flush()
    EVENTS_TOTAL.labels(severity=event.severity, category=event.category).inc()
    return event
