from __future__ import annotations

import smtplib
import ssl
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from megaraid_dashboard.alerts import __main__ as cli
from megaraid_dashboard.alerts.telegram import TelegramSendError, TelegramTransport
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


_FAKE_TELEGRAM_TOKEN = "123456:TEST-FAKE-TOKEN-NOT-A-REAL-SECRET-000"


def _telegram_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "telegram_bot_token"
    token_file.write_text(_FAKE_TELEGRAM_TOKEN)
    token_file.chmod(0o600)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")
    get_settings.cache_clear()


def test_telegram_test_sends_synthetic_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _telegram_env(monkeypatch, tmp_path)
    sent: list[tuple[str, str]] = []

    def fake_send(self: TelegramTransport, text: str) -> None:
        sent.append((self.chat_id, text))

    monkeypatch.setattr(TelegramTransport, "send", fake_send)

    rc = _run(["telegram-test"])

    assert rc == 0
    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "-1001234567890"
    assert text.startswith("🧪")
    assert "SYNTHETIC TEST" in text
    out = capsys.readouterr().out
    assert out == "Sent Telegram test alert to chat -1001234567890\n"
    assert _FAKE_TELEGRAM_TOKEN not in out


def test_telegram_test_chat_id_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _telegram_env(monkeypatch, tmp_path)
    chat_ids: list[str] = []
    monkeypatch.setattr(
        TelegramTransport,
        "send",
        lambda self, text: chat_ids.append(self.chat_id),
    )

    rc = _run(["telegram-test", "--chat-id", "-1009999999999"])

    assert rc == 0
    assert chat_ids == ["-1009999999999"]


def test_telegram_test_reports_config_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", "")
    get_settings.cache_clear()

    rc = _run(["telegram-test", "--chat-id", "-1009999999999"])

    assert rc == 1
    assert "Telegram configuration error: TELEGRAM_BOT_TOKEN_FILE is not set" in (
        capsys.readouterr().err
    )


def test_telegram_test_reports_send_error_without_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _telegram_env(monkeypatch, tmp_path)

    def failing_send(self: TelegramTransport, text: str) -> None:
        del self, text
        raise TelegramSendError("HTTP 401: Unauthorized")

    monkeypatch.setattr(TelegramTransport, "send", failing_send)

    rc = _run(["telegram-test"])

    assert rc == 1
    err = capsys.readouterr().err
    assert err == "Telegram error: HTTP 401: Unauthorized\n"
    assert _FAKE_TELEGRAM_TOKEN not in err
