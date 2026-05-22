from __future__ import annotations

# ruff: noqa: I001

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.services.overview import (
    load_drive_list_view_model,
)
from megaraid_dashboard.storcli import StorcliSnapshot


@pytest.fixture(autouse=True)
def overview_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def test_drive_list_marks_non_online_info_state_as_warning(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, pd_state="Rbld"))

    view_model = load_drive_list_view_model(
        session,
        slot_url_factory=lambda enclosure_id, slot_id: f"/drives/{enclosure_id}:{slot_id}",
    )

    rebuilding_drive = view_model.physical_drives[0]
    assert rebuilding_drive.state == "Rbld"
    assert rebuilding_drive.row_state == "warning"
    assert rebuilding_drive.status_icon == "alert-triangle"
    assert view_model.drive_summary.optimal == 7
    assert view_model.drive_summary.warning == 1
    assert view_model.drive_summary.critical == 0


def _insert(session: Session, snapshot: StorcliSnapshot) -> None:
    insert_snapshot(session, snapshot)
    session.commit()


def _snapshot(
    sample_snapshot: StorcliSnapshot,
    *,
    vd_state: str = "Optl",
    pd_state: str = "Onln",
    temperatures: tuple[int | None, ...] = (40,),
    cv_state: str = "Optimal",
    cv_replacement_required: bool = False,
    cv_capacitance_percent: int | None = 89,
    cachevault_present: bool = True,
    roc_temperature_celsius: int | None = 78,
) -> StorcliSnapshot:
    controller = sample_snapshot.controller.model_copy(
        update={
            "alarm_state": "Off",
            "roc_temperature_celsius": roc_temperature_celsius,
        }
    )
    virtual_drive = sample_snapshot.virtual_drives[0].model_copy(
        update={"state": vd_state, "raid_level": "RAID6"}
    )
    physical_drives = [
        drive.model_copy(
            update={
                "state": pd_state if index == 0 else "Onln",
                "temperature_celsius": temperatures[index] if index < len(temperatures) else 40,
            }
        )
        for index, drive in enumerate(sample_snapshot.physical_drives)
    ]
    cachevault = None
    if cachevault_present:
        assert sample_snapshot.cachevault is not None
        cachevault = sample_snapshot.cachevault.model_copy(
            update={
                "state": cv_state,
                "replacement_required": cv_replacement_required,
                "capacitance_percent": cv_capacitance_percent,
            }
        )
    return sample_snapshot.model_copy(
        update={
            "controller": controller,
            "virtual_drives": [virtual_drive],
            "physical_drives": physical_drives,
            "cachevault": cachevault,
        }
    )
