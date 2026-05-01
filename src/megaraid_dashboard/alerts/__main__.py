from __future__ import annotations

import argparse
import smtplib
import socket
import ssl
import sys
from collections.abc import Callable

from megaraid_dashboard.alerts.transport import AlertMessage


def _handle_test(args: argparse.Namespace) -> int:
    from megaraid_dashboard.alerts import build_default_transport
    from megaraid_dashboard.config import get_settings

    settings = get_settings()
    to_address: str = args.to if args.to else settings.alert_to
    hostname = socket.gethostname()
    message = AlertMessage(
        subject="[megaraid-dashboard] SMTP test",
        body_text=(
            "This is a test message from the megaraid-dashboard CLI test command.\n"
            f"Sender: {settings.alert_from}\n"
            f"Host: {hostname}\n"
            "It is safe to ignore.\n"
        ),
        body_html=None,
    )
    transport = build_default_transport()
    try:
        transport.send(message, to=to_address)
    except smtplib.SMTPAuthenticationError:
        print(
            "Authentication failed: check ALERT_SMTP_USER and ALERT_SMTP_PASSWORD",
            file=sys.stderr,
        )
        return 1
    except smtplib.SMTPException as exc:
        print(f"SMTP error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except ssl.SSLError as exc:
        print(f"TLS error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Connection error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Sent test alert to {to_address}")
    return 0


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "test": _handle_test,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m megaraid_dashboard.alerts")
    subparsers = parser.add_subparsers(dest="command")
    test_parser = subparsers.add_parser("test", help="Send a single SMTP test message.")
    test_parser.add_argument(
        "--to",
        default=None,
        help="Override the recipient address (defaults to ALERT_TO).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = HANDLERS.get(args.command) if args.command else None
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
