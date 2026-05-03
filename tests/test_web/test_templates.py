from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfoNotFoundError

import pytest

from megaraid_dashboard.web import templates


def test_iso_utc_returns_empty_string_for_none() -> None:
    assert templates.iso_utc(None) == ""


def test_iso_utc_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware datetime"):
        templates.iso_utc(datetime(2026, 4, 25, 12, 0))


def test_iso_utc_formats_aware_datetime_as_zulu_iso8601() -> None:
    formatted = templates.iso_utc(datetime(2026, 4, 25, 12, 0, tzinfo=UTC))

    assert formatted == "2026-04-25T12:00:00Z"
    assert datetime.fromisoformat(formatted.replace("Z", "+00:00")) == datetime(
        2026,
        4,
        25,
        12,
        0,
        tzinfo=UTC,
    )


def test_utc_to_cest_formats_aware_utc_datetime() -> None:
    formatted = templates.utc_to_cest(datetime(2026, 4, 25, 12, 0, tzinfo=UTC))

    assert formatted == "2026-04-25 14:00:00 CEST"


def test_utc_to_cest_treats_naive_datetime_as_utc() -> None:
    formatted = templates.utc_to_cest(datetime(2026, 4, 25, 12, 0))

    assert formatted == "2026-04-25 14:00:00 CEST"


def test_utc_to_cest_falls_back_to_utc_when_zoneinfo_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_zoneinfo(_name: str) -> object:
        raise ZoneInfoNotFoundError

    monkeypatch.setattr(templates, "ZoneInfo", missing_zoneinfo)

    formatted = templates.utc_to_cest(datetime(2026, 4, 25, 12, 0, tzinfo=UTC))

    assert formatted == "2026-04-25 12:00:00 UTC"
