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
    operator_username: str | None = None

    @property
    def severity_label(self) -> str:
        return self.severity.capitalize()

    @property
    def severity_icon(self) -> str:
        return {
            "info": "check-circle",
            "warning": "alert-triangle",
            "critical": "x-circle",
        }.get(self.severity, "circle")


@dataclass(frozen=True)
class EventsPageViewModel:
    events: tuple[EventRow, ...]
    next_cursor: EventsCursor | None
    is_first_page: bool
    latest_captured_at: datetime | None
    controller_label: str
    category_filters: tuple[str, ...] = ()
    severity_filters: tuple[str, ...] = ()

    @property
    def latest_event_id(self) -> int:
        return self.events[0].id if self.events else 0

    @property
    def category_filter(self) -> str | None:
        return self.category_filters[0] if len(self.category_filters) == 1 else None


@dataclass(frozen=True)
class EventsFragmentViewModel:
    events: tuple[EventRow, ...]
    next_cursor: EventsCursor | None
    is_first_page: bool
    category_filters: tuple[str, ...] = ()
    severity_filters: tuple[str, ...] = ()

    @property
    def latest_event_id(self) -> int:
        return self.events[0].id if self.events else 0

    @property
    def category_filter(self) -> str | None:
        return self.category_filters[0] if len(self.category_filters) == 1 else None


def load_events_page(
    session: Session,
    *,
    page_size: int = EVENTS_PAGE_SIZE,
    before_occurred_at: datetime | None = None,
    before_id: int | None = None,
    categories: tuple[str, ...] = (),
    severities: tuple[str, ...] = (),
    category: str | None = None,
    controller_label: str = _CONTROLLER_LABEL,
) -> EventsPageViewModel:
    category_filters = _normalize_filters(_filter_inputs(categories, category))
    severity_filters = _normalize_filters(severities)
    fragment = load_events_fragment(
        session,
        page_size=page_size,
        before_occurred_at=before_occurred_at,
        before_id=before_id,
        categories=category_filters,
        severities=severity_filters,
    )
    latest_captured_at = _latest_captured_at(session) if fragment.is_first_page else None
    return EventsPageViewModel(
        events=fragment.events,
        next_cursor=fragment.next_cursor,
        is_first_page=fragment.is_first_page,
        latest_captured_at=latest_captured_at,
        controller_label=controller_label,
        category_filters=fragment.category_filters,
        severity_filters=fragment.severity_filters,
    )


def load_events_fragment(
    session: Session,
    *,
    page_size: int = EVENTS_PAGE_SIZE,
    before_occurred_at: datetime | None = None,
    before_id: int | None = None,
    categories: tuple[str, ...] = (),
    severities: tuple[str, ...] = (),
    category: str | None = None,
    since: int | None = None,
    controller_label: str = _CONTROLLER_LABEL,
) -> EventsFragmentViewModel:
    del controller_label
    if page_size < 1:
        msg = "page_size must be at least 1"
        raise ValueError(msg)
    if (before_occurred_at is None) != (before_id is None):
        msg = "before_occurred_at and before_id must be provided together"
        raise ValueError(msg)

    is_first_page = before_occurred_at is None and since is None
    resolved_before_occurred_at = (
        None if before_occurred_at is None else _require_aware_utc(before_occurred_at)
    )
    category_filters = _normalize_filters(_filter_inputs(categories, category))
    severity_filters = _normalize_filters(severities)
    statement = (
        select(Event).order_by(Event.occurred_at.desc(), Event.id.desc()).limit(page_size + 1)
    )
    if category_filters:
        statement = statement.where(Event.category.in_(category_filters))
    if severity_filters:
        statement = statement.where(Event.severity.in_(severity_filters))
    if since is not None:
        statement = statement.where(Event.id > since)
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
        category_filters=category_filters,
        severity_filters=severity_filters,
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
        operator_username=event.operator_username,
    )


def _normalize_event_severity(severity: str) -> str:
    return severity if severity in {"info", "warning", "critical"} else "unknown"


def _normalize_filters(values: tuple[str | None, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if value is None:
            continue
        stripped = value.strip()
        if stripped and stripped not in normalized:
            normalized.append(stripped)
    return tuple(normalized)


def _filter_inputs(values: tuple[str, ...], legacy_value: str | None) -> tuple[str | None, ...]:
    if legacy_value is None:
        return values
    return (*values, legacy_value)


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
