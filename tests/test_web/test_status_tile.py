from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from megaraid_dashboard.web.templates import create_templates

TEMPLATE_DIR = Path("src/megaraid_dashboard/templates")
ICONS_PATH = Path("src/megaraid_dashboard/static/icons.svg")
CSS_PATH = Path("src/megaraid_dashboard/static/css/app.css")
STATES = ("optimal", "warning", "critical", "info", "neutral")
ICON_NAMES = (
    "check-circle",
    "alert-triangle",
    "x-circle",
    "help-circle",
    "info",
    "bell",
    "bell-off",
    "hard-drive",
    "cpu",
    "thermometer",
    "lightbulb",
    "refresh-cw",
    "clock",
)


class StaticRequest:
    def url_for(self, _name: str, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(path=f"/static/{path}")


def render_status_tile(**context: object) -> str:
    template = create_templates(TEMPLATE_DIR).env.get_template("partials/status_tile.html")
    defaults: dict[str, object] = {
        "request": StaticRequest(),
        "label": "Controller",
        "value": "Optimal",
        "href": None,
        "icon": None,
        "aria_label": None,
        "static_asset_version": "asset123",
    }
    defaults.update(context)
    return template.render(defaults)


def test_status_tile_renders_div_with_label_value_and_state() -> None:
    rendered = render_status_tile(status="optimal")

    assert '<div\n  class="status-tile status-tile--optimal"' in rendered
    assert "Controller" in rendered
    assert "Optimal" in rendered


def test_status_tile_renders_anchor_when_href_is_provided() -> None:
    rendered = render_status_tile(status="info", href="/drives")

    assert rendered.startswith('<a\n  class="status-tile status-tile--info"')
    assert 'href="/drives"' in rendered
    assert "</a>" in rendered
    assert "</div>" not in rendered.split('<div class="status-tile__body">', maxsplit=1)[0]


def test_status_tile_renders_icon_reference() -> None:
    rendered = render_status_tile(status="neutral", icon="hard-drive")

    assert '<svg class="status-tile__icon" aria-hidden="true">' in rendered
    assert '<use href="/static/icons.svg?v=asset123#icon-hard-drive"/>' in rendered


def test_status_tile_defaults_to_neutral_status() -> None:
    rendered = render_status_tile()

    assert 'class="status-tile status-tile--neutral"' in rendered


@pytest.mark.parametrize("status", [None, ""])
def test_status_tile_coerces_falsey_status_to_neutral(status: object) -> None:
    rendered = render_status_tile(status=status)

    assert 'class="status-tile status-tile--neutral"' in rendered


def test_status_tile_renders_each_state() -> None:
    for state in STATES:
        rendered = render_status_tile(status=state)

        assert f"status-tile--{state}" in rendered


def test_icons_svg_contains_each_lucide_symbol() -> None:
    icons_svg = ICONS_PATH.read_text(encoding="utf-8")

    for icon_name in ICON_NAMES:
        assert f'id="icon-{icon_name}"' in icons_svg


def test_icons_svg_stays_under_four_kibibytes() -> None:
    assert os.path.getsize(ICONS_PATH) < 4096


def test_status_tile_css_uses_tokens_for_sizing_and_colors() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")
    status_tile_block = css.split(".status-tile {", maxsplit=1)[1].split(
        ".stat-grid {",
        maxsplit=1,
    )[0]

    assert "12px" not in status_tile_block
    assert "20px" not in status_tile_block
    assert "72px" not in status_tile_block
    assert "180px" not in status_tile_block
    assert "#" not in status_tile_block
