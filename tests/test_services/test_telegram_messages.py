from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.telegram_messages import (
    build_telegram_text,
    build_test_telegram_text,
    incident_key,
    incident_state,
)

OCCURRED_AT = datetime(2026, 10, 26, 9, 57, tzinfo=UTC)

# Reviewed message catalog: structured input, exact wording, and disk-handling advice.
CATALOG = [
    ("vd_state", "critical", {"state": "Dgrd"}, "сбой массива, риск потери данных.", True),
    ("vd_state", "critical", {"state": "Pdgd"}, "сбой массива, риск потери данных.", True),
    ("vd_state", "critical", {"state": "OfLn"}, "массив недоступен.", True),
    ("vd_state", "critical", {"state": "Failed"}, "массив недоступен.", True),
    ("pd_state", "critical", {"state": "Failed"}, "отказ диска.", True),
    ("pd_state", "critical", {"state": "Missing"}, "диск не обнаружен.", True),
    ("pd_state", "critical", {"state": "Offline"}, "диск в состоянии «офлайн».", True),
    ("media_errors", "critical", {"media_errors": 2}, "ошибки носителя диска.", True),
    (
        "predictive_failures",
        "critical",
        {"predictive_failures": 1},
        "диск сообщает о вероятном отказе.",
        True,
    ),
    (
        "smart_alert",
        "critical",
        {"smart_alert": True},
        "предупреждение самодиагностики диска.",
        True,
    ),
    ("cv_state", "critical", {"present": False}, "защита кэша не обнаружена.", False),
    (
        "cv_state",
        "critical",
        {"replacement_required": True},
        "защита кэша требует замены.",
        False,
    ),
    ("cv_state", "critical", {"state": "Degraded"}, "сбой защиты кэша.", False),
    (
        "temperature",
        "warning",
        {"temperature_celsius": 57},
        "перегрев диска: 57 °C.",
        False,
    ),
    (
        "temperature",
        "critical",
        {"temperature_celsius": 65},
        "критическая температура диска: 65 °C.",
        False,
    ),
    (
        "controller_temperature",
        "warning",
        {"roc_temperature_celsius": 96},
        "перегрев контроллера: 96 °C.",
        False,
    ),
    (
        "controller_temperature",
        "critical",
        {"roc_temperature_celsius": 106},
        "критическая температура контроллера: 106 °C.",
        False,
    ),
    (
        "system",
        "critical",
        {"monitoring_outage": True},
        "мониторинг недоступен, состояние RAID неизвестно.",
        False,
    ),
    ("cv_state", "critical", {"state": "Unknown"}, "событие RAID, данные неполны.", False),
]


