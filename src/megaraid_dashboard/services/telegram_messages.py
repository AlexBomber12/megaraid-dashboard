"""Concise Russian Telegram text, derived only from structured event fields.

Formatting never determines eligibility, incident identity, recurrence or recovery.
The transport adds the fixed runbook button; summaries and machine identifiers stay
in the event store for diagnostic lookup by hostname, occurrence time and event ID.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.notifier import _to_aware_utc

_VD_DEGRADED_STATES = frozenset({"degraded", "dgrd", "partially degraded", "pdgd"})
_VD_UNAVAILABLE_STATES = frozenset({"failed", "offline", "offln", "ofln"})
_PD_HEADLINES = {
    "failed": "отказ диска.",
    "missing": "диск не обнаружен.",
    "msng": "диск не обнаружен.",
    "offline": "диск в состоянии «офлайн».",
    "offln": "диск в состоянии «офлайн».",
}
_CV_UNHEALTHY_STATES = frozenset({"degraded", "dgrd", "failed", "unhealthy", "faulty"})
_FALLBACK = "событие RAID, данные неполны."
_ACTION = "Перешли в ChatGPT."
_DISK_ACTION = f"{_ACTION} Диски не трогай."


def is_monitoring_outage(event: Event) -> bool:
    """Only the collector's explicit outage event may describe monitoring as unavailable."""
    return (
        event.category == "system"
        and event.subject == "Controller"
        and (event.after_json or {}).get("monitoring_outage") is True
    )


def incident_state(event: Event) -> str | None:
    """Normalized object state after the event, used to tell a repeat from a worse state."""
    after = event.after_json
    if isinstance(after, dict):
        state = after.get("state")
        if isinstance(state, str):
            return state.strip().casefold()
    return None


def incident_key(event: Event) -> tuple[tuple[str, bool | str | None], ...]:
    """Identify the incident an event belongs to, to tell a repeat from a new failure.

    The object state wins when present. Otherwise flag fields such as ``present`` or
    ``replacement_required`` name the failure, while numeric fields (error counters) keep
    only their name so that a growing counter stays one incident.
    """
    state = incident_state(event)
    if state is not None:
        return (("state", state),)
    after = event.after_json if isinstance(event.after_json, dict) else {}
    return tuple(
        sorted(
            (str(name), value if isinstance(value, bool | str) else None)
            for name, value in after.items()
            if name != "serial_number"
        )
    )


def build_telegram_text(event: Event, *, hostname: str) -> str:
    """Render at most two content lines and one Europe/Rome diagnostic footer."""
    headline, protect_disks = _explain(event)
    marker = "🔴" if event.severity == "critical" else "🟠"
    occurred = _to_aware_utc(event.occurred_at).astimezone(ZoneInfo("Europe/Rome"))
    footer = f"{occurred:%d.%m %H:%M} · №{event.id}"
    action = _DISK_ACTION if protect_disks else _ACTION
    return f"{marker} {hostname}: {headline}\n{action}\n{footer}"


def build_test_telegram_text(*, hostname: str) -> str:
    """An independent manual delivery check, without a synthetic incident or event ID."""
    return f"🧪 {hostname}: тест уведомлений\nДействий не требуется."


def _explain(event: Event) -> tuple[str, bool]:
    after = event.after_json if isinstance(event.after_json, dict) else {}
    state = incident_state(event)
    if event.category == "vd_state":
        if state in _VD_DEGRADED_STATES:
            return "сбой массива, риск потери данных.", True
        if state in _VD_UNAVAILABLE_STATES:
            return "массив недоступен.", True
    elif event.category == "pd_state":
        headline = _PD_HEADLINES.get(state or "")
        if headline is not None:
            return headline, True
    elif event.category in {"media_errors", "predictive_failures"}:
        value = after.get(event.category)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            headline = (
                "ошибки носителя диска."
                if event.category == "media_errors"
                else "диск сообщает о вероятном отказе."
            )
            return headline, True
    elif event.category == "smart_alert":
        if after.get("smart_alert") is True:
            return "предупреждение самодиагностики диска.", True
    elif event.category == "cv_state":
        if after.get("present") is False:
            return "защита кэша не обнаружена.", False
        if after.get("replacement_required") is True:
            return "защита кэша требует замены.", False
        if state in _CV_UNHEALTHY_STATES:
            return "сбой защиты кэша.", False
    elif event.category in {"temperature", "controller_temperature"}:
        field = (
            "temperature_celsius" if event.category == "temperature" else "roc_temperature_celsius"
        )
        value = after.get(field)
        # Saved measurements use integral Celsius. Reject bools, strings and implausible
        # values rather than turning malformed metadata or summary text into a reading.
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 200:
            subject = "диска" if event.category == "temperature" else "контроллера"
            level = "критическая температура" if event.severity == "critical" else "перегрев"
            return f"{level} {subject}: {value} °C.", False
    elif is_monitoring_outage(event):
        return "мониторинг недоступен, состояние RAID неизвестно.", False
    return _FALLBACK, False
