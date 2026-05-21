from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services import overview as overview_module
from megaraid_dashboard.services.overview import load_main_page_view_model
from megaraid_dashboard.storcli import StorcliSnapshot


def test_main_page_with_optimal_state(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=_Scheduler(datetime(2026, 4, 25, 12, 0, tzinfo=UTC)),
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.state == "OPTIMAL"
    assert view_model.controller.model == sample_snapshot.controller.model_name
    assert view_model.controller.raid_summary == "RAID6 (1 VD, 8 PD)"
    assert view_model.controller.active_operations == []
    assert view_model.drive_grid.worst_severity == "optimal"
    assert {tile.tile_severity for tile in view_model.drive_grid.tiles} == {"optimal"}
    assert view_model.system_health.app_version == "0.1.0"


def test_main_page_with_warning_drive_temperature(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot, temperatures=(56,)))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.state == "OPTIMAL"
    assert view_model.drive_grid.tiles[0].temperature_state == "warning"
    assert view_model.drive_grid.tiles[0].tile_severity == "warning"
    assert view_model.drive_grid.worst_severity == "warning"


def test_main_page_with_failed_drive(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot, pd_state="Failed"))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.state == "CRITICAL"
    assert view_model.drive_grid.tiles[0].state_severity == "critical"
    assert view_model.drive_grid.tiles[0].tile_severity == "critical"


def test_main_page_with_active_patrol_read(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))
    monkeypatch.setattr(
        overview_module,
        "_load_patrol_read_state",
        lambda snapshot: _OperationState(progress_percent=47),
    )

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.active_operations == [
        overview_module.ActiveOperation(
            name="Patrol read",
            progress_percent=47,
            tooltip="Controller, ETA unknown",
        )
    ]


def test_main_page_with_active_consistency_check_and_patrol_read(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))
    monkeypatch.setattr(
        overview_module,
        "_load_patrol_read_state",
        lambda snapshot: _OperationState(progress_percent=47),
    )
    monkeypatch.setattr(
        overview_module,
        "_load_consistency_check_state",
        lambda snapshot: _OperationState(progress_percent=22),
    )

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert [operation.name for operation in view_model.controller.active_operations] == [
        "Consistency check",
        "Patrol read",
    ]
    assert [
        operation.progress_percent for operation in view_model.controller.active_operations
    ] == [
        22,
        47,
    ]


