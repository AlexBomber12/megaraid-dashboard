from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services import controller_detail as controller_detail_module
from megaraid_dashboard.services.controller_detail import (
    BuzzerControlState,
    HardwareIdentity,
    _format_uptime,
    load_controller_detail_view_model,
)
from megaraid_dashboard.services.drive_actions import ConsistencyCheckStatus, PatrolReadStatus
from megaraid_dashboard.storcli import StorcliSnapshot


def test_controller_detail_smoke_populates_all_sections(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _optimal_snapshot(sample_snapshot), raw_json=_raw_controller_payload())

    view_model = load_controller_detail_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=_Scheduler(datetime(2026, 5, 20, 18, 42, tzinfo=UTC)),
        collector_enabled=True,
        app_start_time=datetime.now(UTC) - timedelta(minutes=7, seconds=23),
        app_version="0.1.0",
        roc_history_chart_url="/controller/roc-history",
    )

    assert view_model.page_title == "Controller"
    assert view_model.page_subtitle.startswith("SN SV00000001. Updated ")
    assert view_model.health.state == "OPTIMAL"
    assert view_model.health.roc_temperature_celsius == 70
    assert view_model.health.cv_capacitance_percent == 89
    assert view_model.health.bbu_status == "Optimal"
    assert view_model.health.memory_ecc_errors_total == 0
    assert view_model.health.uptime_text.endswith(("m 23s", "m 22s"))
    assert view_model.cachevault is not None
    assert view_model.cachevault.model == "CVPM02"
    assert view_model.raid_config[0].raid_level == "RAID5"
    assert view_model.scheduled_tasks[0].name == "Patrol Read"
    assert view_model.hardware.serial == "SV00000001"
    assert view_model.buzzer.current_setting == "On"
    assert not view_model.foreign_config.present
    assert view_model.system_health.app_version == "0.1.0"
    assert view_model.auto_refresh_seconds == 30


