from __future__ import annotations

# ruff: noqa: E402, I001

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import (
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.overview import (
    _drive_state_badge,
    _drive_temperature_badge,
    _empty_next_run_text,
    _event_severity_to_status,
    _physical_drive_aggregate_status,
    _virtual_drive_controller_health_status,
    format_tb,
    load_drive_list_view_model,
    temperature_severity,
)
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.test_services.test_overview import _insert, _snapshot


@pytest.fixture(autouse=True)
def overview_branches_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def _make_pd(
    *,
    state: str = "Onln",
    temperature_celsius: int | None = 40,
    enclosure_id: int = 252,
    slot_id: int = 0,
) -> PhysicalDriveSnapshot:
    return PhysicalDriveSnapshot(
        enclosure_id=enclosure_id,
        slot_id=slot_id,
        device_id=32 + slot_id,
        model="ST4000NM000",
        serial_number=f"SN{slot_id:04d}",
        firmware_version="SN04",
        size_bytes=4_000_000_000_000,
        interface="SAS",
        media_type="HDD",
        state=state,
        temperature_celsius=temperature_celsius,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address=f"5000c500000000{slot_id:02d}",
    )


def _make_vd(*, state: str = "Optl", vd_id: int = 0) -> VirtualDriveSnapshot:
    return VirtualDriveSnapshot(
        vd_id=vd_id,
        name=f"raid6-{vd_id}",
        raid_level="RAID6",
        size_bytes=1_000_000_000,
        state=state,
        access="RW",
        cache="RWBD",
    )


@dataclass(frozen=True)
class _NaiveSchedulerJob:
    next_run_time: datetime


@dataclass(frozen=True)
class _NaiveScheduler:
    next_run_time: datetime

    def get_job(self, job_id: str) -> _NaiveSchedulerJob | None:
        if job_id != "metrics_collector":
            return None
        return _NaiveSchedulerJob(next_run_time=self.next_run_time)


# --- Line 456: load_drive_list_view_model with empty database -----------------


def test_drive_list_view_model_handles_empty_database(session: Session) -> None:
    view_model = load_drive_list_view_model(
        session,
        slot_url_factory=lambda enclosure_id, slot_id: f"/{enclosure_id}/{slot_id}",
    )

    assert view_model.has_snapshot is False
    assert view_model.physical_drives == ()
    assert view_model.drive_summary.total == 0
    assert view_model.empty_next_run == "No collection run is currently scheduled."


# --- Line 574: _load_drive_summary upgrades non-optimal info severities -------


def test_overview_drive_summary_promotes_rebuilding_non_optimal_state_to_warning(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, pd_state="Rbld"))

    view_model = load_drive_list_view_model(
        session,
        slot_url_factory=lambda enclosure_id, slot_id: f"/{enclosure_id}/{slot_id}",
    )

    assert view_model.drive_summary.warning == 1
    assert view_model.physical_drives[0].row_state == "warning"


# --- Line 736: _empty_next_run_text with naive scheduler run time --------------


def test_empty_next_run_text_treats_naive_next_run_as_utc() -> None:
    naive_next_run = datetime(2099, 1, 1, 0, 0, 0)
    scheduler = _NaiveScheduler(next_run_time=naive_next_run)

    text = _empty_next_run_text(scheduler=scheduler, collector_enabled=True)

    assert text.startswith("Next scheduled run in ")
    assert text.endswith(" seconds.")


# --- Line 964: _drive_state_badge promotes "unknown" state status to warning --


def test_drive_state_badge_treats_unknown_state_status_as_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # physical_drive_state_severity never returns severities other than
    # info/critical/warning, but the defensive branch at line 964 promotes
    # an "unknown" mapping to "warning". Patch event_detector's helper used
    # internally by overview.py to return a synthetic severity, which then
    # flows through _event_severity_to_status -> "unknown".
    import megaraid_dashboard.services.overview as overview_module

    monkeypatch.setattr(
        overview_module,
        "physical_drive_state_severity",
        lambda previous, current: "fabricated",
    )
    drive = _make_pd(state="Mystery", temperature_celsius=40)

    badge = _drive_state_badge(drive, temp_warning=55, temp_critical=60)

    assert badge == "warning"


# --- Line 971: _drive_state_badge resets unknown temperature to optimal ------


def test_drive_state_badge_resets_unknown_temperature_to_optimal() -> None:
    drive = _make_pd(state="Onln", temperature_celsius=None)

    badge = _drive_state_badge(drive, temp_warning=55, temp_critical=60)

    assert badge == "optimal"


# --- Line 988: _drive_temperature_badge returns "unknown" for missing temp ---


def test_drive_temperature_badge_returns_unknown_for_missing_temperature() -> None:
    drive = _make_pd(temperature_celsius=None)

    badge = _drive_temperature_badge(
        drive,
        temp_warning=55,
        temp_critical=60,
        row_state="optimal",
    )

    assert badge == "unknown"


# --- _virtual_drive_controller_health_status critical and fallback ------------


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ((), "optimal"),
        (("Failed",), "critical"),
        (("Offln", "Optl"), "critical"),
        (("Dgrd",), "warning"),
        (("Pdgd",), "critical"),
        (("Optl", "Optimal"), "optimal"),
        (("Rbld",), "warning"),
        (("Optl", "SomethingElse"), "warning"),
    ],
)
def test_virtual_drive_aggregate_status_covers_each_branch(
    states: tuple[str, ...], expected: str
) -> None:
    virtual_drives = [_make_vd(state=state, vd_id=index) for index, state in enumerate(states)]

    assert _virtual_drive_controller_health_status(virtual_drives) == expected


# --- Line 1058: _physical_drive_aggregate_status promotes "Rbld" to warning --


def test_physical_drive_aggregate_status_promotes_rebuilding_to_warning() -> None:
    drives = [
        _make_pd(state="Onln", slot_id=0),
        _make_pd(state="Rbld", slot_id=1),
    ]

    assert _physical_drive_aggregate_status(drives) == "warning"


def test_physical_drive_aggregate_status_empty_is_optimal() -> None:
    assert _physical_drive_aggregate_status([]) == "optimal"


# --- Line 1118: _event_severity_to_status fallback for unknown severity -----


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("info", "optimal"),
        ("warning", "warning"),
        ("critical", "critical"),
        ("debug", "unknown"),
        ("anything-else", "unknown"),
    ],
)
def test_event_severity_to_status_covers_each_branch(severity: str, expected: str) -> None:
    assert _event_severity_to_status(severity) == expected


# --- Line 1128: temperature_severity returns "unknown" for None --------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "unknown"),
        (40, "optimal"),
        (55, "warning"),
        (60, "critical"),
    ],
)
def test_temperature_severity_covers_each_branch(value: int | None, expected: str) -> None:
    assert temperature_severity(value, temp_warning=55, temp_critical=60) == expected


# --- format_tb ---------------------------------------------------------------


def test_format_tb_formats_bytes_as_decimal_tb() -> None:
    assert format_tb(2 * 10**12) == "2.0 TB"
