from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, pass_context, select_autoescape
from jinja2.runtime import Context
from markupsafe import Markup, escape
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from megaraid_dashboard.db.dao import get_maintenance_state, maintenance_writes_enabled
from megaraid_dashboard.services.action_capabilities import action_disabled_reason
from megaraid_dashboard.services.drive_actions import (
    ConsistencyCheckMode,
    PatrolReadMode,
    build_consistency_check_mode_command,
    build_consistency_check_start_command,
    build_patrol_read_mode_command,
)
from megaraid_dashboard.storcli.policy import action_refusal, load_policy, replacement_refusal

_SLOT_TOKEN_RE = re.compile(
    r"(?P<event_slot_ref>e(?P<enclosure>\d+):s(?P<slot>\d+))"
    r"|(?P<slot_context>\b(?:PD|drive|slot|slots?)\s+)(?P<slot_ref>\d+:\d+)",
    re.IGNORECASE,
)
_ContextProcessor = Callable[[Request], dict[str, Any]]
_IEC_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def create_templates(
    directory: Path,
    *,
    context_processors: list[_ContextProcessor] | None = None,
) -> Jinja2Templates:
    environment = Environment(
        loader=FileSystemLoader(directory),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["utc_to_cest"] = utc_to_cest
    environment.filters["iso_utc"] = iso_utc
    environment.filters["slot_link"] = _slot_link_filter
    environment.filters["celsius"] = celsius
    environment.filters["bytes_iec"] = bytes_iec
    processors: list[_ContextProcessor] = [_maintenance_context_processor]
    if context_processors is not None:
        processors.extend(context_processors)
    return Jinja2Templates(env=environment, context_processors=processors)


def _maintenance_context_processor(request: Request) -> dict[str, Any]:
    session_factory = cast(sessionmaker[Session], request.app.state.session_factory)
    with session_factory() as session:
        context = {
            **{
                f"{family.replace(' ', '_')}_disabled_reason": action_disabled_reason(
                    request.app.state.settings, family
                )
                for family in ("locate", "maintenance", "other writes")
            },
            "maintenance_state": get_maintenance_state(session, now=datetime.now(UTC)),
            "controller_read_only": request.app.state.settings.controller_read_only,
            "controller_writes_enabled": maintenance_writes_enabled(
                session,
                configured=request.app.state.settings.maintenance_mode,
                now=datetime.now(UTC),
            ),
        }
        context["approved_disk_group"] = None
        if (
            request.app.state.settings.controller_action_capability == "full-supported-actions"
            and context["other_writes_disabled_reason"] is None
        ):
            context["approved_disk_group"] = load_policy(
                request.app.state.settings.controller_command_policy
            ).disk_group
        context["locate_start_disabled_reason"] = context["locate_disabled_reason"]
        if context["locate_start_disabled_reason"] is None and not maintenance_writes_enabled(
            session,
            configured=request.app.state.settings.maintenance_mode,
            now=datetime.now(UTC),
        ):
            context["locate_start_disabled_reason"] = LOCATE_MAINTENANCE_REASON

        def approved_action_reason(
            path: str, mode: str | None = None, *, workflow: bool = False
        ) -> str | None:
            settings = request.app.state.settings
            if settings.controller_action_capability != "full-supported-actions":
                return None
            if context["other_writes_disabled_reason"] is not None:
                return str(context["other_writes_disabled_reason"])
            if not path.endswith("/locate/stop") and not context["controller_writes_enabled"]:
                return MAINTENANCE_REQUIRED_REASON
            if (
                "/replace/" in path or "/foreign-config/" in path
            ) and not settings.destructive_mode:
                return DESTRUCTIVE_MODE_REASON
            policy = load_policy(settings.controller_command_policy)
            if workflow:
                return replacement_refusal(policy, path)
            selected = None
            if path.endswith("/controller/consistency-check/start"):
                selected = build_consistency_check_start_command(None)
            elif path.endswith("/controller/consistency-check/mode"):
                selected = build_consistency_check_mode_command(cast(ConsistencyCheckMode, mode))
            elif path.endswith("/controller/patrol-read/mode"):
                selected = build_patrol_read_mode_command(cast(PatrolReadMode, mode))
            return action_refusal(policy, path, selected)

        context["approved_action_reason"] = approved_action_reason
        # Rollout/policy refusals shared by every action on the page: rendered once by
        # the Actions gate (partials/_actions.html) and never repeated in action rows.
        page_action_reasons: list[str] = []
        for key in (
            "maintenance_disabled_reason",
            "locate_disabled_reason",
            "other_writes_disabled_reason",
        ):
            value = context[key]
            if value and str(value) not in page_action_reasons:
                page_action_reasons.append(str(value))
        context["page_action_reasons"] = page_action_reasons
        # Page-level reasons explained by the gate or the destructive group.
        context["gate_reasons"] = [
            *page_action_reasons,
            MAINTENANCE_REQUIRED_REASON,
            LOCATE_MAINTENANCE_REASON,
            DESTRUCTIVE_MODE_REASON,
        ]
        context["destructive_mode_reason"] = (
            None if request.app.state.settings.destructive_mode else DESTRUCTIVE_MODE_REASON
        )
        return context


MAINTENANCE_REQUIRED_REASON = "Start a maintenance window before this action."
LOCATE_MAINTENANCE_REASON = "Locate Start requires an active maintenance window."
DESTRUCTIVE_MODE_REASON = "Ask the administrator to enable destructive mode before this action."


def iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("iso_utc requires a timezone-aware datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def celsius(value: int | None) -> str:
    if value is None:
        return "N/A"
    return f"{value}\u00b0C"


def bytes_iec(value: int | None) -> str:
    """Human-readable binary size: 4000787030016 -> '3.64 TiB'."""
    if value is None:
        return "Unknown"
    if abs(value) < 1024:
        return f"{value} B"
    size = float(value)
    for unit in _IEC_UNITS[1:-1]:
        size /= 1024
        if abs(size) < 1024:
            return f"{size:.2f} {unit}"
    return f"{size / 1024:.2f} {_IEC_UNITS[-1]}"


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
