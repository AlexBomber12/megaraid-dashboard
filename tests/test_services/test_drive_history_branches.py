from __future__ import annotations

from datetime import datetime

import pytest

from megaraid_dashboard.services.drive_history import (
    _optional_float,
    _require_aware_utc,
    _require_float,
    _source_chronology_rank,
)


def test_require_aware_utc_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive datetimes"):
        _require_aware_utc(datetime(2026, 5, 15, 12, 0))


def test_optional_float_returns_none_for_none() -> None:
    assert _optional_float(None) is None


def test_optional_float_returns_float_for_int() -> None:
    assert _optional_float(5) == 5.0


def test_require_float_raises_for_none() -> None:
    with pytest.raises(ValueError, match="temperature average"):
        _require_float(None)


def test_require_float_returns_value() -> None:
    assert _require_float(2.5) == 2.5


@pytest.mark.parametrize(
    ("source", "rank"),
    [("daily", 0), ("hourly", 1), ("raw", 2)],
)
def test_source_chronology_rank_known(source: str, rank: int) -> None:
    assert _source_chronology_rank(source) == rank


def test_source_chronology_rank_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown drive history source"):
        _source_chronology_rank("bogus")


def test_build_drive_history_rejects_non_positive_range_days() -> None:
    from typing import cast
    from unittest.mock import MagicMock

    from sqlalchemy.orm import Session

    from megaraid_dashboard.services import drive_history as dh

    with pytest.raises(ValueError, match="range_days must be positive"):
        dh._load_selected_history_rows(
            session=cast(Session, MagicMock()),
            enclosure_id=1,
            slot_id=1,
            current_serial_number="SN",
            range_days=0,
            now_utc=None,
        )
