from __future__ import annotations

from megaraid_dashboard.alerts.transport import (
    AlertMessage,
    AlertTransport,
    SmtpAlertTransport,
)

__all__ = [
    "AlertMessage",
    "AlertTransport",
    "SmtpAlertTransport",
    "build_default_transport",
]


def build_default_transport() -> SmtpAlertTransport:
    from megaraid_dashboard.config import get_settings

    return SmtpAlertTransport(get_settings())
