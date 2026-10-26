"""Source inventory catches new controls even when a fixture cannot render their state."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = re.compile(
    r"<(?:button|form|input|select|a|summary)\b[^>]*>"
    r"|<[^>]+(?:hx-post|formaction|role=[\"']button)[^>]*>"
    r"|createElement\([\"'](?:button|form|input|select)[\"']\)",
    re.DOTALL,
)


def source_controls() -> dict[str, list[str]]:
    package = ROOT / "src/megaraid_dashboard"
    result = {}
    for folder, suffix in (("templates", "*.html"), ("static/js", "*.js")):
        for path in sorted((package / folder).rglob(suffix)):
            controls = [
                " ".join(match.group().split()) for match in CONTROL.finditer(path.read_text())
            ]
            if controls:
                result[str(path.relative_to(ROOT))] = controls
    return result