def test_live_operations_sorted_by_priority(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    base_snapshot = _optimal_snapshot(sample_snapshot)
    rebuilding = base_snapshot.physical_drives[0].model_copy(update={"state": "Rbld"})
    snapshot = base_snapshot.model_copy(
        update={"physical_drives": [rebuilding, *base_snapshot.physical_drives[1:]]}
    )
    _insert(session, snapshot, raw_json=_raw_controller_payload())
    monkeypatch.setattr(
        controller_detail_module,
        "_load_consistency_check_state",
        lambda _snapshot: ConsistencyCheckStatus(
            mode="manual",
            state="active",
            progress_percent=22,
            last_run_timestamp=None,
            inconsistency_count=None,
            inconsistency_detail=None,
        ),
    )
    monkeypatch.setattr(
        controller_detail_module,
        "_load_patrol_read_state",
        lambda _snapshot: PatrolReadStatus(
            mode="auto",
            state="active",
            progress_percent=47,
            completed_drive_count=None,
            last_run_timestamp=None,
        ),
    )

    view_model = _load(session, tmp_path)

    assert [operation.name for operation in view_model.live_operations] == [
        "Rebuild",
        "Consistency Check",
        "Patrol Read",
    ]


def test_cachevault_is_none_when_controller_has_no_cachevault(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    base_snapshot = _optimal_snapshot(sample_snapshot)
    controller = base_snapshot.controller.model_copy(update={"cv_present": False})
    snapshot = base_snapshot.model_copy(update={"controller": controller, "cachevault": None})
    _insert(session, snapshot, raw_json=_raw_controller_payload())

    assert _load(session, tmp_path).cachevault is None


def test_hardware_identity_all_fields_populated(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _optimal_snapshot(sample_snapshot), raw_json=_raw_controller_payload())

    hardware = _load(session, tmp_path).hardware

    for field in fields(HardwareIdentity):
        value = getattr(hardware, field.name)
        assert value != ""
        assert value is not None
    assert hardware.revision == "27E"
    assert hardware.chip_revision == "B0"
    assert hardware.pending_images_count == 1
    assert hardware.fw_cache_size_text == "875 MB"


def test_raid_config_has_one_sorted_row_per_virtual_drive(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    base_snapshot = _optimal_snapshot(sample_snapshot)
    first = base_snapshot.virtual_drives[0].model_copy(update={"vd_id": 2, "name": "second"})
    second = base_snapshot.virtual_drives[0].model_copy(update={"vd_id": 1, "name": "first"})
    snapshot = base_snapshot.model_copy(update={"virtual_drives": [first, second]})
    _insert(session, snapshot, raw_json=_raw_controller_payload())

    rows = _load(session, tmp_path).raid_config

    assert [row.vd_id for row in rows] == [1, 2]
    assert [row.name for row in rows] == ["first", "second"]


def test_scheduled_tasks_include_patrol_read_next_run(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    raw_json = _raw_controller_payload()
    raw_json["foreign_config"] = {"present": False}
    _insert(session, _optimal_snapshot(sample_snapshot), raw_json=raw_json)

    tasks = _load(session, tmp_path).scheduled_tasks

    assert tasks == [
        controller_detail_module.ScheduledTaskRow(
            name="Patrol Read",
            schedule_text="Every 168h (7 days). Next May 24, 18:00.",
            is_enabled=True,
            configure_url="/controller/patrol-read/mode",
        )
    ]


@pytest.mark.parametrize(
    ("alarm_state", "expected"),
    [
        ("On", BuzzerControlState("On", True, True, False, "", "", "", "")),
        ("Silenced", BuzzerControlState("Silenced", False, True, False, "", "", "", "")),
        ("Off", BuzzerControlState("Off", False, False, True, "", "", "", "")),
    ],
)
def test_buzzer_state_transitions_map_to_capabilities(
    alarm_state: str,
    expected: BuzzerControlState,
) -> None:
    state = controller_detail_module._build_buzzer_control_state(alarm_state)

    assert state.current_setting == expected.current_setting
    assert state.can_silence is expected.can_silence
    assert state.can_disable is expected.can_disable
    assert state.can_enable is expected.can_enable
    assert state.silence_url == "/controller/buzzer/silence"
    assert state.disable_url == "/controller/buzzer/disable"
    assert state.enable_url == "/controller/buzzer/enable"


def test_foreign_config_state_absent_and_present() -> None:
    absent = controller_detail_module._build_foreign_config_state(_raw_controller_payload())

    assert not absent.present
    assert absent.drive_count == 0
    assert not absent.can_import
    assert not absent.can_clear

    raw_json = _raw_controller_payload()
    raw_json["foreign_config"] = {
        "present": True,
        "drive_count": 4,
        "source_controller_serial": "OLD123",
    }
    present = controller_detail_module._build_foreign_config_state(raw_json)

    assert present.present
    assert present.drive_count == 4
    assert present.source_controller_serial == "OLD123"
    assert present.can_import
    assert present.can_clear


def test_foreign_config_state_parses_stored_storcli_payload() -> None:
    raw_json = _raw_controller_payload()
    raw_json["foreign_config"] = _load_redacted_fixture("c0_fall_show_all_present.json")

    present = controller_detail_module._build_foreign_config_state(raw_json)

    assert present.present
    assert present.drive_count == 4
    assert present.description_text == "Foreign configuration detected on 4 drive(s)."
    assert present.can_import
    assert present.can_clear


def test_foreign_config_state_treats_unparseable_stored_payload_as_absent() -> None:
    raw_json = {
        "foreign_config": {
            "Controllers": [
                {
                    "Command Status": {
                        "Status": "Failure",
                        "Detailed Status": [
                            {"Status": "Failure", "ErrCd": 99, "ErrMsg": "adapter offline"}
                        ],
                    }
                }
            ]
        }
    }

    absent = controller_detail_module._build_foreign_config_state(raw_json)

    assert not absent.present
    assert absent.drive_count == 0
    assert not absent.can_import
    assert not absent.can_clear


def test_strip_size_resolves_nested_virtual_drive_properties() -> None:
    raw_json = {
        "controller": {
            "Controllers": [
                {
                    "Response Data": {
                        "Defaults": {"Strip Size": "64 KB"},
                    }
                }
            ]
        },
        "virtual_drives": {
            "Controllers": [
                {
                    "Response Data": {
                        "VD0 Properties": {"Strip Size": "256 KB"},
                        "VD1 Properties": {"Strip Size": "512 KB"},
                    }
                }
            ]
        },
    }

    assert controller_detail_module._strip_size_text(raw_json, 0) == "256 KB"
    assert controller_detail_module._strip_size_text(raw_json, 1) == "512 KB"


def test_strip_size_does_not_use_controller_default_for_missing_vd_properties() -> None:
    raw_json = {
        "controller": {
            "Controllers": [
                {
                    "Response Data": {
                        "Defaults": {"Strip Size": "64 KB"},
                    }
                }
            ]
        }
    }

    assert controller_detail_module._strip_size_text(raw_json, 1) == "N/A"


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
    _insert(session, _optimal_snapshot(sample_snapshot), raw_json=_raw_controller_payload())
    now = datetime.now(UTC)
    session.add_all(
        [
            _event(severity="warning", occurred_at=now - timedelta(hours=1)),
            _event(severity="critical", occurred_at=now - timedelta(hours=2)),
            _event(severity="info", occurred_at=now - timedelta(hours=3)),
            _event(severity="warning", occurred_at=now - timedelta(days=2)),
        ]
    )
    session.commit()

    assert _load(session, tmp_path).health.errors_24h == 2


def test_roc_history_chart_url_points_to_endpoint(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _optimal_snapshot(sample_snapshot), raw_json=_raw_controller_payload())

    assert _load(session, tmp_path).roc_history_chart_url == "/controller/roc-history"


def test_roc_history_chart_url_can_include_forwarded_prefix(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    _insert(session, _optimal_snapshot(sample_snapshot), raw_json=_raw_controller_payload())

    view_model = _load(
        session,
        tmp_path,
        roc_history_chart_url="/raid/controller/roc-history",
    )

    assert view_model.roc_history_chart_url == "/raid/controller/roc-history"


def test_empty_controller_detail_when_no_snapshot(session: Session, tmp_path: Path) -> None:
    view_model = _load(session, tmp_path)

    assert view_model.page_subtitle.startswith("SN Unknown.")
    assert view_model.health.state == "UNKNOWN"
    assert view_model.live_operations == []
    assert view_model.cachevault is None
    assert view_model.raid_config == []
    assert view_model.hardware.model == "N/A"


def test_warning_health_summary_and_missing_bbu_status(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    tmp_path: Path,
) -> None:
    base_snapshot = _optimal_snapshot(sample_snapshot)
    controller = base_snapshot.controller.model_copy(
        update={"roc_temperature_celsius": 100, "bbu_present": False, "cv_present": False}
    )
    snapshot = base_snapshot.model_copy(update={"controller": controller, "cachevault": None})
    _insert(session, snapshot, raw_json={})

    health = _load(session, tmp_path).health

    assert health.state == "WARNING"
    assert health.summary_text.startswith("Controller requires attention.")
    assert health.bbu_status == "N/A"


def test_private_mapping_helpers_cover_fallbacks() -> None:
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    assert controller_detail_module._dominant_raid_level([]) == "RAID unknown"
    assert controller_detail_module._memory_ecc_errors_total(None) == 0
    assert controller_detail_module._format_uptime(timedelta(seconds=12)) == "12s"
    assert controller_detail_module._format_bytes(999) == "999 B"
    assert controller_detail_module._format_size_text(None) == "N/A"
    assert controller_detail_module._format_size_text("875", default_unit="MB") == "875 MB"
    assert controller_detail_module._format_manufacture_date(None, now=now) == "N/A"
    assert controller_detail_module._first_raw_text({}, "missing") is None
    assert (
        controller_detail_module._find_raw_value([{"Property": "Answer", "Value": 42}], "Answer")
        == 42
    )
    assert (
        controller_detail_module._find_raw_value([{"nested": {"Key": "value"}}], "Key") == "value"
    )
    assert controller_detail_module._property_value(["bad"], "Answer") is None
    assert controller_detail_module._pending_images_count({"Image name": ["a", "b"]}) == 2
    assert controller_detail_module._pending_images_count({"Image name": "N/A"}) == 0
    assert controller_detail_module._parse_interval_hours(None) is None
    assert controller_detail_module._parse_interval_hours("every 7 days") == 168
    assert controller_detail_module._parse_interval_hours("manual") is None
    assert controller_detail_module._parse_timestamp(None) is None
    assert controller_detail_module._parse_timestamp("2026/05/20 12:00:00") == now
    assert controller_detail_module._parse_timestamp("2026-05-20 12:00:00") == now
    assert controller_detail_module._parse_timestamp("May 20, 2026 12:00") == now
    assert controller_detail_module._parse_timestamp("not a timestamp") is None
    assert controller_detail_module._parse_date(None) is None
    assert controller_detail_module._parse_date("05/20/2026").isoformat() == "2026-05-20"
    assert controller_detail_module._parse_date("20/05/2026").isoformat() == "2026-05-20"
    assert controller_detail_module._parse_date("bad") is None
    assert controller_detail_module._snapshot_text(None, "model_name") == "N/A"
    assert controller_detail_module._normalize_alarm_state("enabled") == "On"
    assert controller_detail_module._normalize_alarm_state("muted") == "Silenced"
    assert controller_detail_module._normalize_alarm_state(None) == "Off"
    assert controller_detail_module._truthy(True)
    assert controller_detail_module._truthy(1)
    assert controller_detail_module._truthy("present")
    assert not controller_detail_module._truthy(object())
    assert controller_detail_module._int_or_zero(True) == 0
    assert controller_detail_module._int_or_zero(1.8) == 1
    assert controller_detail_module._int_or_zero("drive_count=4") == 4
    assert controller_detail_module._int_or_zero("none") == 0
    assert controller_detail_module._optional_text(None) is None
    assert controller_detail_module._optional_text("   ") is None
    assert controller_detail_module._require_aware_utc(datetime(2026, 5, 20, 12, 0)) == now


def test_operation_and_schedule_helper_fallbacks() -> None:
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    idle_patrol = PatrolReadStatus(
        mode="",
        state="stopped",
        progress_percent=77,
        completed_drive_count=None,
        last_run_timestamp=None,
    )
    idle_cc = ConsistencyCheckStatus(
        mode="manual",
        state="stopped",
        progress_percent=44,
        last_run_timestamp="05/20/2026, 10:00:00",
        inconsistency_count=None,
        inconsistency_detail=None,
    )

    patrol_card = controller_detail_module._patrol_read_card(idle_patrol, now=now)
    cc_card = controller_detail_module._consistency_check_card(idle_cc, now=now)

    assert patrol_card.mode_text == "Unknown mode. Interval 168h."
    assert patrol_card.status_text == "Stopped. Last completion unknown."
    assert patrol_card.progress_percent is None
    assert patrol_card.progress_eta_text is None
    assert cc_card.status_text == "Stopped. Last completed May 20 (2h 0m ago)."
    assert cc_card.progress_percent is None
    assert controller_detail_module._schedule_text(None, None, None, now=now) == "Next: unknown."
    assert (
        controller_detail_module._schedule_text(5, None, None, now=now)
        == "Every 5h. Next: unknown."
    )
    assert (
        controller_detail_module._schedule_text(
            168,
            datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            None,
            now=now,
        )
        == "Every 168h (7 days). Next May 24, 12:00."
    )
    assert controller_detail_module._mode_text("manual", interval_hours=None) == "Manual mode."
    assert controller_detail_module._strip_size_text({"VD1 Strip Size": "512"}, 1) == "512 KB"
    assert (
        controller_detail_module._strip_size_text(
            {"VD1 Properties": {"Other": "value"}, "VD1 Strip Size": "128"},
            1,
        )
        == "128 KB"
    )
    assert controller_detail_module._vd_state_severity("Failed") == "critical"
    assert controller_detail_module._vd_state_severity("Dgrd") == "warning"


def _load(
    session: Session,
    tmp_path: Path,
    *,
    roc_history_chart_url: str = "/controller/roc-history",
):
    return load_controller_detail_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_start_time=datetime(2026, 5, 20, 18, 0, tzinfo=UTC),
        app_version="0.1.0",
        roc_history_chart_url=roc_history_chart_url,
    )


def _insert(
    session: Session,
    snapshot: StorcliSnapshot,
    *,
    raw_json: dict[str, Any],
) -> None:
    insert_snapshot(session, snapshot, store_raw=True, raw_payload=raw_json)
    session.commit()


def _optimal_snapshot(sample_snapshot: StorcliSnapshot) -> StorcliSnapshot:
    controller = sample_snapshot.controller.model_copy(update={"roc_temperature_celsius": 70})
    return sample_snapshot.model_copy(
        update={
            "controller": controller,
            "virtual_drives": [
                drive.model_copy(update={"state": "Optl"})
                for drive in sample_snapshot.virtual_drives
            ],
            "physical_drives": [
                drive.model_copy(update={"state": "Onln", "temperature_celsius": 40})
                for drive in sample_snapshot.physical_drives
            ],
        }
    )


def _event(*, severity: str, occurred_at: datetime) -> Event:
    return Event(
        occurred_at=occurred_at,
        severity=severity,
        category="controller",
        subject="Controller",
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


class _Scheduler:
    def __init__(self, last_run_at: datetime | None) -> None:
        self.last_run_at = last_run_at

    def get_last_collector_run_at(self) -> datetime | None:
        return self.last_run_at


def _raw_controller_payload() -> dict[str, Any]:
    payload = _load_redacted_fixture("c0_show_all.json")
    assert isinstance(payload, dict)
    response_data = payload["Controllers"][0]["Response Data"]
    response_data["HwCfg"]["ChipRevision"] = "B0"
    response_data["HwCfg"]["Current Size of FW Cache (MB)"] = 875
    response_data["Scheduled Tasks"]["Patrol Read Reoccurrence"] = "168 hours"
    response_data["Scheduled Tasks"]["Next Patrol Read launch"] = "05/24/2026, 18:00:00"
    return payload


def _load_redacted_fixture(name: str) -> dict[str, Any]:
    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/storcli/redacted" / name).read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload
