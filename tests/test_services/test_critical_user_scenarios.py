"""Saved observations -> event/audit store -> visible health -> mocked Telegram.

No hardware reads or writes and no real outbound notification are used here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.db.dao import insert_snapshot, record_event, set_maintenance_state
from megaraid_dashboard.db.models import ControllerSnapshot
from megaraid_dashboard.services.controller_detail import load_controller_detail_view_model
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.services.overview import load_main_page_view_model
from megaraid_dashboard.services.telegram_notifier import run_telegram_cycle
from megaraid_dashboard.storcli import StorcliSnapshot
from megaraid_dashboard.web.metrics import _encode_controller_health
from tests.test_services.test_telegram_notifier import FakeSender, _settings

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def _healthy(sample: StorcliSnapshot, *, at: datetime = NOW) -> StorcliSnapshot:
    assert sample.cachevault is not None
    return sample.model_copy(
        update={
            "captured_at": at,
            "controller": sample.controller.model_copy(
                update={"roc_temperature_celsius": 80, "cv_present": True}
            ),
            "physical_drives": [
                d.model_copy(update={"state": "Onln", "temperature_celsius": 35})
                for d in sample.physical_drives
            ],
            "virtual_drives": [
                d.model_copy(update={"state": "Optl"}) for d in sample.virtual_drives
            ],
            "cachevault": sample.cachevault.model_copy(
                update={
                    "state": "Optimal",
                    "replacement_required": False,
                    "capacitance_percent": 89,
                }
            ),
        }
    )


def _fault(sample: StorcliSnapshot, case: str) -> StorcliSnapshot:
    if case in {"vd-degraded", "vd-offline", "vd-hyphen"}:
        return sample.model_copy(
            update={
                "virtual_drives": [
                    d.model_copy(
                        update={
                            "state": {
                                "vd-degraded": "Dgrd",
                                "vd-offline": "OfLn",
                                "vd-hyphen": "Partially-Degraded",
                            }[case]
                        }
                    )
                    for d in sample.virtual_drives
                ]
            }
        )
    if case.startswith("cv-"):
        assert sample.cachevault is not None
        if case == "cv-absent":
            return sample.model_copy(
                update={
                    "cachevault": None,
                    "controller": sample.controller.model_copy(update={"cv_present": False}),
                }
            )
        values: dict[str, Any] = {
            "cv-failed": {"state": "Failed"},
            "cv-replace": {"replacement_required": True},
            "cv-capacitance": {"capacitance_percent": 60},
        }[case]
        return sample.model_copy(update={"cachevault": sample.cachevault.model_copy(update=values)})
    if case.startswith("roc-"):
        return sample.model_copy(
            update={
                "controller": sample.controller.model_copy(
                    update={"roc_temperature_celsius": 106 if case == "roc-critical" else 96}
                )
            }
        )
    values = {
        "pd-failed": {"state": "Failed"},
        "pd-ubad": {"state": "UBad"},
        "pd-missing": {"state": "Msng"},
        "pd-smart": {"smart_alert": True},
        "pd-predictive": {"predictive_failures": 1},
        "pd-media": {"media_errors": 1},
        "pd-hot": {"temperature_celsius": 65},
        "pd-spare": {"state": "DHS"},
    }[case]
    return sample.model_copy(
        update={
            "physical_drives": [
                d.model_copy(update=values) if d.slot_id == 4 else d for d in sample.physical_drives
            ]
        }
    )


@pytest.mark.parametrize("phase", ["first-poll", "transition", "maintenance"])
@pytest.mark.parametrize(
    ("case", "health", "message"),
    [
        ("vd-degraded", "CRITICAL", "сбой массива"),
        ("vd-offline", "CRITICAL", "массив недоступен"),
        ("vd-hyphen", "CRITICAL", "сбой массива"),
        ("pd-failed", "CRITICAL", "отказ диска"),
        ("pd-ubad", "CRITICAL", "непригодным"),
        ("pd-missing", "CRITICAL", "диск не обнаружен"),
        ("pd-smart", "CRITICAL", "самодиагностики"),
        ("pd-predictive", "CRITICAL", "вероятном отказе"),
        ("pd-media", "WARNING", "ошибки носителя"),
        ("pd-hot", "CRITICAL", "критическая температура диска"),
        ("roc-critical", "CRITICAL", "критическая температура контроллера"),
        ("roc-warning", "WARNING", None),
        ("cv-failed", "CRITICAL", "сбой защиты кэша"),
        ("cv-replace", "CRITICAL", "защита кэша требует замены"),
        ("cv-absent", "CRITICAL", "защита кэша не обнаружена"),
        ("cv-capacitance", "WARNING", None),
        ("pd-spare", "OPTIMAL", None),
    ],
)
def test_critical_operator_observation_chain(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    phase: str,
    case: str,
    health: str,
    message: str | None,
) -> None:
    settings = _settings()
    baseline = _healthy(sample_snapshot, at=NOW - timedelta(minutes=5))
    previous: ControllerSnapshot | None = None
    if phase != "first-poll":
        previous = insert_snapshot(session, baseline)
        session.commit()
    if phase == "maintenance":
        set_maintenance_state(
            session, active=True, started_by="scenario", expires_at=NOW + timedelta(hours=1)
        )
        session.commit()
    current = _fault(_healthy(sample_snapshot), case)
    detector = EventDetector(
        temp_warning=55,
        temp_critical=60,
        temp_hysteresis=5,
        roc_temp_warning=95,
        roc_temp_critical=105,
        roc_temp_hysteresis=5,
    )
    detected = detector.detect(previous, current)
    stored = insert_snapshot(session, current)
    for item in detected:
        event = record_event(
            session,
            severity=item.severity,
            category=item.category,
            subject=item.subject,
            summary=item.summary,
            before=item.before,
            after=item.after,
        )
        event.occurred_at = NOW
    session.commit()
    sender = FakeSender()
    first = run_telegram_cycle(
        session, settings=settings, now=NOW, transport_factory=lambda _: sender
    )
    if message is not None:
        assert first.sent == 1, (case, phase, detected, first)
        assert message in sender.sent[0]
        assert len(sender.sent[0]) <= 160
    else:
        assert first.sent == 0
    second = run_telegram_cycle(
        session, settings=settings, now=NOW, transport_factory=lambda _: sender
    )
    assert second.sent == 0
    overview = load_main_page_view_model(
        session, settings=settings, scheduler=None, collector_enabled=True, app_version="scenario"
    )
    controller = load_controller_detail_view_model(
        session,
        settings=settings,
        scheduler=None,
        collector_enabled=True,
        app_start_time=NOW - timedelta(hours=1),
        app_version="scenario",
        roc_history_chart_url="/controller/roc-history",
    )
    assert overview.controller.state == controller.health.state == health
    assert (
        _encode_controller_health(
            stored, list(stored.physical_drives), list(stored.virtual_drives), settings=settings
        )
        == {"OPTIMAL": 0, "WARNING": 1, "CRITICAL": 2}[health]
    )
    if not case.startswith("vd-"):
        assert "state degraded" not in controller.health.summary_text
