from __future__ import annotations

import pytest
from pydantic import ValidationError

from megaraid_dashboard.config import Settings
from tests.test_config import set_required_env


def test_metrics_listen_address_must_not_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("METRICS_LISTEN_ADDRESS", " ")

    with pytest.raises(ValidationError, match="metrics_listen_address must not be empty"):
        Settings()
