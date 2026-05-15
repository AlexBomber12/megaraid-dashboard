from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.requests import Request

from megaraid_dashboard.web.templates import create_templates


def test_create_templates_appends_extra_context_processors(tmp_path: Path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()

    extra_calls: list[Request] = []

    def extra_processor(request: Request) -> dict[str, Any]:
        extra_calls.append(request)
        return {"extra_key": "extra_value"}

    templates = create_templates(template_dir, context_processors=[extra_processor])

    assert extra_processor in templates.context_processors
    assert templates.context_processors[-1] is extra_processor
