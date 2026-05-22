from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import PhysicalDriveSnapshot
from megaraid_dashboard.services import overview as overview_module
from megaraid_dashboard.services.overview import (
    _drive_state_badge,
    _drive_temperature_badge,
    _empty_next_run_text,
    _event_severity_to_status,
    _require_aware_utc,
    _temperature_tooltip,
    derive_controller_health,
    load_drive_list_view_model,
)
from tests.test_services.test_overview_controller_health import (
    _physical_drive,
    _snapshot,
    _virtual_drive,
)


@pytest.fixture(autouse=True)
def overview_live_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def test_empty_drive_list_view_model_uses_scheduler_next_run(session: Session) -> None:
    next_run = datetime.now(UTC) + timedelta(seconds=45)

    view_model = load_drive_list_view_model(
        session,
        slot_url_factory=lambda enclosure_id, slot_id: f"/drives/{enclosure_id}:{slot_id}",
        scheduler=_Scheduler(_SchedulerJob(next_run)),
    )

    assert view_model.has_snapshot is False
    assert view_model.physical_drives == ()
    assert view_model.empty_next_run.startswith("Next scheduled run in ")


def test_empty_next_run_text_handles_scheduler_without_job() -> None:
    assert (
        _empty_next_run_text(scheduler=_Scheduler(None), collector_enabled=True)
        == "No collection run is currently scheduled."
    )


def test_empty_next_run_text_handles_disabled_collector() -> None:
    assert (
        _empty_next_run_text(scheduler=None, collector_enabled=False)
        == "Metrics collection is disabled; no collection run is scheduled."
    )


def test_empty_next_run_text_handles_missing_scheduler() -> None:
    assert (
        _empty_next_run_text(scheduler=None, collector_enabled=True)
        == "No collection run is currently scheduled."
    )


def test_empty_next_run_text_normalizes_naive_scheduler_timestamp() -> None:
    next_run = datetime.now() + timedelta(seconds=45)

    assert _empty_next_run_text(
        scheduler=_Scheduler(_SchedulerJob(next_run)),
        collector_enabled=True,
    ).startswith("Next scheduled run in ")


def test_derive_controller_health_resolves_physical_drive_severity_when_not_supplied() -> None:
    health = derive_controller_health(
        _snapshot(),
        [_physical_drive(state="Unexpected")],
        [_virtual_drive(state="Optl")],
    )

    assert health == "warning"


def test_derive_controller_health_accepts_precomputed_physical_drive_severity() -> None:
    health = derive_controller_health(
        _snapshot(),
        [_physical_drive(state="Unexpected")],
        [_virtual_drive(state="Optl")],
        physical_drive_severity="optimal",
    )

    assert health == "optimal"


@pytest.mark.parametrize("state", ["Unexpected", "Unknown"])
def test_drive_state_badge_treats_non_online_optimal_states_as_warning(state: str) -> None:
    assert (
        _drive_state_badge(
            _drive(state=state, temperature=None),
            temp_warning=55,
            temp_critical=60,
        )
        == "warning"
    )


def test_drive_state_badge_treats_unknown_severity_as_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        overview_module,
        "physical_drive_state_severity",
        lambda previous_state, current_state: "unexpected",
    )

    assert (
        _drive_state_badge(
            _drive(state="Onln", temperature=40),
            temp_warning=55,
            temp_critical=60,
        )
        == "warning"
    )


def test_drive_temperature_badge_preserves_unknown_when_no_temperature() -> None:
    assert (
        _drive_temperature_badge(
            _drive(temperature=None),
            temp_warning=55,
            temp_critical=60,
            row_state="optimal",
        )
        == "unknown"
    )


def test_drive_temperature_badge_is_neutral_when_row_state_already_warning() -> None:
    assert (
        _drive_temperature_badge(
            _drive(temperature=56),
            temp_warning=55,
            temp_critical=60,
            row_state="warning",
        )
        == "neutral"
    )


def test_private_small_helpers_cover_unknown_and_missing_inputs() -> None:
    assert _event_severity_to_status("debug") == "unknown"
    assert _temperature_tooltip(None, warning=55, critical=60) is None
    with pytest.raises(ValueError, match="timezone"):
        _require_aware_utc(datetime(2026, 5, 22, 12, 0))


def _drive(*, state: str = "Onln", temperature: int | None = 40) -> PhysicalDriveSnapshot:
    return PhysicalDriveSnapshot(
        enclosure_id=252,
        slot_id=0,
        device_id=10,
        model="ST1000",
        serial_number="SERIAL0",
        firmware_version="1.0",
        size_bytes=1_000_000_000_000,
        interface="SAS",
        media_type="HDD",
        state=state,
        temperature_celsius=temperature,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="0x0",
    )


@dataclass(frozen=True)
class _SchedulerJob:
    next_run_time: datetime | None


@dataclass(frozen=True)
class _Scheduler:
    job: _SchedulerJob | None

    def get_job(self, job_id: str) -> _SchedulerJob | None:
        assert job_id == "metrics_collector"
        return self.job
