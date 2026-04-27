from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape


def create_templates(directory: Path) -> Jinja2Templates:
    environment = Environment(
        loader=FileSystemLoader(directory),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["utc_to_cest"] = utc_to_cest
    return Jinja2Templates(env=environment)


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
