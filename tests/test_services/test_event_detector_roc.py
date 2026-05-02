from __future__ import annotations

from datetime import UTC, datetime

import pytest

from megaraid_dashboard.db.models import ControllerSnapshot
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.storcli import ControllerInfo, StorcliSnapshot


def test_roc_temperature_warm_up_has_no_event() -> None:
    assert _detector().detect(None, _current(98)) == []


@pytest.mark.parametrize(
    ("previous_temperature", "current_temperature", "expected"),
    [
        (
            85,
            98,
            [("warning", "RoC temperature 98 C crossed warning threshold (95 C)")],
        ),
        (98, 99, []),
        (98, 91, []),
        (98, 90, [("info", "RoC temperature 90 C cleared warning threshold")]),
        (
            99,
            105,
            [("critical", "RoC temperature 105 C crossed critical threshold (105 C)")],
        ),
        (106, 101, []),
        (106, 100, [("info", "RoC temperature 100 C cleared critical threshold")]),
    ],
)
def test_roc_temperature_threshold_transitions(
    previous_temperature: int,
    current_temperature: int,
    expected: list[tuple[str, str]],
) -> None:
    events = _detector().detect(
        _previous(previous_temperature),
        _current(current_temperature),
    )

    assert [(event.severity, event.summary) for event in events] == expected
    assert all(event.category == "controller_temperature" for event in events)


def test_roc_temperature_sustained_above_threshold_does_not_repeat() -> None:
    events = _detector().detect(_previous(98), _current(99))

    assert events == []


def test_roc_temperature_uses_configured_thresholds() -> None:
    detector = EventDetector(
        temp_warning=55,
        temp_critical=60,
        temp_hysteresis=5,
        roc_temp_warning=90,
        roc_temp_critical=100,
        roc_temp_hysteresis=4,
        cv_capacitance_warning_percent=70,
    )

    events = detector.detect(_previous(89), _current(90))

    assert [(event.severity, event.summary) for event in events] == [
        ("warning", "RoC temperature 90 C crossed warning threshold (90 C)")
    ]


@pytest.mark.parametrize(
    ("previous_temperature", "current_temperature"),
    [(None, 98), (85, None), (None, None)],
)
def test_roc_temperature_unavailable_skips_event(
    previous_temperature: int | None,
    current_temperature: int | None,
) -> None:
    events = _detector().detect(
        _previous(previous_temperature),
        _current(current_temperature),
    )

    assert events == []


def _detector() -> EventDetector:
    return EventDetector(
        temp_warning=55,
        temp_critical=60,
        temp_hysteresis=5,
        roc_temp_warning=95,
        roc_temp_critical=105,
        roc_temp_hysteresis=5,
        cv_capacitance_warning_percent=70,
    )


def _previous(roc_temperature_celsius: int | None) -> ControllerSnapshot:
    return ControllerSnapshot(
        captured_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number="SV00000001",
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state="Off",
        cv_present=True,
        bbu_present=False,
        roc_temperature_celsius=roc_temperature_celsius,
    )


def _current(roc_temperature_celsius: int | None) -> StorcliSnapshot:
    return StorcliSnapshot(
        controller=ControllerInfo(
            model_name="LSI MegaRAID SAS 9270CV-8i",
            serial_number="SV00000001",
            firmware_version="23.34.0-0019",
            bios_version="6.36.00.3_4.19.08.00_0x06180203",
            driver_name="megaraid_sas",
            driver_version="07.727.03.00",
            pci_address="00:01:00:00",
            system_time=datetime(2026, 4, 25, 12, 5, tzinfo=UTC),
            alarm_state="Off",
            cv_present=True,
            bbu_present=False,
            roc_temperature_celsius=roc_temperature_celsius,
        ),
        virtual_drives=[],
        physical_drives=[],
        cachevault=None,
        bbu=None,
        captured_at=datetime(2026, 4, 25, 12, 5, tzinfo=UTC),
    )
