"""Shared presentation formats; never change stored measurements or event messages."""

from __future__ import annotations


def celsius(value: int | float | None) -> str:
    """Format a Celsius measurement consistently for operator displays."""
    if value is None:
        return "N/A"
    return f"{value:g}°C"
