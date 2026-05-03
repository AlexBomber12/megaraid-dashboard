from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.services.overview import (
    OverviewViewModel,
    _pluralize,
    load_drive_list_view_model,
    load_overview_view_model,
)
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.test_services.test_overview import _insert, _snapshot


@pytest.fixture(autouse=True)
def overview_polish_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-bcrypt-hash")
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("TEMP_WARNING_CELSIUS", "55")
    monkeypatch.setenv("TEMP_CRITICAL_CELSIUS", "60")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_pluralize_uses_singular_for_exactly_one() -> None:
    assert _pluralize(1, "drive", "drives") == "drive"


def test_pluralize_uses_plural_for_zero() -> None:
    assert _pluralize(0, "drive", "drives") == "drives"


def test_pluralize_uses_plural_for_multiple() -> None:
    assert _pluralize(2, "drive", "drives") == "drives"


def test_status_strip_uses_singular_drive_copy_for_one_elevated_drive(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, temperatures=(55,)))

    view_model = load_overview_view_model(session)
    temp_card = _card(view_model, "Max Disk Temp")

    assert [(badge.label, badge.severity) for badge in temp_card.badges] == [
        ("1 drive elevated", "warning")
    ]


def test_drive_row_with_warning_temperature_and_degraded_state_emits_one_badge(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, pd_state="Degraded", temperatures=(56,)))

    drive = _first_drive_row(session)

    assert _warning_or_critical_badges(drive.row_state, drive.temperature_severity) == ("warning",)


def test_drive_row_with_warning_temperature_and_optimal_state_emits_one_warning_badge(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, pd_state="Onln", temperatures=(56,)))

    drive = _first_drive_row(session)

    assert _warning_or_critical_badges(drive.row_state, drive.temperature_severity) == ("warning",)


def test_drive_row_with_failed_state_and_optimal_temperature_emits_one_critical_badge(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, pd_state="Failed", temperatures=(50,)))

    drive = _first_drive_row(session)

    assert _warning_or_critical_badges(drive.row_state, drive.temperature_severity) == ("critical",)


def _first_drive_row(session: Session):
    view_model = load_drive_list_view_model(
        session,
        slot_url_factory=lambda enclosure_id, slot_id: f"/drives/{enclosure_id}/{slot_id}",
    )
    return view_model.physical_drives[0]


def _warning_or_critical_badges(*severities: str) -> tuple[str, ...]:
    return tuple(severity for severity in severities if severity in {"critical", "warning"})


def _card(view_model: OverviewViewModel, label: str):
    for card in view_model.cards:
        if card.label == label:
            return card
    raise AssertionError(f"missing card: {label}")
