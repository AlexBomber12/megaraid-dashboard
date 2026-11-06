"""Semantic state comparison; keep the original firmware spelling in stored evidence."""

from __future__ import annotations

_ALIASES = {
    "optl": "optimal",
    "msng": "missing",
    "offln": "offline",
    "ofln": "offline",
    "dgrd": "degraded",
    "pdgd": "partially degraded",
}


def normalized_state(state: str) -> str:
    normalized = state.strip().casefold()
    return _ALIASES.get(normalized, normalized)


def normalize_alarm_state(value: str | None) -> str:
    """Interpret known Alarm spellings without changing stored firmware evidence."""
    normalized = (value or "").strip().casefold()
    if normalized in {"on", "enabled", "enable"}:
        return "On"
    if normalized in {"silenced", "silence", "mute", "muted"}:
        return "Silenced"
    if normalized in {"off", "disabled", "disable"}:
        return "Off"
    return "Unknown"
