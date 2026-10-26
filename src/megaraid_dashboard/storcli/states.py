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