def _event(
    *,
    category: str,
    severity: str = "critical",
    subject: str = "Controller",
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


@pytest.mark.parametrize("hostname", ["SERVER", "AI-SERVER"])
@pytest.mark.parametrize(("category", "severity", "after", "headline", "protect_disks"), CATALOG)
def test_complete_catalog(
    hostname: str,
    category: str,
    severity: str,
    after: dict[str, Any],
    headline: str,
    protect_disks: bool,
) -> None:
    event = _event(category=category, severity=severity, after=after)
    text = build_telegram_text(event, hostname=hostname)
    marker = "🔴" if severity == "critical" else "🟠"
    action = "Перешли в ChatGPT." + (" Диски не трогай." if protect_disks else "")
    assert text == f"{marker} {hostname}: {headline}\n{action}\n26.10 10:57 · №42"
    assert len(text) <= 160
    assert len(text.splitlines()) == 3
    assert all(text.splitlines())
    assert "http" not in text


@pytest.mark.parametrize(
    ("category", "aliases", "headline"),
    [
        (
            "vd_state",
            ["Dgrd", "Degraded", "Pdgd", "Partially Degraded"],
            "сбой массива, риск потери данных.",
        ),
        ("vd_state", ["Failed", "OfLn", "Offln", "Offline"], "массив недоступен."),
        ("pd_state", ["Failed"], "отказ диска."),
        ("pd_state", ["Missing", "Msng"], "диск не обнаружен."),
        ("pd_state", ["Offline", "Offln"], "диск в состоянии «офлайн»."),
        ("cv_state", ["Degraded", "Dgrd", "Failed", "Unhealthy", "Faulty"], "сбой защиты кэша."),
    ],
)
def test_state_aliases_casing_whitespace_and_transition_do_not_change_meaning(
    category: str,
    aliases: list[str],
    headline: str,
) -> None:
    for alias in aliases:
        for state in (alias, alias.upper(), f"  {alias.lower()}  "):
            for before in (None, {"state": "Optl"}):
                event = _event(category=category, after={"state": state})
                event.before_json = before
                text = build_telegram_text(event, hostname="SERVER")
                assert text.splitlines()[0] == f"🔴 SERVER: {headline}"


@pytest.mark.parametrize(
    "category",
    [
        "vd_state",
        "pd_state",
        "cv_state",
        "media_errors",
        "predictive_failures",
        "smart_alert",
        "temperature",
        "controller_temperature",
        "system",
        "unsupported",
    ],
)
@pytest.mark.parametrize("after", [None, {}, {"state": "unknown"}, {"state": 42}])
def test_unknown_metadata_is_factual_fallback(category: str, after: dict[str, Any] | None) -> None:
    text = build_telegram_text(_event(category=category, after=after), hostname="SERVER")
    assert text == (
        "🔴 SERVER: событие RAID, данные неполны.\nПерешли в ChatGPT.\n26.10 10:57 · №42"
    )


@pytest.mark.parametrize("category", ["temperature", "controller_temperature"])
@pytest.mark.parametrize(
    "value",
    [None, True, False, "65", "65 C", -1, 201, 10**400, 65.5, float("nan"), float("inf"), {}, []],
)
def test_invalid_measurement_is_never_inferred_from_summary(category: str, value: Any) -> None:
    field = "temperature_celsius" if category == "temperature" else "roc_temperature_celsius"
    text = build_telegram_text(
        _event(category=category, after={field: value}, summary="PD e252:s4 overheated at 65 C"),
        hostname="SERVER",
    )
    assert "данные неполны" in text
    assert "65" not in text and "°C" not in text


@pytest.mark.parametrize("value", [0, 200])
def test_valid_integral_measurement_boundaries(value: int) -> None:
    text = build_telegram_text(
        _event(category="temperature", after={"temperature_celsius": value}),
        hostname="SERVER",
    )
    assert f": {value} °C." in text


@pytest.mark.parametrize("category", ["media_errors", "predictive_failures"])
@pytest.mark.parametrize("value", [None, True, 0, -1, "1", {}, []])
def test_invalid_counter_is_not_a_diagnosis(category: str, value: Any) -> None:
    text = build_telegram_text(
        _event(category=category, after={category: value}), hostname="SERVER"
    )
    assert "данные неполны" in text


@pytest.mark.parametrize(
    ("category", "after"),
    [
        ("smart_alert", {"smart_alert": False}),
        ("smart_alert", {"smart_alert": 1}),
        ("cv_state", {"present": 0}),
        ("cv_state", {"replacement_required": "true"}),
        ("system", {"monitoring_outage": 1}),
        ("system", {"monitoring_outage": "true"}),
        ("vd_state", {"state": "Optimal"}),
        ("pd_state", {"state": "Onln"}),
        ("cv_state", {"state": "Optimal"}),
    ],
)
def test_unconfirmed_or_healthy_fields_cannot_invent_failure(
    category: str,
    after: dict[str, Any],
) -> None:
    assert "данные неполны" in build_telegram_text(
        _event(category=category, after=after),
        hostname="SERVER",
    )


def test_free_text_and_machine_identifiers_never_enter_body() -> None:
    event = _event(category="vd_state", after={"state": "Dgrd", "serial_number": "SECRET-SERIAL"})
    expected = build_telegram_text(event, hostname="SERVER")
    event.summary = (
        "<b>FAIL</b> *[link](https://evil.invalid)*\nTraceback: 999 disks 300 C " * 10000
    )
    event.subject = "VD 0 PD e252:s4 <b>subject</b>"
    assert build_telegram_text(event, hostname="SERVER") == expected


@pytest.mark.parametrize(
    ("occurred", "footer"),
    [
        (datetime(2026, 1, 26, 9, 57, tzinfo=UTC), "26.01 10:57 · №42"),
        (datetime(2026, 7, 26, 8, 57, tzinfo=UTC), "26.07 10:57 · №42"),
        (datetime(2026, 10, 26, 9, 57), "26.10 10:57 · №42"),
    ],
)
def test_footer_uses_occurrence_time_in_rome(occurred: datetime, footer: str) -> None:
    event = _event(category="vd_state", after={"state": "Dgrd"})
    event.occurred_at = occurred
    assert build_telegram_text(event, hostname="SERVER").splitlines()[-1] == footer


def test_full_database_event_identity_fits_without_truncation() -> None:
    event = _event(category="system", after={"monitoring_outage": True})
    event.id = 2**63 - 1
    text = build_telegram_text(event, hostname="AI-SERVER")
    assert len(text) <= 160
    assert text.endswith(f"№{event.id}")


@pytest.mark.parametrize("hostname", ["SERVER", "AI-SERVER"])
def test_manual_test_is_independent_and_neutral(hostname: str) -> None:
    text = build_test_telegram_text(hostname=hostname)
    assert text == f"🧪 {hostname}: тест уведомлений\nДействий не требуется."
    assert len(text) <= 160 and len(text.splitlines()) == 2


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


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        ({"state": "Dgrd", "present": True}, (("state", "dgrd"),)),
        ({"present": False}, (("present", False),)),
        ({"replacement_required": True}, (("replacement_required", True),)),
        ({"media_errors": 7}, (("media_errors", None),)),
        (None, ()),
    ],
)
def test_incident_key(after: dict[str, Any] | None, expected: tuple[object, ...]) -> None:
    assert incident_key(_event(category="cv_state", after=after)) == expected
