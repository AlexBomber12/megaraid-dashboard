from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from megaraid_dashboard.services.drive_detail import BackplaneSlot


class _BackplaneDiagramParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.container_classes: list[str] = []
        self.slot_links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        if tag == "div" and "backplane-diagram" in attr_map.get("class", ""):
            self.container_classes.append(attr_map["class"])
        if tag == "a" and "backplane-slot" in attr_map.get("class", ""):
            self.slot_links.append(attr_map)


def test_backplane_diagram_renders_eight_slots() -> None:
    parsed = _parse(_render(slots=_slots()))

    assert len(parsed.slot_links) == 8


def test_backplane_diagram_marks_current_slot() -> None:
    parsed = _parse(_render(slots=_slots(current_slot=3)))

    assert "backplane-slot--this" in parsed.slot_links[3]["class"]


def test_backplane_diagram_uses_severity_tint() -> None:
    parsed = _parse(_render(slots=_slots(critical_slot=5)))

    assert "backplane-slot--crit" in parsed.slot_links[5]["class"]


def test_backplane_diagram_compact_variant_marks_container() -> None:
    parsed = _parse(_render(slots=_slots(), compact=True))

    assert parsed.container_classes == ["backplane-diagram backplane-diagram--compact"]


def test_backplane_diagram_links_each_slot_to_detail_url() -> None:
    parsed = _parse(_render(slots=_slots()))

    expected_urls = [f"/drives/252:{slot}" for slot in range(8)]

    assert [link["href"] for link in parsed.slot_links] == expected_urls


def test_backplane_diagram_empty_list_renders_empty_container() -> None:
    parsed = _parse(_render(slots=[]))

    assert parsed.container_classes == ["backplane-diagram"]
    assert parsed.slot_links == []


def _render(*, slots: list[BackplaneSlot], compact: bool | None = None) -> str:
    template = _environment().get_template("partials/backplane_diagram.html")
    context: dict[str, object] = {"slots": slots}
    if compact is not None:
        context["compact"] = compact
    return template.render(context)


def _slots(*, current_slot: int = 4, critical_slot: int | None = None) -> list[BackplaneSlot]:
    return [
        BackplaneSlot(
            slot_label=str(slot),
            enclosure_id=252,
            slot_id=slot,
            is_this=slot == current_slot,
            severity="critical" if slot == critical_slot else "optimal",
            detail_url=f"/drives/252:{slot}",
        )
        for slot in range(8)
    ]


def _parse(html: str) -> _BackplaneDiagramParser:
    parser = _BackplaneDiagramParser()
    parser.feed(html)
    return parser


def _environment() -> Environment:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "megaraid_dashboard" / "templates"
    return Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
