from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services import controller_detail as detail_module
from megaraid_dashboard.services.controller_detail import (
    BuzzerControlState,
    ControllerDetailViewModel,
    _format_uptime,
    load_controller_detail_view_model,
)
from megaraid_dashboard.services.drive_actions import ConsistencyCheckStatus, PatrolReadStatus
from megaraid_dashboard.storcli import StorcliSnapshot

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "storcli" / "redacted"
APP_START_TIME = datetime(2026, 5, 20, 18, 35, tzinfo=UTC)


def test_controller_detail_smoke_optimal_controller(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot), raw_payload=_load_fixture("c0_show_all.json"))

    view_model = _load(session, tmp_path)

    assert isinstance(view_model, ControllerDetailViewModel)
    assert view_model.page_title == "Controller"
    assert view_model.health.state == "OPTIMAL"
    assert view_model.health.roc_temperature_celsius == 78
    assert view_model.health.cv_capacitance_percent == 89
    assert view_model.health.bbu_status == "Optimal"
    assert view_model.cachevault is not None
    assert view_model.cachevault.model == "CVPM02"
    assert view_model.cachevault.state == "Optimal"
    assert view_model.hardware.model == sample_snapshot.controller.model_name
    assert view_model.buzzer.current_setting == "On"
    assert view_model.foreign_config.present is False
    assert view_model.system_health.app_version == "0.1.0"
    assert view_model.auto_refresh_seconds == 30


