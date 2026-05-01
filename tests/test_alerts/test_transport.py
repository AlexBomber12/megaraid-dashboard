from __future__ import annotations

import dataclasses
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from megaraid_dashboard.alerts.transport import (
    AlertMessage,
    SmtpAlertTransport,
)
from megaraid_dashboard.config import Settings
from tests.test_config import set_required_env


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    set_required_env(monkeypatch)
    return Settings()


def _patch_smtp() -> tuple[MagicMock, MagicMock]:
    smtp_class = MagicMock(name="smtplib.SMTP")
    smtp_instance = MagicMock(name="smtp_instance")
    smtp_class.return_value.__enter__.return_value = smtp_instance
    smtp_class.return_value.__exit__.return_value = False
    return smtp_class, smtp_instance


def _captured_message(smtp_instance: MagicMock) -> EmailMessage:
    smtp_instance.send_message.assert_called_once()
    args, _ = smtp_instance.send_message.call_args
    msg = args[0]
    assert isinstance(msg, EmailMessage)
    return msg


def test_alert_message_is_frozen() -> None:
    message = AlertMessage(subject="s", body_text="b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        message.subject = "other"  # type: ignore[misc]


def test_alert_message_text_only_is_single_part(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    message = AlertMessage(subject="s", body_text="b")
    smtp_class, smtp_instance = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(message, to="to@example.test")
    msg = _captured_message(smtp_instance)
    assert not msg.is_multipart()


def test_alert_message_with_html_is_multipart_alternative(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    message = AlertMessage(subject="s", body_text="b", body_html="<p>b</p>")
    smtp_class, smtp_instance = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(message, to="to@example.test")
    msg = _captured_message(smtp_instance)
    assert msg.is_multipart()
    assert msg.get_content_type() == "multipart/alternative"


def test_send_opens_smtp_with_settings(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, _ = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")
    smtp_class.assert_called_once_with(
        settings.alert_smtp_host, settings.alert_smtp_port, timeout=30
    )


def test_send_with_starttls_calls_in_order(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")
    method_names = [name for name, _, _ in smtp_instance.method_calls]
    assert method_names == ["ehlo", "starttls", "ehlo", "login", "send_message"]


def test_send_without_starttls_skips_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("ALERT_SMTP_USE_STARTTLS", "false")
    settings = Settings()
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")
    method_names = [name for name, _, _ in smtp_instance.method_calls]
    assert "starttls" not in method_names
    assert method_names == ["ehlo", "login", "send_message"]


def test_send_login_uses_settings_credentials(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")
    smtp_instance.login.assert_called_once_with(
        settings.alert_smtp_user, settings.alert_smtp_password
    )


def test_message_from_header_uses_display_name(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")
    msg = _captured_message(smtp_instance)
    assert msg["From"] == f"MegaRAID Alerts <{settings.alert_from}>"


def test_message_to_header_uses_argument_not_settings(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    recipient = "explicit@example.test"
    with patch("smtplib.SMTP", smtp_class):
        transport.send(AlertMessage(subject="s", body_text="b"), to=recipient)
    msg = _captured_message(smtp_instance)
    assert msg["To"] == recipient
    assert msg["To"] != settings.alert_to


def test_message_has_date_and_message_id_headers(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")
    msg = _captured_message(smtp_instance)
    assert msg["Date"]
    assert msg["Message-ID"]
    domain = settings.alert_from.rsplit("@", 1)[1]
    assert msg["Message-ID"].endswith(f"@{domain}>")


def test_send_reraises_authentication_error(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    smtp_instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
    with patch("smtplib.SMTP", smtp_class), pytest.raises(smtplib.SMTPAuthenticationError):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")


def test_send_reraises_generic_smtp_exception(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    smtp_instance.send_message.side_effect = smtplib.SMTPException("boom")
    with patch("smtplib.SMTP", smtp_class), pytest.raises(smtplib.SMTPException):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")


def test_send_reraises_ssl_error(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    smtp_instance.starttls.side_effect = ssl.SSLError("tls failed")
    with patch("smtplib.SMTP", smtp_class), pytest.raises(ssl.SSLError):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")


def test_send_reraises_os_error_on_connect(settings: Settings) -> None:
    transport = SmtpAlertTransport(settings)
    smtp_class = MagicMock(name="smtplib.SMTP", side_effect=TimeoutError("connect timed out"))
    with patch("smtplib.SMTP", smtp_class), pytest.raises(TimeoutError):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")


def test_send_message_id_falls_back_when_from_has_no_at(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("ALERT_FROM", "noreply-no-at")
    settings = Settings()
    transport = SmtpAlertTransport(settings)
    smtp_class, smtp_instance = _patch_smtp()
    with patch("smtplib.SMTP", smtp_class):
        transport.send(AlertMessage(subject="s", body_text="b"), to="to@example.test")
    msg = _captured_message(smtp_instance)
    message_id: Any = msg["Message-ID"]
    assert message_id
    assert "@" in message_id
