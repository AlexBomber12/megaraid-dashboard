from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, pass_context, select_autoescape
from jinja2.runtime import Context
from markupsafe import Markup, escape

_SLOT_TOKEN_RE = re.compile(
    r"(?P<event_slot_ref>e(?P<enclosure>\d+):s(?P<slot>\d+))"
    r"|(?P<slot_context>\b(?:PD|drive|slot|slots?)\s+)(?P<slot_ref>\d+:\d+)",
    re.IGNORECASE,
)


def create_templates(directory: Path) -> Jinja2Templates:
    environment = Environment(
        loader=FileSystemLoader(directory),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["utc_to_cest"] = utc_to_cest
    environment.filters["iso_utc"] = iso_utc
    environment.filters["slot_link"] = _slot_link_filter
    environment.globals["app_version"] = _app_version()
    environment.globals["build_sha"] = os.environ.get("GIT_SHA", "unknown")
    return Jinja2Templates(env=environment)


def iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("iso_utc requires a timezone-aware datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pass_context
def _slot_link_filter(context: Context, text: str) -> Markup:
    request = context["request"]

    def slot_url(slot_ref: str) -> str:
        return str(request.url_for("drive_detail_slot_ref", slot_ref=slot_ref).path)

    return slot_link(text, slot_url=slot_url)


def slot_link(text: str, *, slot_url: Callable[[str], str] | None = None) -> Markup:
    match = _SLOT_TOKEN_RE.search(text)
    if match is None:
        return Markup(escape(text))

    slot_ref = _slot_ref(match)
    href = escape(_default_slot_url(slot_ref) if slot_url is None else slot_url(slot_ref))
    label_start, label_end = _slot_label_span(match)
    label = escape(text[label_start:label_end])
    before = escape(text[:label_start])
    after = escape(text[label_end:])
    return Markup(f'{before}<a href="{href}">{label}</a>{after}')


def _slot_ref(match: re.Match[str]) -> str:
    slot_ref = match.group("slot_ref")
    if slot_ref is not None:
        return slot_ref
    return f"{match.group('enclosure')}:{match.group('slot')}"


def _slot_label_span(match: re.Match[str]) -> tuple[int, int]:
    slot_ref = match.group("slot_ref")
    if slot_ref is not None:
        return match.start("slot_ref"), match.end("slot_ref")
    return match.start("event_slot_ref"), match.end("event_slot_ref")


def _default_slot_url(slot_ref: str) -> str:
    return f"/drives/{slot_ref}"


# Deprecated: prefer iso_utc + JS local-time. Remove after all templates migrate.
def utc_to_cest(value: datetime) -> str:
    utc_value = _to_aware_utc(value)
    try:
        localized = utc_value.astimezone(ZoneInfo("Europe/Rome"))
    except ZoneInfoNotFoundError:
        localized = utc_value
    return localized.strftime("%Y-%m-%d %H:%M:%S %Z")


def _to_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _app_version() -> str:
    try:
        return version("megaraid-dashboard")
    except PackageNotFoundError:
        return "dev"
