from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.db.dao import insert_snapshot, record_event
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.events import (
    event_severity_to_status,
    load_events_fragment,
    load_events_page,
)
from megaraid_dashboard.storcli import StorcliSnapshot


def test_empty_database_returns_empty_events_and_no_cursor(session: Session) -> None:
    view_model = load_events_fragment(session)

    assert view_model.events == ()
    assert view_model.next_cursor is None


@pytest.mark.parametrize(
    ("severity", "expected_status"),
    [
        ("info", "optimal"),
        ("warning", "warning"),
        ("critical", "critical"),
        ("unexpected", "unknown"),
    ],
)
def test_event_severity_to_status_contract(severity: str, expected_status: str) -> None:
    assert event_severity_to_status(severity) == expected_status


def test_cursor_pagination_emits_cursor_then_none_on_last_page(session: Session) -> None:
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    events = tuple(
        _insert_event(session, occurred_at=base_time + timedelta(minutes=index), subject=str(index))
        for index in range(4)
    )
    session.commit()

    view_model = load_events_fragment(session, page_size=3)

    assert [event.subject for event in view_model.events] == ["3", "2", "1"]
    assert view_model.next_cursor is not None
    assert view_model.next_cursor.before_occurred_at == events[1].occurred_at
    assert view_model.next_cursor.before_id == events[1].id

    last_page = load_events_fragment(
        session,
        page_size=3,
        before_occurred_at=view_model.next_cursor.before_occurred_at,
        before_id=view_model.next_cursor.before_id,
    )

    assert [event.subject for event in last_page.events] == ["0"]
    assert last_page.next_cursor is None


def test_cursor_pagination_uses_id_when_events_share_occurred_at(session: Session) -> None:
    occurred_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    events = tuple(
        _insert_event(session, occurred_at=occurred_at, subject=str(index)) for index in range(5)
    )
    session.commit()
    expected_ids = [event.id for event in reversed(events)]

    first_page = load_events_fragment(session, page_size=2)
    assert first_page.next_cursor is not None
    second_page = load_events_fragment(
        session,
        page_size=2,
        before_occurred_at=first_page.next_cursor.before_occurred_at,
        before_id=first_page.next_cursor.before_id,
    )
    assert second_page.next_cursor is not None
    third_page = load_events_fragment(
        session,
        page_size=2,
        before_occurred_at=second_page.next_cursor.before_occurred_at,
        before_id=second_page.next_cursor.before_id,
    )

    paged_ids = [
        event.id for event in (*first_page.events, *second_page.events, *third_page.events)
    ]
    assert paged_ids == expected_ids
    assert third_page.next_cursor is None


def test_events_ordering_is_descending_by_occurred_at_and_id(session: Session) -> None:
    shared_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    older_time = shared_time - timedelta(minutes=5)
    newest_time = shared_time + timedelta(minutes=5)
    older = _insert_event(session, occurred_at=older_time, subject="older")
    shared_low_id = _insert_event(session, occurred_at=shared_time, subject="shared-low")
    shared_high_id = _insert_event(session, occurred_at=shared_time, subject="shared-high")
    newest = _insert_event(session, occurred_at=newest_time, subject="newest")
    session.commit()

    view_model = load_events_fragment(session)

    assert [event.id for event in view_model.events] == [
        newest.id,
        shared_high_id.id,
        shared_low_id.id,
        older.id,
    ]


def test_load_events_page_populates_latest_captured_at_when_available(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert_event(session, occurred_at=datetime(2026, 4, 25, 12, 5, tzinfo=UTC))
    session.commit()

    without_snapshot = load_events_page(session)
    assert without_snapshot.latest_captured_at is None

    insert_snapshot(session, sample_snapshot)
    session.commit()

    view_model = load_events_page(session)

    assert view_model.latest_captured_at == sample_snapshot.captured_at


def _insert_event(
    session: Session,
    *,
    occurred_at: datetime,
    severity: str = "info",
    category: str = "physical_drive",
    subject: str = "PD e252:s4",
    summary: str = "Drive state changed",
) -> Event:
    event = record_event(
        session,
        severity=severity,
        category=category,
        subject=subject,
        summary=summary,
    )
    event.occurred_at = occurred_at
    session.flush()
    return event
