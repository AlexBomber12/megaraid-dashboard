from __future__ import annotations

import pytest

from megaraid_dashboard.db.models import PhysicalDriveSnapshot
from megaraid_dashboard.services.overview import _drive_grid_tile


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [(40, "optimal"), (56, "warning"), (61, "critical")],
)
def test_temperature_state_thresholds_default_55_60(
    temperature: int,
    expected: str,
) -> None:
    tile = _drive_grid_tile(_drive(temperature=temperature), temp_warning=55, temp_critical=60)

    assert tile.temperature_state == expected


def test_state_severity_mapping_for_all_states() -> None:
    assert (
        _drive_grid_tile(_drive(state="Onln"), temp_warning=55, temp_critical=60).state_severity
        == "optimal"
    )
    assert (
        _drive_grid_tile(_drive(state="UGood"), temp_warning=55, temp_critical=60).state_severity
        == "optimal"
    )
    assert (
        _drive_grid_tile(_drive(state="UBad"), temp_warning=55, temp_critical=60).state_severity
        == "warning"
    )
    assert (
        _drive_grid_tile(_drive(state="Rebld"), temp_warning=55, temp_critical=60).state_severity
        == "warning"
    )
    assert (
        _drive_grid_tile(_drive(state="Failed"), temp_warning=55, temp_critical=60).state_severity
        == "critical"
    )
    assert (
        _drive_grid_tile(_drive(state="Missing"), temp_warning=55, temp_critical=60).state_severity
        == "critical"
    )


def test_tile_severity_takes_worst_of_temp_and_state() -> None:
    hot_online = _drive_grid_tile(
        _drive(state="Onln", temperature=61),
        temp_warning=55,
        temp_critical=60,
    )
    cool_unconfigured_bad = _drive_grid_tile(
        _drive(state="UBad", temperature=40),
        temp_warning=55,
        temp_critical=60,
    )

    assert hot_online.tile_severity == "critical"
    assert cool_unconfigured_bad.tile_severity == "warning"


def test_missing_temperature_is_optimal_for_grid_tint() -> None:
    tile = _drive_grid_tile(_drive(temperature=None), temp_warning=55, temp_critical=60)

    assert tile.temperature_state == "optimal"
    assert tile.tile_severity == "optimal"


def test_unknown_non_online_state_defaults_to_warning() -> None:
    tile = _drive_grid_tile(_drive(state="Unexpected"), temp_warning=55, temp_critical=60)

    assert tile.state_severity == "warning"
    assert tile.tile_severity == "warning"


def _drive(*, state: str = "Onln", temperature: int | None = 40) -> PhysicalDriveSnapshot:
    return PhysicalDriveSnapshot(
        snapshot_id=1,
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
        disk_group_id=0,
        temperature_celsius=temperature,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="0x0",
    )
