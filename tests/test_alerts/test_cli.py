from __future__ import annotations

import smtplib
import ssl
from unittest.mock import MagicMock, patch

import pytest

from megaraid_dashboard.alerts import __main__ as cli
from megaraid_dashboard.config import Settings, get_settings
from tests.test_config import set_required_env


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Settings:
    set_required_env(monkeypatch)
    get_settings.cache_clear()
    yield Settings()
    get_settings.cache_clear()


def _run(argv: list[str]) -> int:
    return cli.main(argv)


def test_test_command_uses_settings_alert_to_when_no_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = MagicMock()
    settings = get_settings()
    with patch("megaraid_dashboard.alerts.build_default_transport", return_value=transport):
        rc = _run(["test"])
    assert rc == 0
    transport.send.assert_called_once()
    _, kwargs = transport.send.call_args
    assert kwargs["to"] == settings.alert_to
    out = capsys.readouterr().out
    assert f"Sent test alert to {settings.alert_to}" in out


def test_test_command_with_to_override(capsys: pytest.CaptureFixture[str]) -> None:
    transport = MagicMock()
    with patch("megaraid_dashboard.alerts.build_default_transport", return_value=transport):
        rc = _run(["test", "--to", "other@example.com"])
    assert rc == 0
    transport.send.assert_called_once()
    _, kwargs = transport.send.call_args
    assert kwargs["to"] == "other@example.com"
    out = capsys.readouterr().out
    assert "Sent test alert to other@example.com" in out


def test_authentication_error_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    transport = MagicMock()
    transport.send.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
    with patch("megaraid_dashboard.alerts.build_default_transport", return_value=transport):
        rc = _run(["test"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Authentication failed" in err


def test_generic_smtp_exception_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    transport = MagicMock()
    transport.send.side_effect = smtplib.SMTPException("boom")
    with patch("megaraid_dashboard.alerts.build_default_transport", return_value=transport):
        rc = _run(["test"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.strip() != ""


def test_ssl_error_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    transport = MagicMock()
    transport.send.side_effect = ssl.SSLError("tls failed")
    with patch("megaraid_dashboard.alerts.build_default_transport", return_value=transport):
        rc = _run(["test"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "TLS error" in err


def test_os_error_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    transport = MagicMock()
    transport.send.side_effect = TimeoutError("connect timed out")
    with patch("megaraid_dashboard.alerts.build_default_transport", return_value=transport):
        rc = _run(["test"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Connection error" in err
    assert "TimeoutError" in err


def test_unknown_subcommand_exits_two() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run(["bogus"])
    assert exc_info.value.code == 2


def test_no_subcommand_exits_two() -> None:
    rc = _run([])
    assert rc == 2


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run(["--help"])
    assert exc_info.value.code == 0