def test_page_subtitle_uses_snapshot_captured_at(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    captured_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    _insert(
        session,
        _snapshot(sample_snapshot).model_copy(update={"captured_at": captured_at}),
    )

    view_model = _load(session, tmp_path)

    assert view_model.page_subtitle == "SN SV00000001. Updated Apr 25, 2026 12:00 UTC."


def test_live_operations_sorted_by_priority(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        detail_module,
        "_load_consistency_check_state",
        lambda snapshot: ConsistencyCheckStatus(
            mode="auto",
            state="active",
            progress_percent=22,
            last_run_timestamp=None,
            inconsistency_count=None,
            inconsistency_detail=None,
        ),
    )
    monkeypatch.setattr(
        detail_module,
        "_load_patrol_read_state",
        lambda snapshot: PatrolReadStatus(
            mode="auto",
            state="active",
            progress_percent=47,
            completed_drive_count=None,
            last_run_timestamp=None,
        ),
    )
    _insert(session, _snapshot(sample_snapshot, pd_state="Rebld"))

    view_model = _load(session, tmp_path)

    assert [card.name for card in view_model.live_operations] == [
        "Rebuild",
        "Consistency check",
        "Patrol read",
    ]
    assert [card.progress_percent for card in view_model.live_operations] == [None, 22, 47]


def test_cachevault_none_when_controller_has_no_cachevault(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    controller = sample_snapshot.controller.model_copy(
        update={"cv_present": False, "bbu_present": True}
    )
    _insert(
        session,
        sample_snapshot.model_copy(update={"controller": controller, "cachevault": None}),
    )

    view_model = _load(session, tmp_path)

    assert view_model.cachevault is None
    assert view_model.health.cv_capacitance_percent is None
    assert view_model.health.bbu_status == "Unknown"


def test_hardware_identity_all_fields_populated(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot), raw_payload=_load_fixture("c0_show_all.json"))

    hardware = _load(session, tmp_path).hardware

    assert hardware.model == "LSI MegaRAID SAS 9270CV-8i"
    assert hardware.serial == "SV00000001"
    assert hardware.revision == "27E"
    assert hardware.chip_revision == "D1"
    assert hardware.manufactured_date_text == "March 25, 2013"
    assert hardware.rework_date_text == "N/A"
    assert hardware.firmware_version == "3.460.115-6465"
    assert hardware.bios_version.startswith("5.50.03.0")
    assert hardware.driver_version == "07.727.03.00-rc1"
    assert hardware.sas_address == "5000000000000010"
    assert hardware.pci_address == "00:01:00:00"
    assert hardware.backend_ports == "8"
    assert hardware.nvram_size_text == "32KB"
    assert hardware.flash_size_text == "32MB"
    assert hardware.memory_size_text == "1024MB"
    assert hardware.fw_cache_size_text == "875 MB"
    assert hardware.pending_images_count == 0
    assert hardware.alarm_buzzer_text == "On"


def test_raid_config_one_row_per_vd_sorted_by_vd_id(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    first = sample_snapshot.virtual_drives[0]
    later_vd = first.model_copy(update={"vd_id": 3, "name": "later", "raid_level": "RAID1"})
    earlier_vd = first.model_copy(update={"vd_id": 1, "name": "earlier", "raid_level": "RAID5"})
    _insert(
        session,
        _snapshot(sample_snapshot).model_copy(update={"virtual_drives": [later_vd, earlier_vd]}),
        raw_payload={
            "Controllers": [
                {
                    "Response Data": {
                        "VD1 Properties": {"Strip Size": "64 KB"},
                        "VD3 Properties": {"Strip Size": "256 KB"},
                    }
                }
            ]
        },
    )

    rows = _load(session, tmp_path).raid_config

    assert [row.vd_id for row in rows] == [1, 3]
    assert [row.name for row in rows] == ["earlier", "later"]
    assert [row.strip_size_text for row in rows] == ["64 KB", "256 KB"]


def test_scheduled_tasks_include_patrol_read_next_run(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        detail_module,
        "_load_patrol_read_state",
        lambda snapshot: PatrolReadStatus(
            mode="auto",
            state="stopped",
            progress_percent=None,
            completed_drive_count=None,
            last_run_timestamp="2026-05-17 18:00:00",
        ),
    )
    _insert(session, _snapshot(sample_snapshot))

    task = _load(session, tmp_path).scheduled_tasks[0]

    assert task.name == "Patrol Read"
    assert task.is_enabled is True
    assert task.schedule_text == "Every 168h (7 days). Next May 24, 18:00."
    assert task.configure_url == "/controller/patrol-read/mode"


def test_controller_detail_loads_persisted_patrol_read_state(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(
        session,
        _snapshot(sample_snapshot),
        raw_payload={
            "operations": {
                "patrol_read": {"payload": _patrol_payload(mode="Disable", state="Not in progress")}
            }
        },
    )

    view_model = _load(session, tmp_path)
    card = view_model.live_operations[2]
    task = view_model.scheduled_tasks[0]

    assert card.name == "Patrol read"
    assert card.mode_text == "Disabled mode. Interval 168h."
    assert card.status_text == "Idle. Last completed May 4, 2026 01:00 UTC."
    assert card.can_start is True
    assert card.can_stop is False
    assert task.is_enabled is False
    assert task.schedule_text == "Every 168h (7 days). Next May 11, 01:00."


def test_controller_detail_loads_persisted_consistency_check_state(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(
        session,
        _snapshot(sample_snapshot),
        raw_payload={
            "consistency_check": {
                "show": _cc_show_payload(mode="Manual"),
                "progress": _cc_progress_payload(
                    state="Active 25%",
                    extra_props=[("CC Inconsistencies", "0")],
                ),
            }
        },
    )

    card = _load(session, tmp_path).live_operations[1]

    assert card.name == "Consistency check"
    assert card.mode_text == "Manual mode."
    assert card.status_text == "Running. 25%."
    assert card.progress_percent == 25
    assert card.progress_eta_text == "ETA unknown"
    assert card.can_start is False
    assert card.can_stop is True


def test_controller_detail_loads_top_level_consistency_check_state(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(
        session,
        _snapshot(sample_snapshot),
        raw_payload={
            "consistency_check_show": _cc_show_payload(mode="Auto"),
            "consistency_check_progress": _cc_progress_payload(state="Stopped"),
        },
    )

    card = _load(session, tmp_path).live_operations[1]

    assert card.mode_text == "Auto mode."
    assert card.status_text == "Idle. Last completed May 4, 2026 02:00 UTC."
    assert card.can_start is True
    assert card.can_stop is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("On", BuzzerControlState("On", True, True, False, "", "", "", "")),
        ("Off", BuzzerControlState("Off", False, False, True, "", "", "", "")),
        ("Silenced", BuzzerControlState("Silenced", False, True, False, "", "", "", "")),
    ],
)
def test_buzzer_state_transitions(raw: str, expected: BuzzerControlState) -> None:
    state = detail_module._build_buzzer_control_state(raw)

    assert state.current_setting == expected.current_setting
    assert state.can_silence is expected.can_silence
    assert state.can_disable is expected.can_disable
    assert state.can_enable is expected.can_enable
    assert state.silence_url == "/controller/buzzer/silence"
    assert state.disable_url == "/controller/buzzer/disable"
    assert state.enable_url == "/controller/buzzer/enable"


def test_foreign_config_state_absent_and_present() -> None:
    absent = detail_module._build_foreign_config_state(
        _load_fixture("c0_fall_show_all_absent.json")
    )
    present = detail_module._build_foreign_config_state(
        _load_fixture("c0_fall_show_all_present.json")
    )

    assert absent.present is False
    assert absent.drive_count == 0
    assert absent.can_import is False
    assert absent.can_clear is False
    assert present.present is True
    assert present.drive_count == 4
    assert present.description_text == "4 foreign drives detected."
    assert present.can_import is True
    assert present.can_clear is True
    assert present.import_url == "/controller/foreign-config/import"
    assert present.clear_url == "/controller/foreign-config/clear"


def test_foreign_config_state_reads_persisted_collector_wrapper() -> None:
    state = detail_module._build_foreign_config_state(
        {
            "controller": _load_fixture("c0_show_all.json"),
            "virtual_drives": _load_fixture("vall_show_all.json"),
            "foreign_config": _load_fixture("c0_fall_show_all_present.json"),
        }
    )

    assert state.present is True
    assert state.drive_count == 4
    assert state.can_import is True
    assert state.import_url == "/controller/foreign-config/import"


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(minutes=7, seconds=23), "7m 23s"),
        (timedelta(hours=2, minutes=15), "2h 15m"),
        (timedelta(days=3, hours=4), "3d 4h"),
    ],
)
def test_uptime_text_format(delta: timedelta, expected: str) -> None:
    assert _format_uptime(delta) == expected


