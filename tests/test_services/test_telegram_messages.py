from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.telegram_messages import (
    RUNBOOK_URL,
    build_telegram_text,
    build_test_telegram_text,
    incident_state,
)

OCCURRED_AT = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _event(
    *,
    category: str,
    severity: str = "critical",
    subject: str = "PD e252:s4",
    summary: str = "machine summary",
    after: dict[str, Any] | None = None,
) -> Event:
    return Event(
        id=42,
        occurred_at=OCCURRED_AT,
        severity=severity,
        category=category,
        subject=subject,
        summary=summary,
        after_json=after,
    )


@pytest.mark.parametrize(
    ("category", "severity", "after", "headline"),
    [
        ("vd_state", "critical", {"state": "Dgrd"}, "дисковый массив потерял защиту от отказа"),
        ("vd_state", "critical", {"state": "Pdgd"}, "дисковый массив потерял защиту от отказа"),
        ("vd_state", "critical", {"state": "OfLn"}, "дисковый массив RAID недоступен"),
        ("vd_state", "critical", {"state": " Failed "}, "дисковый массив RAID недоступен"),
        ("pd_state", "critical", {"state": "Failed"}, "диск RAID отказал или пропал"),
        ("media_errors", "critical", None, "диск сообщает об ошибках носителя"),
        ("predictive_failures", "critical", None, "диск предсказывает свой отказ"),
        ("smart_alert", "critical", None, "диск подал SMART-предупреждение"),
        ("cv_state", "critical", None, "неисправна защита кэша RAID-контроллера (CacheVault)"),
        ("temperature", "critical", None, "критическая температура диска"),
        ("temperature", "warning", None, "диск долго перегревается"),
        ("controller_temperature", "critical", None, "критическая температура RAID-контроллера"),
        ("controller_temperature", "warning", None, "RAID-контроллер долго перегревается"),
        ("system", "critical", None, "мониторинг RAID не получает данные"),
        ("other_errors", "warning", None, "событие RAID требует внимания"),
    ],
)
def test_build_telegram_text_explains_each_alert_in_russian(
    category: str,
    severity: str,
    after: dict[str, Any] | None,
    headline: str,
) -> None:
    text = build_telegram_text(
        _event(category=category, severity=severity, after=after),
        hostname="server",
    )

    first_line = text.splitlines()[0]
    marker = "🔴 СРОЧНО" if severity == "critical" else "🟠 ВНИМАНИЕ"
    assert first_line == f"{marker} — server: {headline}."
    assert "\nЧто делать: " in text
    assert text.endswith(f"\nИнструкция: {RUNBOOK_URL}")


def test_build_telegram_text_keeps_machine_identifiers_in_details_line() -> None:
    text = build_telegram_text(
        _event(
            category="vd_state",
            subject="VD 0",
            summary="VD 0 state changed from Optl to Dgrd",
            after={"state": "Dgrd"},
        ),
        hostname="server",
    )

    lines = text.splitlines()
    details = [line for line in lines if line.startswith("Детали: ")]
    assert details == [
        "Детали: VD 0; VD 0 state changed from Optl to Dgrd; событие #42; "
        "2026-10-25 12:00:00 UTC (2026-10-25 13:00:00 CET)"
    ]
    assert all("Dgrd" not in line for line in lines if not line.startswith("Детали: "))
    assert "Перешли это сообщение в ChatGPT для проверки." in text


def test_build_telegram_text_says_monitoring_state_is_unknown_not_failed() -> None:
    text = build_telegram_text(_event(category="system", subject="Controller"), hostname="h")

    assert "НЕИЗВЕСТНО" in text
    assert "не означает, что диски сломались" in text


def test_build_telegram_text_truncates_long_summary() -> None:
    text = build_telegram_text(
        _event(category="media_errors", summary="x" * 2000),
        hostname="server",
    )

    assert "x" * 500 in text
    assert "x" * 501 not in text


def test_build_test_telegram_text_is_clearly_marked_synthetic() -> None:
    text = build_test_telegram_text(hostname="server")

    assert text.startswith("🧪 ТЕСТ: проверочное сообщение, реальной аварии нет.\n\n🔴 СРОЧНО")
    assert "SYNTHETIC TEST" in text
    assert text.endswith(RUNBOOK_URL)


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (None, None),
        ({}, None),
        ({"state": 3}, None),
        ({"state": " Dgrd "}, "dgrd"),
    ],
)
def test_incident_state(after: dict[str, Any] | None, expected: str | None) -> None:
    assert incident_state(_event(category="vd_state", after=after)) == expected
