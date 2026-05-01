from __future__ import annotations

import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Protocol

import structlog

from megaraid_dashboard.config import Settings

_LOG = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AlertMessage:
    subject: str
    body_text: str
    body_html: str | None = None


class AlertTransport(Protocol):
    def send(self, message: AlertMessage, *, to: str) -> None: ...


class SmtpAlertTransport:
    def __init__(self, settings: Settings) -> None:
        self._host = settings.alert_smtp_host
        self._port = settings.alert_smtp_port
        self._user = settings.alert_smtp_user
        self._password = settings.alert_smtp_password
        self._from_addr = settings.alert_from
        self._use_starttls = settings.alert_smtp_use_starttls

    def _build_message(self, message: AlertMessage, *, to: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = message.subject
        msg["From"] = formataddr(("MegaRAID Alerts", self._from_addr))
        msg["To"] = to
        msg["Date"] = formatdate(localtime=True)
        if "@" in self._from_addr:
            domain = self._from_addr.rsplit("@", 1)[1]
            msg["Message-ID"] = make_msgid(domain=domain)
        else:
            msg["Message-ID"] = make_msgid()
        msg.set_content(message.body_text)
        if message.body_html is not None:
            msg.add_alternative(message.body_html, subtype="html")
        return msg

    def send(self, message: AlertMessage, *, to: str) -> None:
        msg = self._build_message(message, to=to)
        start = time.monotonic()
        try:
            with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
                smtp.ehlo()
                if self._use_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(self._user, self._password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            _LOG.error(
                "alert_smtp_failed",
                subject=message.subject,
                to=to,
                error=str(exc),
                exc_info=True,
            )
            raise
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _LOG.info(
            "alert_smtp_send",
            subject=message.subject,
            to=to,
            elapsed_ms=elapsed_ms,
            starttls=self._use_starttls,
        )
