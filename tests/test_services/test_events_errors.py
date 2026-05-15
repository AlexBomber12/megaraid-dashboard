from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.db.dao import record_event
from megaraid_dashboard.services.events import (
    EventRow,
    EventsCursor,
    EventsFragmentViewModel,
    EventsPageViewModel,
    _filter_inputs,
    _normalize_filters,
    _require_aware_utc,
    list_recent_events,
    load_events_fragment,
)


def test_page_view_model_category_filter_returns_none_when_multiple() -> None:
    cursor = EventsCursor(before_occurred_at=datetime.now(UTC), before_id=1)
    fragment = EventsFragmentViewModel(
        events=(), next_cursor=cursor, is_first_page=True, category_filters=("a", "b")
    )
    page = EventsPageViewModel(
        events=(),
        next_cursor=cursor,
        is_first_page=True,
        latest_captured_at=None,
        controller_label="LSI",
        category_filters=("a", "b"),
    )
    assert fragment.category_filter is None
    assert page.category_filter is None


def test_fragment_view_model_category_filter_returns_only_when_single() -> None:
    fragment = EventsFragmentViewModel(
        events=(), next_cursor=None, is_first_page=True, category_filters=("only",)
    )
    assert fragment.category_filter == "only"


def test_load_events_fragment_rejects_invalid_page_size(session: Session) -> None:
    with pytest.raises(ValueError, match="page_size must be at least 1"):
        load_events_fragment(session, page_size=0)


def test_load_events_fragment_rejects_partial_cursor(session: Session) -> None:
    with pytest.raises(ValueError, match="must be provided together"):
        load_events_fragment(session, before_occurred_at=datetime.now(UTC))


def test_load_events_fragment_rejects_partial_cursor_only_id(session: Session) -> None:
    with pytest.raises(ValueError, match="must be provided together"):
        load_events_fragment(session, before_id=1)


def test_list_recent_events_rejects_zero_limit(session: Session) -> None:
    with pytest.raises(ValueError, match="limit must be at least 1"):
        list_recent_events(session, limit=0)


def test_normalize_filters_skips_none_blank_and_duplicates() -> None:
    assert _normalize_filters((None, "x", "  ", "x", " y ")) == ("x", "y")


def test_filter_inputs_appends_legacy() -> None:
    assert _filter_inputs(("a",), "b") == ("a", "b")


def test_filter_inputs_returns_values_when_legacy_is_none() -> None:
    assert _filter_inputs(("a",), None) == ("a",)


def test_require_aware_utc_rejects_naive() -> None:
    with pytest.raises(ValueError, match="datetime must include a timezone"):
        _require_aware_utc(datetime(2026, 5, 15))


def test_events_cursor_iso_property() -> None:
    cursor = EventsCursor(before_occurred_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC), before_id=1)
    assert cursor.before_occurred_at_iso.startswith("2026-05-15T12:00:00")


@pytest.mark.parametrize(
    ("severity", "label", "icon"),
    [
        ("info", "Info", "check-circle"),
        ("warning", "Warning", "alert-triangle"),
        ("critical", "Critical", "x-circle"),
        ("other", "Other", "circle"),
    ],
)
def test_event_row_severity_label_and_icon(severity: str, label: str, icon: str) -> None:
    row = EventRow(
        id=1,
        occurred_at=datetime(2026, 5, 15, tzinfo=UTC),
        severity=severity,
        severity_status="optimal",
        category="cv",
        subject="x",
        summary="y",
    )
    assert row.severity_label == label
    assert row.severity_icon == icon


def test_view_models_latest_event_id(session: Session) -> None:
    page = EventsPageViewModel(
        events=(),
        next_cursor=None,
        is_first_page=True,
        latest_captured_at=None,
        controller_label="LSI",
    )
    fragment = EventsFragmentViewModel(events=(), next_cursor=None, is_first_page=True)
    assert page.latest_event_id == 0
    assert fragment.latest_event_id == 0


def test_list_recent_events_returns_rows(session: Session) -> None:
    record_event(
        session,
        severity="info",
        category="cv",
        subject="hello",
        summary="ok",
    )
    session.flush()
    assert len(list_recent_events(session, limit=5)) == 1
