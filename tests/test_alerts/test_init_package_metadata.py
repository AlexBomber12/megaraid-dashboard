from __future__ import annotations

import importlib
import importlib.metadata

import pytest

import megaraid_dashboard
import megaraid_dashboard.alerts as alerts_pkg
from megaraid_dashboard.alerts.transport import SmtpAlertTransport
from megaraid_dashboard.config import Settings, get_settings
from tests.test_config import set_required_env


def test_version_fallback_when_package_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _raise)
    importlib.reload(megaraid_dashboard)
    try:
        assert megaraid_dashboard.__version__ == "0.0.0+unknown"
    finally:
        # Restore the real version for downstream tests.
        monkeypatch.undo()
        importlib.reload(megaraid_dashboard)


def test_build_default_transport_returns_smtp_transport_using_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_env(monkeypatch)
    get_settings.cache_clear()
    try:
        transport = alerts_pkg.build_default_transport()
    finally:
        get_settings.cache_clear()

    assert isinstance(transport, SmtpAlertTransport)
    assert isinstance(get_settings(), Settings)
