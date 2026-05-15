from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from megaraid_dashboard.db.base import UTCDateTime


def _build_type() -> UTCDateTime:
    type_decorator = UTCDateTime()
    type_decorator.load_dialect_impl(MagicMock(name="dialect"))
    return type_decorator


def test_load_dialect_impl_returns_timezone_aware_type() -> None:
    type_decorator = UTCDateTime()
    dialect = MagicMock()
    dialect.type_descriptor.side_effect = lambda value: value
    impl = type_decorator.load_dialect_impl(dialect)
    assert dialect.type_descriptor.called
    assert impl is not None


def test_process_bind_param_returns_none_for_none() -> None:
    type_decorator = _build_type()
    assert type_decorator.process_bind_param(None, MagicMock()) is None


def test_process_bind_param_rejects_naive_datetime() -> None:
    type_decorator = _build_type()
    with pytest.raises(ValueError, match="naive datetimes"):
        type_decorator.process_bind_param(datetime(2026, 5, 15, 12, 0), MagicMock())


def test_process_bind_param_normalizes_non_utc_offset_to_utc() -> None:
    type_decorator = _build_type()
    tz = timezone(timedelta(hours=2))
    value = datetime(2026, 5, 15, 14, 0, tzinfo=tz)
    result = type_decorator.process_bind_param(value, MagicMock())
    assert result is not None
    assert result.tzinfo is UTC
    assert result.hour == 12


def test_process_result_value_returns_none_for_none() -> None:
    type_decorator = _build_type()
    assert type_decorator.process_result_value(None, MagicMock()) is None


def test_process_result_value_attaches_utc_for_naive_datetime() -> None:
    type_decorator = _build_type()
    value = datetime(2026, 5, 15, 12, 0)
    result = type_decorator.process_result_value(value, MagicMock())
    assert result is not None
    assert result.tzinfo is UTC


def test_process_result_value_converts_offset_aware_datetime_to_utc() -> None:
    type_decorator = _build_type()
    tz = timezone(timedelta(hours=-5))
    value = datetime(2026, 5, 15, 7, 0, tzinfo=tz)
    result = type_decorator.process_result_value(value, MagicMock())
    assert result is not None
    assert result.tzinfo is UTC
    assert result.hour == 12
