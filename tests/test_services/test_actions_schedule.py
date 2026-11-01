"""Observed scheduling must survive the default compact snapshot path."""

from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from megaraid_dashboard.services import controller_detail as detail
from megaraid_dashboard.services.drive_actions import parse_patrol_read_status


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("168 hours", 168),
        ("24 HRS", 24),
        ("72", 72),
        ("0", None),
        ("-1 hours", None),
        ("1.5 hours", None),
        ("30 minutes", None),
        ("bad 12", None),
        ("999999999999999999999999999999", None),
    ],
)
def test_patrol_interval_does_not_guess_units(value, expected):
    response = {
        "Controllers": [
            {
                "Response Data": {
                    "Controller Properties": [{"Ctrl_Prop": "PR Execution Delay", "Value": value}]
                }
            }
        ]
    }
    assert parse_patrol_read_status(response).interval_hours == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("1 day", 24),
        ("168 hours", 168),
        ("0 hrs", None),
        ("30 minutes", None),
        ("1.5 days", None),
        ("invalid", None),
    ],
)
def test_legacy_schedule_interval_validation(value, expected):
    assert detail._parse_interval_hours(value) == expected


def test_next_start_is_reported_without_invented_interval():
    when = datetime(2026, 10, 31, 9, tzinfo=UTC)
    assert detail._schedule_text(None, when) == (
        "Interval unknown · Reported schedule: 31 Oct 2026, 09:00 (controller time)."
    )


@pytest.mark.parametrize(
    "state,label",
    [("active", "Running"), ("paused", "Paused"), ("stopped", "Stopped"), ("unknown", "Unknown")],
)
def test_state_badge_does_not_repeat_progress(state, label):
    status = detail.PatrolReadStatus("auto", state, progress_percent=42)
    assert detail._operation_status_text(status) == label


@pytest.mark.parametrize("foreign", [None, {}, {"present": "invalid"}])
def test_missing_or_invalid_foreign_evidence_is_unknown(foreign):
    state = detail._build_foreign_config_state({"foreign_config_state": foreign})
    assert not state.is_known and not state.present
    assert state.description_text == "Foreign configuration status unknown."
    assert not state.can_import and not state.can_clear


def test_compact_patrol_schedule_and_foreign_state_survive_collection(
    monkeypatch, session, tmp_path
):
    # Run the real collector → compact DAO → real controller view model.
    # The boundary is mocked; no subprocess or hardware is reachable.
    import asyncio

    from megaraid_dashboard.db.dao import insert_snapshot
    from megaraid_dashboard.services.collector import collect_storcli_snapshot
    from tests.test_services.test_collector import _load_fixture, _settings
    from tests.test_services.test_controller_detail import _load

    fixtures = {
        "/c0 show all": "c0_show_all.json",
        "/c0/vall show all": "vall_show_all.json",
        "/c0/eall/sall show all": "eall_sall_show_all.json",
        "/c0/cv show all": "cv_show_all.json",
        "/c0/bbu show all": "bbu_show_all.json",
        "/c0/fall show all": "c0_fall_show_all_absent.json",
        "/c0 show patrolread": "c0_show_patrolread_schedule.json",
    }
    calls = []

    async def fake(args, **kwargs):
        calls.append(args)
        name = fixtures.get(" ".join(args))
        if name is not None:
            return _load_fixture(name)
        return {
            "Controllers": [
                {
                    "Command Status": {"Status": "Success"},
                    "Response Data": {"CC Mode": "Concurrent", "State": "Stopped"},
                }
            ]
        }

    monkeypatch.setattr("megaraid_dashboard.services.collector.run_storcli", fake)
    snapshot, raw = asyncio.run(collect_storcli_snapshot(settings=_settings()))
    row = insert_snapshot(session, snapshot, store_raw=False, raw_payload=raw)
    session.commit()
    assert "controller" not in row.raw_json
    assert row.raw_json["patrol_read"]["interval_hours"] == 168
    assert row.raw_json["foreign_config_state"]["present"] is False
    view = _load(session, tmp_path)
    assert view.scheduled_tasks[0].schedule_text == (
        "Every 7 days · Reported schedule: 31 Oct 2026, 09:00 (controller time)."
    )
    assert view.scheduled_tasks[0].is_enabled is True
    assert view.foreign_config.is_known and not view.foreign_config.present
    assert len(calls) == 9
    assert all("set" not in args and "start" not in args and "stop" not in args for args in calls)


@pytest.mark.parametrize("next_start,interval", [("invalid", 168), ("10/31/2026, 09:00:00", None)])
def test_schedule_uses_observed_probe_fields_without_stale_raw_fallback(
    session, sample_snapshot, tmp_path, next_start, interval
):
    from tests.test_services.test_controller_detail import _insert, _load, _optimal_snapshot

    status = detail.PatrolReadStatus(
        "auto", "stopped", interval_hours=interval, next_start_timestamp=next_start
    )
    _insert(session, _optimal_snapshot(sample_snapshot), raw_json={"patrol_read": asdict(status)})
    text = _load(session, tmp_path).scheduled_tasks[0].schedule_text
    assert ("Schedule time unknown" in text) is (next_start == "invalid")
    assert ("Interval unknown" in text) is (interval is None)


@pytest.mark.parametrize("state", ["active", "stopped", "unknown"])
def test_overview_loads_persisted_operation_evidence(session, sample_snapshot, tmp_path, state):
    from megaraid_dashboard.services.overview import load_main_page_view_model
    from tests.test_services.test_controller_detail import _insert, _optimal_snapshot, _settings

    _insert(
        session,
        _optimal_snapshot(sample_snapshot),
        raw_json={
            "patrol_read": {"mode": "auto", "state": state},
            "consistency_check": {"mode": "concurrent", "state": state},
        },
    )
    view = load_main_page_view_model(
        session,
        settings=_settings(tmp_path),
        scheduler=None,
        collector_enabled=True,
        app_version="test",
    )
    assert view.controller.operations_known is (state != "unknown")
    assert [op.name for op in view.controller.active_operations] == (
        ["Consistency check", "Patrol read"] if state == "active" else []
    )


def test_nonpositive_saved_interval_is_not_displayed(session, sample_snapshot, tmp_path):
    from tests.test_services.test_controller_detail import _insert, _load, _optimal_snapshot

    _insert(
        session,
        _optimal_snapshot(sample_snapshot),
        raw_json={"patrol_read": {"mode": "auto", "state": "stopped", "interval_hours": 0}},
    )
    assert _load(session, tmp_path).scheduled_tasks[0].schedule_text == "Schedule unknown."
