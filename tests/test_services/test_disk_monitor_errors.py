from __future__ import annotations

from datetime import datetime

import pytest

from megaraid_dashboard.services.disk_monitor import _require_aware_utc


def test_require_aware_utc_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive datetimes are not allowed"):
        _require_aware_utc(datetime(2026, 5, 15, 12, 0))
