from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.overview import _load_recent_activity


def test_load_recent_activity_orders_desc_and_limits_default(session: Session) -> None:
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    for index in range(12):
        session.add(_event(occurred_at=base_time + timedelta(minutes=index), summary=str(index)))
    session.flush()

    activity = _load_recent_activity(session)

    assert [item.message for item in activity] == ["11", "10", "9", "8", "7", "6", "5", "4"]


def test_load_recent_activity_accepts_explicit_limit(session: Session) -> None:
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    for index in range(3):
        session.add(_event(occurred_at=base_time + timedelta(minutes=index), summary=str(index)))
    session.flush()

    activity = _load_recent_activity(session, limit=2)

    assert [item.message for item in activity] == ["2", "1"]


def test_load_recent_activity_empty_database_returns_empty_list(session: Session) -> None:
    assert _load_recent_activity(session) == []


def test_load_recent_activity_maps_event_fields_and_icons(session: Session) -> None:
    occurred_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            _event(occurred_at=occurred_at, severity="critical", summary="critical event"),
            _event(
                occurred_at=occurred_at + timedelta(minutes=1),
                severity="warning",
                summary="warning event",
            ),
            _event(
                occurred_at=occurred_at + timedelta(minutes=2),
                severity="info",
                summary="info event",
            ),
            _event(
                occurred_at=occurred_at + timedelta(minutes=3),
                severity="unexpected",
                summary="fallback event",
            ),
        ]
    )
    session.flush()

    activity = _load_recent_activity(session)

    assert [
        (item.category, item.message, item.severity, item.occurred_at, item.severity_icon)
        for item in activity
    ] == [
        (
            "physical_drive",
            "fallback event",
            "unexpected",
            occurred_at + timedelta(minutes=3),
            "info",
        ),
        (
            "physical_drive",
            "info event",
            "info",
            occurred_at + timedelta(minutes=2),
            "check-circle",
        ),
        (
            "physical_drive",
            "warning event",
            "warning",
            occurred_at + timedelta(minutes=1),
            "alert-triangle",
        ),
        ("physical_drive", "critical event", "critical", occurred_at, "x-circle"),
    ]


def _event(
    *,
    occurred_at: datetime,
    summary: str,
    severity: str = "info",
    category: str = "physical_drive",
) -> Event:
    return Event(
        occurred_at=occurred_at,
        severity=severity,
        category=category,
        subject="e252:s4",
        summary=summary,
    )
