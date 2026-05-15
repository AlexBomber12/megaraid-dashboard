from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import (
    CacheVaultSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from megaraid_dashboard.services.overview import (
    _cachevault_card,
    _drive_detail_url,
    _drive_state_badge,
    _drive_temperature_badge,
    _empty_next_run_text,
    _event_severity_to_status,
    _format_tb,
    _hottest_drive,
    _max_temperature,
    _physical_drive_aggregate_status,
    _require_temperature,
    _temperature_count,
    _temperature_severity,
    _virtual_drive_aggregate_status,
    _virtual_drive_aggregate_value,
    load_drive_list_view_model,
    load_overview_view_model,
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

    view_model = load_overview_view_model(session)

    # _load_drive_summary upgrades non-PD-optimal states whose severity-to-
    # status mapping returns "optimal" (e.g., "Rbld" because Onln->Rbld is
    # severity "info") to "warning". Controller Health reflects this.
    controller_health = next(card for card in view_model.cards if card.label == "Controller Health")
    assert controller_health.severity == "warning"


# --- Line 736: _empty_next_run_text with naive scheduler run time --------------


def test_empty_next_run_text_treats_naive_next_run_as_utc() -> None:
    naive_next_run = datetime(2099, 1, 1, 0, 0, 0)
    scheduler = _NaiveScheduler(next_run_time=naive_next_run)

    text = _empty_next_run_text(scheduler=scheduler, collector_enabled=True)

    assert text.startswith("Next scheduled run in ")
    assert text.endswith(" seconds.")


# --- Line 838: _cachevault_card fallback when capacitance_percent <= 0 --------


def test_cachevault_card_falls_back_to_unknown_when_capacitance_is_zero() -> None:
    cv = CacheVaultSnapshot(
        type="CV",
        state="Optimal",
        temperature_celsius=30,
        pack_energy=None,
        capacitance_percent=0,
        replacement_required=False,
        next_learn_cycle=None,
    )

    card = _cachevault_card(cv, capacitance_warning_percent=80)

    assert card.value == "Unknown"
    assert card.severity == "unknown"


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


# --- Line 1029, 1034: _virtual_drive_aggregate_status critical and fallback --


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ((), "neutral"),
        (("Failed",), "critical"),
        (("Offln", "Optl"), "critical"),
        (("Dgrd",), "warning"),
        (("Pdgd",), "warning"),
        (("Optl", "Optimal"), "optimal"),
        (("Rbld",), "warning"),
        (("Optl", "SomethingElse"), "warning"),
    ],
)
def test_virtual_drive_aggregate_status_covers_each_branch(
    states: tuple[str, ...], expected: str
) -> None:
    virtual_drives = [_make_vd(state=state, vd_id=index) for index, state in enumerate(states)]

    assert _virtual_drive_aggregate_status(virtual_drives) == expected


# --- Line 1058: _physical_drive_aggregate_status promotes "Rbld" to warning --


def test_physical_drive_aggregate_status_promotes_rebuilding_to_warning() -> None:
    drives = [
        _make_pd(state="Onln", slot_id=0),
        _make_pd(state="Rbld", slot_id=1),
    ]

    assert _physical_drive_aggregate_status(drives) == "warning"


def test_physical_drive_aggregate_status_empty_is_optimal() -> None:
    assert _physical_drive_aggregate_status([]) == "optimal"


# --- Line 1071, 1087: _virtual_drive_aggregate_value branches ----------------


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ((), "Unknown"),
        (("Failed", "Optl"), "1 failed"),
        (("Dgrd", "Optl"), "1 degraded"),
        (("Pdgd", "Optl"), "1 degraded"),
        (("Optl", "Optimal"), "2/2 OK"),
        (("Optl", "Rbld"), "1 unknown"),
    ],
)
def test_virtual_drive_aggregate_value_covers_each_branch(
    states: tuple[str, ...], expected: str
) -> None:
    virtual_drives = [_make_vd(state=state, vd_id=index) for index, state in enumerate(states)]

    assert _virtual_drive_aggregate_value(virtual_drives) == expected


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


# --- Line 1153: _temperature_severity (private wrapper) ----------------------


def test_temperature_severity_private_wrapper_delegates_to_public() -> None:
    assert _temperature_severity(58, temp_warning=55, temp_critical=60) == "warning"
    assert _temperature_severity(None, temp_warning=55, temp_critical=60) == "unknown"


# --- Line 1165: _temperature_count -------------------------------------------


def test_temperature_count_counts_drives_at_or_above_threshold() -> None:
    drives = [
        _make_pd(temperature_celsius=40, slot_id=0),
        _make_pd(temperature_celsius=55, slot_id=1),
        _make_pd(temperature_celsius=60, slot_id=2),
        _make_pd(temperature_celsius=None, slot_id=3),
    ]

    assert _temperature_count(drives, threshold=55) == 2
    assert _temperature_count(drives, threshold=80) == 0


# --- Lines 1173-1178: _max_temperature ---------------------------------------


def test_max_temperature_returns_max_of_populated_drives() -> None:
    drives = [
        _make_pd(temperature_celsius=40, slot_id=0),
        _make_pd(temperature_celsius=58, slot_id=1),
        _make_pd(temperature_celsius=None, slot_id=2),
    ]

    assert _max_temperature(drives) == 58


def test_max_temperature_returns_none_when_no_temperature_samples() -> None:
    drives = [
        _make_pd(temperature_celsius=None, slot_id=0),
        _make_pd(temperature_celsius=None, slot_id=1),
    ]

    assert _max_temperature(drives) is None
    assert _max_temperature([]) is None


# --- Lines 1184-1189: _hottest_drive -----------------------------------------


def test_hottest_drive_returns_drive_with_highest_temperature() -> None:
    drives = [
        _make_pd(temperature_celsius=40, slot_id=0),
        _make_pd(temperature_celsius=58, slot_id=1),
        _make_pd(temperature_celsius=58, slot_id=2),
    ]

    hottest = _hottest_drive(drives)

    assert hottest is not None
    # Tie-breaks by enclosure_id then slot_id ascending
    assert hottest.slot_id == 1


def test_hottest_drive_returns_none_when_no_temperatures() -> None:
    drives = [
        _make_pd(temperature_celsius=None, slot_id=0),
    ]

    assert _hottest_drive(drives) is None
    assert _hottest_drive([]) is None


# --- Lines 1196-1199: _require_temperature -----------------------------------


def test_require_temperature_returns_value_when_present() -> None:
    drive = _make_pd(temperature_celsius=42)

    assert _require_temperature(drive) == 42


def test_require_temperature_raises_when_missing() -> None:
    drive = _make_pd(temperature_celsius=None)

    with pytest.raises(ValueError, match="drive temperature is required"):
        _require_temperature(drive)


# --- Line 1203: _drive_detail_url --------------------------------------------


def test_drive_detail_url_strips_trailing_slash_and_appends_path() -> None:
    drive = _make_pd(enclosure_id=252, slot_id=3)

    assert _drive_detail_url("/drives/", drive) == "/drives/252/3"
    assert _drive_detail_url("/drives", drive) == "/drives/252/3"


# --- Line 1211: _format_tb wrapper -------------------------------------------


def test_format_tb_wrapper_delegates_to_public() -> None:
    assert _format_tb(2 * 10**12) == "2.0 TB"
