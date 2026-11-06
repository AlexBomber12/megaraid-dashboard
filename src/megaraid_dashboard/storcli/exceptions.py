from __future__ import annotations

from typing import Any


class StorcliError(Exception):
    """Base exception for storcli wrapper failures."""

    # False is proof that preflight refused before dispatch; None remains uncertain.
    write_sent: bool | None = None


class StorcliCommandFailed(StorcliError):  # noqa: N818
    """Raised when storcli exits non-zero or returns a failure command status."""

    def __init__(self, message: str, *, err_msg: str | None = None) -> None:
        super().__init__(message)
        self.err_msg = err_msg


class StorcliNotAvailable(StorcliError):  # noqa: N818
    """Raised when storcli cannot be executed."""


class StorcliParseError(StorcliError):
    """Raised when storcli output is invalid JSON or does not match the expected schema."""


class StorcliResponseFailed(StorcliCommandFailed):
    """Nonzero process with validated failed JSON; still a command failure.

    Payload is private evidence, deliberately excluded from exception text/repr.
    Only callers with an explicit optional-read policy may inspect it.
    """

    def __init__(self, exit_code: int, payload: dict[str, Any]) -> None:
        super().__init__(f"storcli exited with code {exit_code}: controller reported Failure")
        self.exit_code = exit_code
        self.payload = payload
