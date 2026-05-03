from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import get_latest_snapshot, insert_snapshot
from megaraid_dashboard.services.overview import (
    _DriveSummary,
    _load_bbu_tile,
    _load_controller_tile,
    _load_max_temp_tile,
    _load_raid_tile,
    _load_roc_temperature,
    _load_roc_tile,
    _load_vd_tile,
)
from megaraid_dashboard.storcli import StorcliSnapshot


@pytest.fixture(autouse=True)
def overview_strip_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def test_controller_tile_reports_alarm_as_critical(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, alarm_state="On"))

    tile = _load_controller_tile(snapshot)

    assert tile.label == "Controller"
    assert tile.value == "Alarm"
    assert tile.status == "critical"
    assert tile.icon == "cpu"


def test_vd_tile_summarizes_all_optimal_drives(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, vd_states=("Optl", "Optimal")))

    tile = _load_vd_tile(snapshot)

    assert tile.value == "2/2 OK"
    assert tile.status == "optimal"
    assert tile.href == "/"


def test_vd_and_raid_tiles_warn_for_one_degraded_drive(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, vd_states=("Optl", "Dgrd")))

    vd_tile = _load_vd_tile(snapshot)
    raid_tile = _load_raid_tile(snapshot)

    assert vd_tile.value == "1 degraded"
    assert vd_tile.status == "warning"
    assert raid_tile.value == "RAID6"
    assert raid_tile.status == "warning"


@pytest.mark.parametrize("state", ["Pdgd", "Partially Degraded"])
def test_vd_and_raid_tiles_are_warning_for_partially_degraded_drive(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    state: str,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, vd_states=("Optl", state)))

    vd_tile = _load_vd_tile(snapshot)
    raid_tile = _load_raid_tile(snapshot)

    assert vd_tile.value == "1 degraded"
    assert vd_tile.status == "warning"
    assert raid_tile.value == "RAID6"
    assert raid_tile.status == "warning"


def test_raid_tile_uses_stable_tie_break_for_equally_common_levels(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    first_snapshot = _latest(
        session,
        _snapshot(
            sample_snapshot,
            vd_states=("Optl", "Optl", "Optl", "Optl"),
            raid_levels=("RAID6", "RAID10", "RAID10", "RAID6"),
        ),
    )
    second_snapshot = _latest(
        session,
        _snapshot(
            sample_snapshot,
            vd_states=("Optl", "Optl", "Optl", "Optl"),
            raid_levels=("RAID10", "RAID6", "RAID6", "RAID10"),
        ),
    )

    assert _load_raid_tile(first_snapshot).value == "RAID10"
    assert _load_raid_tile(second_snapshot).value == "RAID10"


def test_bbu_tile_is_neutral_when_bbu_is_absent(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(
        session,
        _snapshot(sample_snapshot, bbu_present=False, cachevault_present=False),
    )

    tile = _load_bbu_tile(snapshot)

    assert tile.value == "None"
    assert tile.status == "neutral"
    assert tile.icon == "lightbulb"


@pytest.mark.parametrize(
    ("cv_state", "cv_replacement_required", "expected_value", "expected_status"),
    [
        ("Degraded", False, "Warning", "warning"),
        ("Optimal", True, "Replace", "critical"),
    ],
)
def test_bbu_tile_uses_cachevault_state_when_bbu_is_absent(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    cv_state: str,
    cv_replacement_required: bool,
    expected_value: str,
    expected_status: str,
) -> None:
    snapshot = _latest(
        session,
        _snapshot(
            sample_snapshot,
            bbu_present=False,
            cv_state=cv_state,
            cv_replacement_required=cv_replacement_required,
        ),
    )

    tile = _load_bbu_tile(snapshot)

    assert tile.value == expected_value
    assert tile.status == expected_status
    assert tile.href == "/drives"


def test_bbu_tile_is_warning_for_present_degraded_cachevault(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, cv_state="Degraded"))

    tile = _load_bbu_tile(snapshot)

    assert tile.value == "Warning"
    assert tile.status == "warning"
    assert tile.href == "/drives"


def test_bbu_tile_is_critical_when_cachevault_replacement_required(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, cv_replacement_required=True))

    tile = _load_bbu_tile(snapshot)

    assert tile.value == "Replace"
    assert tile.status == "critical"
    assert tile.href == "/drives"


