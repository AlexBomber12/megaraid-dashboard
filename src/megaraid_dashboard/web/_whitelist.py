from __future__ import annotations

WHITELIST_EXACT = frozenset({"/healthz", "/favicon.ico"})
WHITELIST_PREFIX = ("/static/",)


def is_whitelisted(path: str) -> bool:
    return path in WHITELIST_EXACT or path.startswith(WHITELIST_PREFIX)