def test_errors_24h_aggregates_warning_and_critical_events(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))
    now = datetime.now(UTC)
    session.add_all(
        [
            _event(severity="warning", occurred_at=now - timedelta(hours=1)),
            _event(severity="critical", occurred_at=now - timedelta(hours=2)),
            _event(severity="info", occurred_at=now - timedelta(hours=3)),
            _event(severity="critical", occurred_at=now - timedelta(days=2)),
        ]
    )
    session.commit()

    assert _load(session, tmp_path).health.errors_24h == 2


def test_roc_history_chart_url_points_to_future_endpoint(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))

    assert _load(session, tmp_path).roc_history_chart_url == "/controller/roc-temperature/history"


def test_empty_database_builds_unknown_controller_detail(session: Session, tmp_path: Path) -> None:
    view_model = load_controller_detail_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=False,
        app_start_time=datetime(2026, 5, 20, 18, 35),
        app_version="0.1.0",
    )

    assert view_model.health.state == "UNKNOWN"
    assert view_model.health.summary_text == "Waiting for first controller snapshot."
    assert view_model.live_operations[0].status_text == "Idle."
    assert view_model.raid_config == []
    assert view_model.hardware.model == "N/A"
    assert view_model.scheduled_tasks[0].schedule_text == "Every 168h (7 days). Next: unknown."


def test_health_summary_handles_critical_and_missing_vds(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    failed_snapshot = _snapshot(sample_snapshot, pd_state="Failed").model_copy(
        update={"virtual_drives": []}
    )
    _insert(session, failed_snapshot)

    view_model = _load(session, tmp_path)

    assert view_model.health.state == "CRITICAL"
    assert view_model.health.summary_text == (
        "Critical controller condition. 7/8 drives online. RAID status unknown."
    )


def test_multiple_virtual_drives_summary(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    first = sample_snapshot.virtual_drives[0]
    _insert(
        session,
        _snapshot(sample_snapshot).model_copy(
            update={"virtual_drives": [first, first.model_copy(update={"vd_id": 2})]}
        ),
    )

    assert "2 virtual drives configured" in _load(session, tmp_path).health.summary_text


def test_operation_status_idle_after_completed_run() -> None:
    status = PatrolReadStatus(
        mode="auto",
        state="stopped",
        progress_percent=None,
        completed_drive_count=None,
        last_run_timestamp="2026-05-20 18:00:00",
    )

    assert detail_module._operation_status_text(status, idle_label="Idle.") == (
        "Idle. Last completed May 20, 2026 18:00 UTC."
    )
    assert (
        detail_module._operation_status_text(
            PatrolReadStatus(
                mode="auto",
                state="stopped",
                progress_percent=None,
                completed_drive_count=None,
                last_run_timestamp=None,
            ),
            idle_label="Idle.",
        )
        == "Idle."
    )


def test_scheduler_next_run_uses_job_when_present(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _snapshot(sample_snapshot))
    scheduler = _Scheduler(
        last_run_at=None,
        job=_SchedulerJob(datetime(2026, 5, 25, 1, 30, tzinfo=UTC)),
    )

    view_model = load_controller_detail_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=scheduler,
        collector_enabled=True,
        app_start_time=APP_START_TIME,
        app_version="0.1.0",
    )

    assert view_model.scheduled_tasks[0].schedule_text == "Every 168h (7 days). Next May 25, 01:30."


def test_raw_helper_edge_cases() -> None:
    assert detail_module._parse_date("not-a-date") is None
    assert detail_module._pending_images_count({"Pending Images in Flash": {}}) == 0
    assert (
        detail_module._pending_images_count(
            {"Pending Images in Flash": {"Image name": "firmware.rom"}}
        )
        == 1
    )
    assert detail_module._pending_images_count({"Pending Images in Flash": ["a", "b"]}) == 2
    assert detail_module._pending_images_count({"Pending Images in Flash": "3"}) == 3
    assert detail_module._normalize_alarm_state(None) == "Off"
    assert detail_module._find_raw_value({}) is None
    assert detail_module._find_raw_value({}, "missing") is None
    assert detail_module._find_raw_value({"Nested": [{"Property": "X", "Value": "Y"}]}, "X") == "Y"
    assert detail_module._first_response_data({"Controllers": ["bad"]}) == {}
    assert detail_module._first_response_data({"Controllers": [{"Response Data": []}]}) == {}
    assert detail_module._first_present(None, "") == "N/A"
    assert detail_module._storcli_payload({"payload": {}}) is None
    assert detail_module._parse_int(True) is None
    assert detail_module._parse_int(None) is None
    assert detail_module._parse_int("n/a") is None
    assert detail_module._require_aware_utc(datetime(2026, 5, 20, 18, 0)).tzinfo is UTC


def _load(session: Session, tmp_path: Path) -> ControllerDetailViewModel:
    return load_controller_detail_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=_Scheduler(last_run_at=datetime(2026, 5, 20, 18, 40, tzinfo=UTC)),
        collector_enabled=True,
        app_start_time=APP_START_TIME,
        app_version="0.1.0",
    )


