from __future__ import annotations


class StorcliError(Exception):
    """Base exception for storcli wrapper failures."""


class StorcliCommandFailed(StorcliError):  # noqa: N818
    """Raised when storcli exits non-zero or returns a failure command status."""

    def __init__(self, message: str, *, err_msg: str | None = None) -> None:
        super().__init__(message)
        self.err_msg = err_msg


class StorcliNotAvailable(StorcliError):  # noqa: N818
    """Raised when storcli cannot be executed."""


class StorcliParseError(StorcliError):
    """Raised when storcli output is invalid JSON or does not match the expected schema."""
