from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
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


def test_celsius_formats_temperature_with_degree_symbol() -> None:
    assert templates.celsius(42) == "42°C"


def test_celsius_returns_na_for_none() -> None:
    assert templates.celsius(None) == "N/A"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "Unknown"),
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.00 KiB"),
        (5 * 1024**2, "5.00 MiB"),
        (4000787030016, "3.64 TiB"),
        (1024**5, "1.00 PiB"),
        (2048 * 1024**5, "2048.00 PiB"),
    ],
)
def test_bytes_iec_formats_binary_sizes(value: int | None, expected: str) -> None:
    assert templates.bytes_iec(value) == expected


def test_bytes_iec_is_registered_as_template_filter(tmp_path: Path) -> None:
    (tmp_path / "size.html").write_text("{{ size | bytes_iec }}", encoding="utf-8")
    environment = templates.create_templates(tmp_path).env

    assert environment.get_template("size.html").render(size=4000787030016) == "3.64 TiB"


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
