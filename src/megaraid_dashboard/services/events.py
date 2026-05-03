from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from megaraid_dashboard.db.models import ControllerSnapshot, Event

EVENTS_PAGE_SIZE = 50
_CONTROLLER_LABEL = "LSI MegaRAID SAS9270CV-8i"


@dataclass(frozen=True)
class EventsCursor:
    before_occurred_at: datetime
    before_id: int

    @property
    def before_occurred_at_iso(self) -> str:
        return self.before_occurred_at.isoformat()


@dataclass(frozen=True)
class EventRow:
    id: int
    occurred_at: datetime
    severity: str
    severity_status: str
    category: str
    subject: str
    summary: str

    @property
    def severity_label(self) -> str:
        return self.severity.capitalize()


@dataclass(frozen=True)
class EventsPageViewModel:
    events: tuple[EventRow, ...]
    next_cursor: EventsCursor | None
    is_first_page: bool
    latest_captured_at: datetime | None
    controller_label: str
    category_filter: str | None = None


@dataclass(frozen=True)
class EventsFragmentViewModel:
    events: tuple[EventRow, ...]
    next_cursor: EventsCursor | None
    is_first_page: bool
    category_filter: str | None = None


def load_events_page(
    session: Session,
    *,
    page_size: int = EVENTS_PAGE_SIZE,
    before_occurred_at: datetime | None = None,
    before_id: int | None = None,
    category: str | None = None,
    controller_label: str = _CONTROLLER_LABEL,
) -> EventsPageViewModel:
    fragment = load_events_fragment(
        session,
        page_size=page_size,
        before_occurred_at=before_occurred_at,
        before_id=before_id,
        category=category,
    )
    latest_captured_at = _latest_captured_at(session) if fragment.is_first_page else None
    return EventsPageViewModel(
        events=fragment.events,
        next_cursor=fragment.next_cursor,
        is_first_page=fragment.is_first_page,
        latest_captured_at=latest_captured_at,
        controller_label=controller_label,
        category_filter=fragment.category_filter,
    )


def load_events_fragment(
    session: Session,
    *,
    page_size: int = EVENTS_PAGE_SIZE,
    before_occurred_at: datetime | None = None,
    before_id: int | None = None,
    category: str | None = None,
    controller_label: str = _CONTROLLER_LABEL,
) -> EventsFragmentViewModel:
    del controller_label
    if page_size < 1:
        msg = "page_size must be at least 1"
        raise ValueError(msg)
    if (before_occurred_at is None) != (before_id is None):
        msg = "before_occurred_at and before_id must be provided together"
        raise ValueError(msg)

    is_first_page = before_occurred_at is None
    resolved_before_occurred_at = (
        None if before_occurred_at is None else _require_aware_utc(before_occurred_at)
    )
    category_filter = _normalize_category_filter(category)
    statement = (
        select(Event).order_by(Event.occurred_at.desc(), Event.id.desc()).limit(page_size + 1)
    )
    if category_filter is not None:
        statement = statement.where(Event.category == category_filter)
    if resolved_before_occurred_at is not None and before_id is not None:
        statement = statement.where(
            or_(
                Event.occurred_at < resolved_before_occurred_at,
                and_(Event.occurred_at == resolved_before_occurred_at, Event.id < before_id),
            )
        )
    events = list(session.scalars(statement))
    visible_events = events[:page_size]
    rows = tuple(_event_row(event) for event in visible_events)
    next_cursor = None
    if len(events) > page_size:
        last_event = visible_events[-1]
        next_cursor = EventsCursor(
            before_occurred_at=_require_aware_utc(last_event.occurred_at),
            before_id=last_event.id,
        )

    return EventsFragmentViewModel(
        events=rows,
        next_cursor=next_cursor,
        is_first_page=is_first_page,
        category_filter=category_filter,
    )


def list_recent_events(session: Session, *, limit: int) -> list[Event]:
    if limit < 1:
        msg = "limit must be at least 1"
        raise ValueError(msg)
    return list(
        session.scalars(
            select(Event).order_by(Event.occurred_at.desc(), Event.id.desc()).limit(limit)
        )
    )


def event_severity_to_status(severity: str) -> str:
    return {
        "info": "optimal",
        "warning": "warning",
        "critical": "critical",
    }.get(severity, "unknown")


def _event_row(event: Event) -> EventRow:
    severity = _normalize_event_severity(event.severity)
    return EventRow(
        id=event.id,
        occurred_at=_require_aware_utc(event.occurred_at),
        severity=severity,
        severity_status=event_severity_to_status(severity),
        category=event.category,
        subject=event.subject,
        summary=event.summary,
    )


def _normalize_event_severity(severity: str) -> str:
    return severity if severity in {"info", "warning", "critical"} else "unknown"


def _normalize_category_filter(category: str | None) -> str | None:
    if category is None:
        return None
    stripped = category.strip()
    return stripped or None


def _latest_captured_at(session: Session) -> datetime | None:
    captured_at = session.scalar(
        select(ControllerSnapshot.captured_at)
        .order_by(ControllerSnapshot.captured_at.desc())
        .limit(1)
    )
    return None if captured_at is None else _require_aware_utc(captured_at)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)