def test_max_temp_tile_uses_hottest_drive_and_thresholds(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _latest(session, _snapshot(sample_snapshot, temperatures=(42, 51, 58)))

    tile = _load_max_temp_tile(
        _DriveSummary(
            drive_count=8,
            max_temperature_celsius=58,
            elevated_drive_count=1,
            critical_drive_count=0,
            worst_state_severity="optimal",
            hottest_drive_url="/drives/252/2",
        ),
        settings=get_settings(),
    )

    assert tile.value == "58 C"
    assert tile.status == "warning"
    assert tile.href == "/drives/252/2"


def test_max_temp_tile_links_to_stable_hottest_drive_when_temperatures_tie(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _latest(session, _snapshot(sample_snapshot, temperatures=(58, 42, 58)))

    tile = _load_max_temp_tile(
        _DriveSummary(
            drive_count=8,
            max_temperature_celsius=58,
            elevated_drive_count=2,
            critical_drive_count=0,
            worst_state_severity="optimal",
            hottest_drive_url="/raid/drives/252/0",
        ),
        settings=get_settings(),
        drives_url="/raid/drives",
    )

    assert tile.value == "58 C"
    assert tile.href == "/raid/drives/252/0"


def test_max_temp_tile_is_neutral_without_physical_drives(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _latest(session, _snapshot(sample_snapshot, temperatures=()))

    tile = _load_max_temp_tile(
        _DriveSummary(
            drive_count=0,
            max_temperature_celsius=None,
            elevated_drive_count=0,
            critical_drive_count=0,
            worst_state_severity="optimal",
            hottest_drive_url=None,
        ),
        settings=get_settings(),
    )

    assert tile.value == "Unknown"
    assert tile.status == "neutral"
    assert tile.href == "/drives"


def test_roc_tile_reuses_roc_temperature_section(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, roc_temperature_celsius=None))
    section = _load_roc_temperature(
        session,
        settings=get_settings(),
        latest_snapshot=snapshot,
    )

    tile = _load_roc_tile(section)

    assert tile.label == "RoC"
    assert tile.value == "Unknown"
    assert tile.status == "neutral"
    assert tile.icon == "thermometer"


def _latest(session: Session, snapshot: StorcliSnapshot):
    insert_snapshot(session, snapshot)
    session.commit()
    latest = get_latest_snapshot(session)
    assert latest is not None
    return latest


def _snapshot(
    sample_snapshot: StorcliSnapshot,
    *,
    alarm_state: str = "Off",
    vd_states: tuple[str, ...] = ("Optl",),
    bbu_present: bool = True,
    cv_state: str = "Optimal",
    cv_replacement_required: bool = False,
    cachevault_present: bool = True,
    temperatures: tuple[int | None, ...] = (40,),
    roc_temperature_celsius: int | None = 78,
    raid_levels: tuple[str, ...] | None = None,
) -> StorcliSnapshot:
    controller = sample_snapshot.controller.model_copy(
        update={
            "alarm_state": alarm_state,
            "bbu_present": bbu_present,
            "roc_temperature_celsius": roc_temperature_celsius,
        }
    )
    virtual_drives = [
        sample_snapshot.virtual_drives[0].model_copy(
            update={"vd_id": index, "state": state, "raid_level": raid_level}
        )
        for index, (state, raid_level) in enumerate(
            zip(vd_states, raid_levels or ("RAID6",) * len(vd_states), strict=True)
        )
    ]
    physical_drives = [
        drive.model_copy(update={"temperature_celsius": temperature})
        for drive, temperature in zip(sample_snapshot.physical_drives, temperatures, strict=False)
    ]
    cachevault = sample_snapshot.cachevault
    if not cachevault_present:
        cachevault = None
    elif cachevault is not None:
        cachevault = cachevault.model_copy(
            update={
                "state": cv_state,
                "replacement_required": cv_replacement_required,
            }
        )
    return sample_snapshot.model_copy(
        update={
            "controller": controller,
            "virtual_drives": virtual_drives,
            "physical_drives": physical_drives,
            "cachevault": cachevault,
        }
    )
