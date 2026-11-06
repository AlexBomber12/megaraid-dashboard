from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.services.controller_detail import load_controller_detail_view_model
from megaraid_dashboard.services.drive_detail import load_drive_detail_view_model
from megaraid_dashboard.services.drive_health import assess_drive_health
from megaraid_dashboard.services.overview import (
    load_drive_list_view_model,
    load_main_page_view_model,
)
from tests.test_services.test_drive_detail import _seed_snapshot
from tests.test_services.test_drive_grid_tile import _drive


@pytest.mark.parametrize(
    ("state", "smart", "predictive", "media", "other", "temperature", "severity", "reason"),
    [
        ("Onln", False, 0, 0, 0, "optimal", "optimal", "functioning normally"),
        (" ONLINE ", False, 0, 0, 0, "unknown", "optimal", "functioning normally"),
        ("UGood", False, 0, 0, 0, "optimal", "optimal", "functioning normally"),
        ("Onln", True, 0, 0, 0, "optimal", "critical", "S.M.A.R.T."),
        ("Onln", False, 1, 0, 0, "optimal", "critical", "1 predictive failure."),
        ("Onln", False, 2, 1, 0, "optimal", "critical", "2 predictive failures."),
        ("UBad", False, 0, 0, 0, "optimal", "critical", "unusable (UBad)"),
        ("failed", False, 0, 0, 0, "optimal", "critical", "drive failure"),
        ("OfLn", False, 0, 0, 0, "optimal", "critical", "offline"),
        ("Offln", False, 0, 0, 0, "optimal", "critical", "offline"),
        ("MISSING", False, 0, 0, 0, "optimal", "critical", "No drive detected"),
        ("Msng", False, 0, 0, 0, "optimal", "critical", "No drive detected"),
        ("Rbld", False, 0, 0, 0, "optimal", "warning", "Rebuild"),
        ("Rebld", False, 0, 0, 0, "optimal", "warning", "Rebuild"),
        ("Rebuilding", False, 0, 0, 0, "optimal", "warning", "Rebuild"),
        ("JBOD", False, 0, 0, 0, "optimal", "warning", "not a RAID member"),
        ("Mystery", False, 0, 0, 0, "optimal", "warning", "Unrecognized drive state"),
        ("Onln", False, 0, 0, 0, "critical", "critical", "temperature is critical"),
        ("Onln", False, 0, 0, 0, "warning", "warning", "temperature is elevated"),
        ("Onln", False, 0, 1, 0, "optimal", "warning", "1 historical media error."),
        ("Onln", False, 0, 2, 1, "optimal", "warning", "2 historical media errors."),
        ("Onln", False, 0, 0, 2, "optimal", "warning", "2 other errors."),
        ("Rbld", True, 1, 1, 1, "warning", "critical", "S.M.A.R.T."),
    ],
)
def test_reported_health_signals_and_precedence(
    state: str,
    smart: bool,
    predictive: int,
    media: int,
    other: int,
    temperature: str,
    severity: str,
    reason: str,
) -> None:
    drive = _drive(state=state)
    drive.smart_alert = smart
    drive.predictive_failures = predictive
    drive.media_errors = media
    drive.other_errors = other
    result = assess_drive_health(drive, temperature=temperature)
    assert result.severity == severity
    assert reason in result.summary
    assert drive.state == state
    if state == "UGood":
        assert result.label == "Ready"
    if media and severity == "warning":
        assert "failure" not in result.summary


@pytest.fixture
def health_view_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    values = {
        "ALERT_SMTP_HOST": "smtp.example.test",
        "ALERT_SMTP_PORT": "587",
        "ALERT_SMTP_USER": "test",
        "ALERT_SMTP_PASSWORD": "test",
        "ALERT_FROM": "test@example.test",
        "ALERT_TO": "ops@example.test",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": "test-bcrypt-hash",
        "STORCLI_PATH": "/usr/local/sbin/storcli64",
        "METRICS_INTERVAL_SECONDS": "300",
        "LOG_LEVEL": "INFO",
        "TEMP_WARNING_CELSIUS": "55",
        "TEMP_CRITICAL_CELSIUS": "60",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("health_view_settings")
@pytest.mark.parametrize(
    ("state", "smart", "predictive", "media", "temperature", "severity"),
    [
        ("Onln", True, 0, 0, 40, "critical"),
        ("Onln", False, 1, 0, 40, "critical"),
        ("UBad", False, 0, 0, 40, "critical"),
        ("Offln", False, 0, 0, 40, "critical"),
        ("Onln", False, 0, 1, 40, "warning"),
        ("Onln", False, 0, 0, 56, "warning"),
        ("Onln", False, 0, 0, 61, "critical"),
        ("UGood", False, 0, 0, 40, "optimal"),
        ("Mystery", False, 0, 0, 40, "warning"),
    ],
)
def test_saved_drive_has_one_assessment_on_all_surfaces(
    session: Session,
    state: str,
    smart: bool,
    predictive: int,
    media: int,
    temperature: int,
    severity: str,
) -> None:
    _seed_snapshot(
        session,
        states={4: state},
        smart_alerts={4: smart},
        predictive_failures={4: predictive},
        media_errors={4: media},
        temperatures={4: temperature},
    )
    settings = get_settings()
    overview = load_main_page_view_model(
        session, settings=settings, scheduler=None, collector_enabled=False, app_version="test"
    )
    tile = next(tile for tile in overview.drive_grid.tiles if tile.slot_id == 4)
    rows = load_drive_list_view_model(
        session, slot_url_factory=lambda enclosure, slot: f"/drives/{enclosure}:{slot}"
    )
    row = next(row for row in rows.physical_drives if row.slot == "252:4")
    detail = load_drive_detail_view_model(
        session, enclosure_id=252, slot_id=4, settings=settings, app_version="test"
    )
    assert tile.tile_severity == row.row_state == detail.health.state_severity == severity
    assert tile.health_label == row.health_label == detail.health.health_label
    assert tile.health_reason == row.health_reason
    if severity != "optimal":
        assert tile.health_reason == detail.health.summary_text
    assert tile.state_text == row.state == state
    assert detail.health.state == state.upper()
    assert tile.slot_label == row.physical_slot_label == "S0"
    assert tile.detail_url == row.slot_url == "/drives/252:4"
    selected_slot = next(slot for slot in detail.position.backplane_layout if slot.is_this)
    assert selected_slot.severity == severity
    expected_summary = "OPTIMAL" if severity == "optimal" else severity.upper()
    assert overview.controller.state == expected_summary
    controller = load_controller_detail_view_model(
        session,
        settings=settings,
        scheduler=None,
        collector_enabled=False,
        app_start_time=datetime(2026, 1, 1, tzinfo=UTC),
        app_version="test",
        roc_history_chart_url="/charts/roc",
    )
    assert controller.health.state == expected_summary
