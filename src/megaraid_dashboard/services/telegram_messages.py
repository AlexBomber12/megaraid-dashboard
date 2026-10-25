"""Russian, human-readable Telegram alert text.

Each message says what happened, the risk and one human action; machine identifiers
(``VD 0``, ``PD e252:s4``, storcli state codes) appear only in the "Детали" line, and every
message links to the Obsidian runbook.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.notifier import _format_europe_rome, _to_aware_utc

RUNBOOK_URL = (
    "https://github.com/AlexBomber12/knowledge-vault/blob/main/20-homelab/notifications/megaraid.md"
)
_SUMMARY_MAX_LENGTH = 500
_VD_UNAVAILABLE_STATES = frozenset({"failed", "offline", "offln", "ofln"})
_TEST_PREFIX = "🧪 ТЕСТ: проверочное сообщение, реальной аварии нет.\n\n"


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
        )
    )


@dataclass(frozen=True)
class _Explanation:
    headline: str
    what: str
    risk: str
    action: str


_ACTION_DEFAULT = (
    "ничего не вынимать и не перезагружать сервер наугад. "
    "Перешли это сообщение в ChatGPT для проверки."
)
_ACTION_COOLING = (
    "проверить охлаждение и вентиляторы сервера, ничего не отключать наугад. "
    "Перешли это сообщение в ChatGPT для проверки."
)
_RISK_DRIVE_WEAR = "Это признак износа или повреждения: диск может скоро отказать."

_EXPLANATIONS: dict[tuple[str, str], _Explanation] = {
    ("pd_state", "critical"): _Explanation(
        headline="диск RAID отказал или пропал",
        what="Физический диск перестал нормально работать в массиве.",
        risk="Массив может остаться без резерва: при отказе ещё одного диска данные пропадут.",
        action=_ACTION_DEFAULT,
    ),
    ("media_errors", "critical"): _Explanation(
        headline="диск сообщает об ошибках носителя",
        what="Диск зафиксировал ошибки чтения или записи поверхности (media errors).",
        risk=_RISK_DRIVE_WEAR,
        action=_ACTION_DEFAULT,
    ),
    ("predictive_failures", "critical"): _Explanation(
        headline="диск предсказывает свой отказ",
        what="Диск сообщил о прогнозируемом отказе (predictive failure).",
        risk=_RISK_DRIVE_WEAR,
        action=_ACTION_DEFAULT,
    ),
    ("smart_alert", "critical"): _Explanation(
        headline="диск подал SMART-предупреждение",
        what="Самодиагностика диска (SMART) сообщила о проблеме.",
        risk=_RISK_DRIVE_WEAR,
        action=_ACTION_DEFAULT,
    ),
    ("cv_state", "critical"): _Explanation(
        headline="неисправна защита кэша RAID-контроллера (CacheVault)",
        what=(
            "Модуль CacheVault, который сохраняет кэш контроллера при отключении питания, "
            "отсутствует, неисправен или требует замены."
        ),
        risk="При внезапном отключении питания незаписанные данные из кэша могут потеряться.",
        action=_ACTION_DEFAULT,
    ),
    ("temperature", "critical"): _Explanation(
        headline="критическая температура диска",
        what="Температура диска превысила критический порог.",
        risk="Перегрев может вывести диск из строя и повредить данные.",
        action=_ACTION_COOLING,
    ),
    ("temperature", "warning"): _Explanation(
        headline="диск долго перегревается",
        what="Температура диска устойчиво держится выше порога предупреждения.",
        risk="Длительный перегрев сокращает срок службы диска.",
        action=_ACTION_COOLING,
    ),
    ("controller_temperature", "critical"): _Explanation(
        headline="критическая температура RAID-контроллера",
        what="Процессор RAID-контроллера нагрелся выше критического порога.",
        risk=(
            "Перегретый контроллер может начать сбоить или отключиться, и массив станет недоступен."
        ),
        action=_ACTION_COOLING,
    ),
    ("controller_temperature", "warning"): _Explanation(
        headline="RAID-контроллер долго перегревается",
        what="Температура процессора RAID-контроллера устойчиво выше порога предупреждения.",
        risk="Длительный перегрев сокращает срок службы контроллера и может привести к сбоям.",
        action=_ACTION_COOLING,
    ),
    ("system", "critical"): _Explanation(
        headline="мониторинг RAID не получает данные",
        what=(
            "Сборщик несколько раз подряд не смог опросить RAID-контроллер. "
            "Текущее состояние дисков НЕИЗВЕСТНО — это не означает, что диски сломались."
        ),
        risk="Пока сбор данных не восстановится, оповещение о настоящей аварии не придёт.",
        action=(
            "проверить службу megaraid-dashboard и доступность storcli; команды, меняющие "
            "RAID, не выполнять. Перешли это сообщение в ChatGPT для проверки."
        ),
    ),
}
_VD_DEGRADED = _Explanation(
    headline="дисковый массив потерял защиту от отказа",
    what=(
        "Один из дисков массива не работает нормально. Данные пока могут открываться, "
        "но запаса надёжности больше нет."
    ),
    risk="Отказ ещё одного диска может привести к потере данных.",
    action=_ACTION_DEFAULT,
)
_VD_UNAVAILABLE = _Explanation(
    headline="дисковый массив RAID недоступен",
    what="Виртуальный массив перешёл в нерабочее состояние.",
    risk="Данные на нём сейчас могут быть недоступны; есть риск их потери.",
    action=_ACTION_DEFAULT,
)
_GENERIC = _Explanation(
    headline="событие RAID требует внимания",
    what="Контроллер RAID сообщил о проблеме.",
    risk="Возможен риск для данных.",
    action=_ACTION_DEFAULT,
)


def build_telegram_text(event: Event, *, hostname: str) -> str:
    explanation = _explain(event)
    if event.severity == "critical":
        header = f"🔴 СРОЧНО — {hostname}: {explanation.headline}."
    else:
        header = f"🟠 ВНИМАНИЕ — {hostname}: {explanation.headline}."
    occurred_utc = _to_aware_utc(event.occurred_at)
    occurred = (
        f"{occurred_utc.strftime('%Y-%m-%d %H:%M:%S UTC')} ({_format_europe_rome(occurred_utc)})"
    )
    details = "; ".join(
        (
            event.subject,
            event.summary[:_SUMMARY_MAX_LENGTH],
            f"событие #{event.id}",
            occurred,
        )
    )
    return (
        f"{header}\n"
        "\n"
        f"{explanation.what} {explanation.risk}\n"
        "\n"
        f"Что делать: {explanation.action}\n"
        "\n"
        f"Детали: {details}\n"
        f"Инструкция: {RUNBOOK_URL}"
    )


def build_test_telegram_text(*, hostname: str) -> str:
    """Render a synthetic degraded-array alert; the event is never stored."""
    event = Event(
        id=0,
        occurred_at=datetime.now(UTC),
        severity="critical",
        category="vd_state",
        subject="VD 0",
        summary="SYNTHETIC TEST: VD 0 state changed from Optl to Dgrd",
        after_json={"state": "Dgrd"},
    )
    return _TEST_PREFIX + build_telegram_text(event, hostname=hostname)


def _explain(event: Event) -> _Explanation:
    if event.category == "vd_state":
        if incident_state(event) in _VD_UNAVAILABLE_STATES:
            return _VD_UNAVAILABLE
        return _VD_DEGRADED
    return _EXPLANATIONS.get((event.category, event.severity), _GENERIC)
