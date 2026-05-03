from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import get_latest_snapshot, insert_snapshot
from megaraid_dashboard.services.overview import (
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
    snapshot = _latest(session, _snapshot(sample_snapshot, vd_states=("Optl", "Degraded")))

    vd_tile = _load_vd_tile(snapshot)
    raid_tile = _load_raid_tile(snapshot)

    assert vd_tile.value == "1 degraded"
    assert vd_tile.status == "warning"
    assert raid_tile.value == "RAID6"
    assert raid_tile.status == "warning"


def test_bbu_tile_is_neutral_when_bbu_is_absent(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, bbu_present=False))

    tile = _load_bbu_tile(snapshot)

    assert tile.value == "None"
    assert tile.status == "neutral"
    assert tile.icon == "lightbulb"


def test_bbu_tile_warns_for_present_degraded_cachevault(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, cv_state="Degraded"))

    tile = _load_bbu_tile(snapshot)

    assert tile.value == "Warning"
    assert tile.status == "warning"


def test_max_temp_tile_uses_hottest_drive_and_thresholds(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, temperatures=(42, 51, 58)))

    tile = _load_max_temp_tile(snapshot, settings=get_settings())

    assert tile.value == "58 C"
    assert tile.status == "warning"
    assert tile.href == "/drives?sort=temperature-desc"


def test_max_temp_tile_is_neutral_without_physical_drives(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _latest(session, _snapshot(sample_snapshot, temperatures=()))

    tile = _load_max_temp_tile(snapshot, settings=get_settings())

    assert tile.value == "Unknown"
    assert tile.status == "neutral"


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
    temperatures: tuple[int | None, ...] = (40,),
    roc_temperature_celsius: int | None = 78,
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
            update={"vd_id": index, "state": state, "raid_level": "RAID6"}
        )
        for index, state in enumerate(vd_states)
    ]
    physical_drives = [
        drive.model_copy(update={"temperature_celsius": temperature})
        for drive, temperature in zip(sample_snapshot.physical_drives, temperatures, strict=False)
    ]
    cachevault = sample_snapshot.cachevault
    if cachevault is not None:
        cachevault = cachevault.model_copy(update={"state": cv_state})
    return sample_snapshot.model_copy(
        update={
            "controller": controller,
            "virtual_drives": virtual_drives,
            "physical_drives": physical_drives,
            "cachevault": cachevault,
        }
    )
