from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfoNotFoundError

import pytest

from megaraid_dashboard.services import notifier as notifier_module
from megaraid_dashboard.services.notifier import _format_europe_rome, _to_aware_utc


def test_to_aware_utc_attaches_utc_for_naive() -> None:
    result = _to_aware_utc(datetime(2026, 5, 15, 12, 0))
    assert result.tzinfo is UTC


def test_to_aware_utc_converts_aware_to_utc() -> None:
    from datetime import timedelta, timezone

    value = datetime(2026, 5, 15, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    result = _to_aware_utc(value)
    assert result.tzinfo is UTC
    assert result.hour == 12


def test_format_europe_rome_falls_back_when_zoneinfo_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BadZoneInfo:
        def __init__(self, key: str) -> None:
            raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr(notifier_module, "ZoneInfo", _BadZoneInfo)
    value = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    text = _format_europe_rome(value)
    assert "2026-05-15 12:00:00" in text


def test_format_europe_rome_normal_path_returns_string() -> None:
    text = _format_europe_rome(datetime(2026, 5, 15, 12, 0, tzinfo=UTC))
    assert "2026-05-15" in text
