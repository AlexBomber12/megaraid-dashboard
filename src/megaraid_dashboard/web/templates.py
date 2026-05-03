from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

_SLOT_TOKEN_RE = re.compile(r"\d+:\d+")


def create_templates(directory: Path) -> Jinja2Templates:
    environment = Environment(
        loader=FileSystemLoader(directory),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["utc_to_cest"] = utc_to_cest
    environment.filters["iso_utc"] = iso_utc
    environment.filters["slot_link"] = slot_link
    environment.globals["app_version"] = _app_version()
    environment.globals["build_sha"] = os.environ.get("GIT_SHA", "unknown")
    return Jinja2Templates(env=environment)


def iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("iso_utc requires a timezone-aware datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def slot_link(text: str) -> Markup:
    match = _SLOT_TOKEN_RE.search(text)
    if match is None:
        return Markup(escape(text))

    slot = match.group(0)
    before = escape(text[: match.start()])
    after = escape(text[match.end() :])
    return Markup(f'{before}<a href="/drives/{slot}">{slot}</a>{after}')


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
