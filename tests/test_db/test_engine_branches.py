from __future__ import annotations

from typing import Any

import pytest

from megaraid_dashboard.db import engine as engine_module
from megaraid_dashboard.db.engine import get_engine


def test_get_engine_skips_sqlite_specific_setup_for_non_sqlite_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_module, "_is_sqlite_url", lambda _url: False)

    captured: dict[str, Any] = {}
    real_create_engine = engine_module.create_engine

    def spy_create_engine(url: str, **kwargs: Any) -> Any:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(engine_module, "create_engine", spy_create_engine)

    engine = get_engine("sqlite:///:memory:")
    try:
        assert "connect_args" not in captured["kwargs"]
        assert "poolclass" not in captured["kwargs"]
    finally:
        engine.dispose()
