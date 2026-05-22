from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from megaraid_dashboard.web.templates import create_templates

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src" / "megaraid_dashboard" / "templates"


def test_drive_grid_partial_renders_tiles_in_order() -> None:
    rendered = _render_drive_grid(_tiles())

    assert rendered.count('class="drive-tile-v2 ') == 8
    assert rendered.index(">S0</span>") < rendered.index(">S1</span>")
    assert rendered.index(">S6</span>") < rendered.index(">S7</span>")


def test_drive_grid_partial_renders_warning_temperature_class() -> None:
    tiles = list(_tiles())
    tiles[3] = _tile(slot_id=3, temperature_state="warning", tile_severity="warning")

    rendered = _render_drive_grid(tiles)

    assert '<span class="drive-tile-v2__temp status-text--warning">' in rendered
    assert "57°C" in rendered
    assert "57 C" not in rendered
    assert 'class="drive-tile-v2 drive-tile-v2--warning"' in rendered


def test_drive_grid_partial_renders_critical_failed_state_class() -> None:
    tiles = list(_tiles())
    tiles[4] = _tile(slot_id=4, state_text="Failed", state_severity="critical")

    rendered = _render_drive_grid(tiles)

    assert "Failed" in rendered
    assert '<span class="drive-tile-v2__state status-text--critical">' in rendered
    assert 'class="drive-tile-v2 drive-tile-v2--critical"' in rendered


def test_drive_grid_partial_uses_worst_tile_severity() -> None:
    tiles = list(_tiles())
    tiles[5] = _tile(
        slot_id=5,
        temperature_state="warning",
        state_severity="optimal",
        tile_severity="warning",
    )

    rendered = _render_drive_grid(tiles)

    assert 'href="/drives/252:5"' in rendered
    assert 'class="drive-tile-v2 drive-tile-v2--warning"' in rendered


def _render_drive_grid(tiles: list[SimpleNamespace]) -> str:
    template = create_templates(TEMPLATE_DIR).env.get_template("partials/drive_grid.html")
    view_model = SimpleNamespace(
        drive_grid=SimpleNamespace(
            tiles=tiles,
            worst_severity="critical"
            if any(tile.tile_severity == "critical" for tile in tiles)
            else "warning"
            if any(tile.tile_severity == "warning" for tile in tiles)
            else "optimal",
        )
    )
    return template.render({"view_model": view_model})


def _tiles() -> list[SimpleNamespace]:
    return [_tile(slot_id=slot_id) for slot_id in range(8)]


def _tile(
    *,
    slot_id: int,
    state_text: str = "Onln",
    temperature_state: str = "optimal",
    state_severity: str = "optimal",
    tile_severity: str | None = None,
) -> SimpleNamespace:
    resolved_tile_severity = tile_severity or (
        "critical"
        if state_severity == "critical"
        else "warning"
        if "warning" in {temperature_state, state_severity}
        else "optimal"
    )
    return SimpleNamespace(
        slot_label=f"S{slot_id}",
        enclosure_id=252,
        slot_id=slot_id,
        temperature_celsius=40 if temperature_state == "optimal" else 57,
        temperature_state=temperature_state,
        state_text=state_text,
        state_severity=state_severity,
        tile_severity=resolved_tile_severity,
        error_badge_count=None,
        detail_url=f"/drives/252:{slot_id}",
    )