def _insert(
    session: Session,
    snapshot: StorcliSnapshot,
    *,
    raw_payload: dict[str, Any] | None = None,
) -> None:
    insert_snapshot(
        session,
        snapshot,
        store_raw=raw_payload is not None,
        raw_payload=raw_payload,
    )
    session.commit()


def _snapshot(
    sample_snapshot: StorcliSnapshot,
    *,
    pd_state: str = "Onln",
) -> StorcliSnapshot:
    controller = sample_snapshot.controller.model_copy(update={"roc_temperature_celsius": 78})
    physical_drives = [
        drive.model_copy(update={"state": pd_state if index == 0 else "Onln"})
        for index, drive in enumerate(sample_snapshot.physical_drives)
    ]
    return sample_snapshot.model_copy(
        update={
            "controller": controller,
            "physical_drives": physical_drives,
        }
    )


def _event(*, severity: str, occurred_at: datetime) -> Event:
    return Event(
        occurred_at=occurred_at,
        severity=severity,
        category="controller",
        subject="controller",
        summary=f"{severity} event",
    )


def _settings(tmp_path: Path) -> Settings:
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
        database_url=f"sqlite:///{tmp_path / 'megaraid.db'}",
        log_level="INFO",
    )


@dataclass(frozen=True)
class _Scheduler:
    last_run_at: datetime | None
    job: _SchedulerJob | None = None

    def get_last_collector_run_at(self) -> datetime | None:
        return self.last_run_at

    def get_job(self, job_id: str) -> _SchedulerJob | None:
        del job_id
        return self.job


@dataclass(frozen=True)
class _SchedulerJob:
    next_run_time: datetime | None


def _load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _patrol_payload(
    *,
    mode: str,
    state: str,
    extra_props: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    controller_properties = [
        {"Ctrl_Prop": "PR Mode", "Value": mode},
        {"Ctrl_Prop": "PR Current State", "Value": state},
        {"Ctrl_Prop": "PR Last Run", "Value": "2026-05-04 01:00:00"},
    ]
    controller_properties.extend(
        {"Ctrl_Prop": key, "Value": value} for key, value in (extra_props or [])
    )
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {"Controller Properties": controller_properties},
            }
        ]
    }


def _cc_show_payload(*, mode: str) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "Controller Properties": [
                        {"Ctrl_Prop": "CC Mode", "Value": mode},
                        {"Ctrl_Prop": "CC Last Run", "Value": "2026-05-04 02:00:00"},
                    ]
                },
            }
        ]
    }


def _cc_progress_payload(
    *,
    state: str,
    extra_props: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    controller_properties = [{"Ctrl_Prop": "CC Current State", "Value": state}]
    controller_properties.extend(
        {"Ctrl_Prop": key, "Value": value} for key, value in (extra_props or [])
    )
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {"VD Operation Status": controller_properties},
            }
        ]
    }