def test_main_page_error_badge_aggregation(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(
        session,
        _snapshot(sample_snapshot, media_errors=3, other_errors=2, predictive_failures=1),
    )

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.drive_grid.tiles[0].error_badge_count == 6


def test_main_page_error_badge_hidden_when_all_zero(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.drive_grid.tiles[0].error_badge_count is None


def test_main_page_drive_grid_sort_order(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(sample_snapshot)
    drives = list(reversed(snapshot.physical_drives))
    _insert(session, snapshot.model_copy(update={"physical_drives": drives}))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert [(tile.enclosure_id, tile.slot_id) for tile in view_model.drive_grid.tiles] == sorted(
        (drive.enclosure_id, drive.slot_id) for drive in drives
    )


def test_main_page_recent_activity_limit_10(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))
    base_time = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    for index in range(25):
        session.add(_event(occurred_at=base_time + timedelta(minutes=index), summary=str(index)))
    session.commit()

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert [item.message for item in view_model.recent_activity] == [
        "24",
        "23",
        "22",
        "21",
        "20",
        "19",
        "18",
        "17",
        "16",
        "15",
    ]


def test_main_page_system_health_collector_never_ran(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=_Scheduler(None),
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.system_health.collector_last_run_at is None
    assert view_model.system_health.collector_last_run_text == "never"


def test_main_page_system_health_db_size_format(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "megaraid.db"
    db_path.write_bytes(b"0" * 6_766_592)
    _insert(session, _snapshot(sample_snapshot))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path, database_url=f"sqlite:///{db_path}"),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.system_health.db_size_human == "6.4 MB"


def test_main_page_view_model_uses_snapshot_capture_time_for_updated_at(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.updated_at == sample_snapshot.captured_at
    assert view_model.auto_refresh_seconds == 30


def test_main_page_empty_database(
    session: Session,
    tmp_path: Path,
) -> None:
    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.state == "UNKNOWN"
    assert view_model.controller.raid_summary == "Unknown"
    assert view_model.drive_grid.tiles == []
    assert view_model.drive_grid.worst_severity == "optimal"


def test_main_page_with_rebuild_operation(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot, pd_state="Rebld"))

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.active_operations[0] == overview_module.ActiveOperation(
        name="Rebuild",
        progress_percent=None,
        tooltip="Drive 252:0, ETA unknown",
    )


def test_main_page_active_operation_tooltip_with_started_at(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))
    monkeypatch.setattr(
        overview_module,
        "_load_patrol_read_state",
        lambda snapshot: _OperationState(
            progress_percent=47,
            last_run_timestamp="2026-04-25 18:15:00",
        ),
    )

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.active_operations[0].tooltip == (
        "Controller, started 18:15 - ETA unknown"
    )
    assert view_model.controller.last_patrol_read_completed_at == datetime(
        2026,
        4,
        25,
        18,
        15,
        tzinfo=UTC,
    )


def test_main_page_foreign_import_operation_priority(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))
    foreign_operation = overview_module.ActiveOperation(
        name="Foreign config import",
        progress_percent=None,
        tooltip="ETA unknown",
    )
    monkeypatch.setattr(
        overview_module,
        "_load_foreign_config_import_operation",
        lambda snapshot: foreign_operation,
    )

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.active_operations == [foreign_operation]


def test_main_page_bbu_status_without_cachevault(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    controller_without_bbu = sample_snapshot.controller.model_copy(
        update={"cv_present": False, "bbu_present": False}
    )
    _insert(
        session,
        sample_snapshot.model_copy(
            update={"controller": controller_without_bbu, "cachevault": None}
        ),
    )

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.bbu_status == "N/A"


def test_main_page_bbu_status_cv_only(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    controller_cv_only = sample_snapshot.controller.model_copy(
        update={"cv_present": True, "bbu_present": False}
    )
    _insert(
        session, _snapshot(sample_snapshot).model_copy(update={"controller": controller_cv_only})
    )

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.bbu_status == "N/A"


def test_main_page_bbu_status_unknown_when_bbu_present_without_snapshot(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    controller_with_bbu = sample_snapshot.controller.model_copy(
        update={"cv_present": False, "bbu_present": True}
    )
    _insert(
        session,
        sample_snapshot.model_copy(update={"controller": controller_with_bbu, "cachevault": None}),
    )

    view_model = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="0.1.0",
    )

    assert view_model.controller.bbu_status == "Unknown"


def _insert(session: Session, snapshot: StorcliSnapshot) -> None:
    insert_snapshot(session, snapshot)
    session.commit()


def _snapshot(
    sample_snapshot: StorcliSnapshot,
    *,
    vd_state: str = "Optl",
    pd_state: str = "Onln",
    temperatures: tuple[int | None, ...] = (40,),
    media_errors: int = 0,
    other_errors: int = 0,
    predictive_failures: int = 0,
) -> StorcliSnapshot:
    virtual_drive = sample_snapshot.virtual_drives[0].model_copy(
        update={"state": vd_state, "raid_level": "RAID6"}
    )
    physical_drives = [
        drive.model_copy(
            update={
                "state": pd_state if index == 0 else "Onln",
                "temperature_celsius": temperatures[index] if index < len(temperatures) else 40,
                "media_errors": media_errors if index == 0 else 0,
                "other_errors": other_errors if index == 0 else 0,
                "predictive_failures": predictive_failures if index == 0 else 0,
            }
        )
        for index, drive in enumerate(sample_snapshot.physical_drives)
    ]
    return sample_snapshot.model_copy(
        update={
            "virtual_drives": [virtual_drive],
            "physical_drives": physical_drives,
        }
    )


def _event(*, occurred_at: datetime, summary: str) -> Event:
    return Event(
        occurred_at=occurred_at,
        severity="warning",
        category="pd_state",
        subject="e252:s0",
        summary=summary,
    )


def _settings(tmp_path: Path, *, database_url: str | None = None) -> Settings:
    return Settings(
        alert_smtp_host="smtp.example.test",
        alert_smtp_port=587,
        alert_smtp_user="alert@example.test",
        alert_smtp_password="test-token",
        alert_from="alert@example.test",
        alert_to="ops@example.test",
        admin_username="admin",
        admin_password_hash="hash",
        storcli_path="/usr/local/sbin/storcli64",
        metrics_interval_seconds=300,
        database_url=database_url or f"sqlite:///{tmp_path / 'megaraid.db'}",
        log_level="INFO",
    )


@dataclass(frozen=True)
class _Scheduler:
    last_run_at: datetime | None

    def get_last_collector_run_at(self) -> datetime | None:
        return self.last_run_at


@dataclass(frozen=True)
class _OperationState:
    progress_percent: int | None
    is_running: bool = True
    last_run_timestamp: str | None = None
