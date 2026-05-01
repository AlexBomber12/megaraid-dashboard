from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.overview import (
    OverviewViewModel,
    _load_alert_status,
    load_overview_view_model,
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


def test_overview_view_model_all_optimal(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, temperatures=(40,)))

    view_model = load_overview_view_model(session)

    assert view_model.has_snapshot is True
    assert view_model.captured_at == sample_snapshot.captured_at
    assert view_model.max_temperature_celsius == 40
    assert view_model.elevated_drive_count == 0
    assert view_model.critical_drive_count == 0
    assert _card(view_model, "Controller Health").value == "Optimal"
    assert _card(view_model, "Controller Health").severity == "optimal"
    assert _card(view_model, "Virtual Drive").value == "Optimal"
    assert _card(view_model, "Virtual Drive").severity == "optimal"
    assert _card(view_model, "RAID Type").value == "RAID6"
    assert _card(view_model, "Size").value.endswith(" TB")
    assert _card(view_model, "BBU/CV").value == "Opt"
    assert _card(view_model, "BBU/CV").severity == "optimal"


def test_overview_view_model_degraded_virtual_drive(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, vd_state="Degraded"))

    view_model = load_overview_view_model(session)

    assert _card(view_model, "Virtual Drive").value == "Degraded"
    assert _card(view_model, "Virtual Drive").severity == "warning"
    assert _card(view_model, "Controller Health").value == "Degraded"
    assert _card(view_model, "Controller Health").severity == "warning"


def test_overview_view_model_falls_back_to_first_available_virtual_drive(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _snapshot(sample_snapshot)
    nonzero_virtual_drive = snapshot.virtual_drives[0].model_copy(
        update={"vd_id": 2, "state": "Optl", "raid_level": "RAID10", "size_bytes": 2 * 10**12}
    )
    _insert(session, snapshot.model_copy(update={"virtual_drives": [nonzero_virtual_drive]}))

    view_model = load_overview_view_model(session)

    assert _card(view_model, "Virtual Drive").value == "Optimal"
    assert _card(view_model, "RAID Type").value == "RAID10"
    assert _card(view_model, "Size").value == "2.0 TB"


def test_overview_view_model_failed_physical_drive(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, pd_state="Failed"))

    view_model = load_overview_view_model(session)

    assert _card(view_model, "Controller Health").value == "Critical"
    assert _card(view_model, "Controller Health").severity == "critical"


def test_overview_view_model_warning_temperature(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, temperatures=(55,)))

    view_model = load_overview_view_model(session)
    temp_card = _card(view_model, "Max Disk Temp")

    assert view_model.max_temperature_celsius == 55
    assert view_model.elevated_drive_count == 1
    assert view_model.critical_drive_count == 0
    assert temp_card.value == "55 C"
    assert temp_card.severity == "warning"
    assert [(badge.label, badge.severity) for badge in temp_card.badges] == [
        ("1 drives elevated", "warning")
    ]


def test_overview_view_model_critical_temperature(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, temperatures=(60,)))

    view_model = load_overview_view_model(session)
    temp_card = _card(view_model, "Max Disk Temp")

    assert view_model.max_temperature_celsius == 60
    assert view_model.elevated_drive_count == 1
    assert view_model.critical_drive_count == 1
    assert temp_card.value == "60 C"
    assert temp_card.severity == "critical"
    assert [(badge.label, badge.severity) for badge in temp_card.badges] == [
        ("1 drives critical", "critical"),
        ("1 drives elevated", "warning"),
    ]


def test_overview_view_model_bbu_warning_from_low_capacitance(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, cv_capacitance_percent=65))

    view_model = load_overview_view_model(session)

    assert _card(view_model, "BBU/CV").value == "Warning"
    assert _card(view_model, "BBU/CV").severity == "warning"


def test_overview_view_model_bbu_accepts_abbreviated_optimal_state(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, cv_state="Optl"))

    view_model = load_overview_view_model(session)

    assert _card(view_model, "BBU/CV").value == "Opt"
    assert _card(view_model, "BBU/CV").severity == "optimal"


def test_overview_view_model_bbu_replace_from_replacement_required(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, cv_replacement_required=True))

    view_model = load_overview_view_model(session)

    assert _card(view_model, "BBU/CV").value == "Replace"
    assert _card(view_model, "BBU/CV").severity == "critical"


def test_overview_view_model_absent_cachevault(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _insert(session, _snapshot(sample_snapshot, cachevault_present=False))

    view_model = load_overview_view_model(session)

    assert _card(view_model, "BBU/CV").value == "Absent"
    assert _card(view_model, "BBU/CV").severity == "unknown"


def test_overview_view_model_handles_missing_vd_temperatures_and_cachevault_capacitance(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    snapshot = _snapshot(
        sample_snapshot,
        temperatures=(None, None, None, None, None, None, None, None),
        cv_capacitance_percent=None,
    ).model_copy(update={"virtual_drives": []})
    _insert(session, snapshot)

    view_model = load_overview_view_model(session)

    assert _card(view_model, "Virtual Drive").value == "Unknown"
    assert _card(view_model, "RAID Type").value == "Unknown"
    assert _card(view_model, "Size").value == "Unknown"
    assert _card(view_model, "BBU/CV").value == "Unknown"
    assert _card(view_model, "Max Disk Temp").value == "Unknown"
    assert view_model.physical_drives[0].temperature == "Unknown"
    assert view_model.physical_drives[0].temperature_severity == "unknown"


def test_overview_view_model_empty_database(session: Session) -> None:
    view_model = load_overview_view_model(session)

    assert view_model.has_snapshot is False
    assert view_model.alert_status.last_alert_sent_at is None
    assert view_model.alert_status.pending_count == 0
    assert view_model.alert_status.sent_last_hour == 0
    assert view_model.alert_status.health == "optimal"
    assert view_model.alert_status.health_label == "Notifier OK"
    assert view_model.empty_title == "Waiting for first metrics collection"
    assert view_model.empty_next_run == "No collection run is currently scheduled."


def test_overview_view_model_empty_database_reports_missing_scheduler_job(
    session: Session,
) -> None:
    view_model = load_overview_view_model(session, scheduler=_SchedulerWithoutJobs())

    assert view_model.empty_next_run == "No collection run is currently scheduled."


def test_overview_view_model_empty_database_reports_disabled_collector(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    get_settings.cache_clear()

    view_model = load_overview_view_model(session)

    assert (
        view_model.empty_next_run
        == "Metrics collection is disabled; no collection run is scheduled."
    )


def test_overview_view_model_empty_database_reports_next_scheduled_run(session: Session) -> None:
    next_run_time = datetime.now(UTC) + timedelta(seconds=90)

    view_model = load_overview_view_model(session, scheduler=_SchedulerWithJob(next_run_time))

    assert view_model.empty_next_run.startswith("Next scheduled run in ")
    assert view_model.empty_next_run.endswith(" seconds.")


def test_load_alert_status_empty_database(session: Session) -> None:
    status = _load_alert_status(
        session,
        settings=get_settings(),
        now=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
    )

    assert status.last_alert_sent_at is None
    assert status.pending_count == 0
    assert status.sent_last_hour == 0
    assert status.health == "optimal"
    assert status.health_status == "optimal"
    assert status.health_label == "Notifier OK"


def test_load_alert_status_pending_never_sent_is_critical(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    session.add(_event(occurred_at=now - timedelta(minutes=5), notified_at=None))
    session.flush()

    status = _load_alert_status(session, settings=get_settings(), now=now)

    assert status.last_alert_sent_at is None
    assert status.pending_count == 1
    assert status.sent_last_hour == 0
    assert status.health == "critical"
    assert status.health_label == "Notifier appears stuck"


def test_load_alert_status_recently_notified_without_pending_is_optimal(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    notified_at = now - timedelta(seconds=30)
    session.add(_event(occurred_at=now - timedelta(minutes=5), notified_at=notified_at))
    session.flush()

    status = _load_alert_status(session, settings=get_settings(), now=now)

    assert status.last_alert_sent_at == notified_at
    assert status.pending_count == 0
    assert status.sent_last_hour == 1
    assert status.health == "optimal"
    assert status.health_label == "Notifier OK"


def test_load_alert_status_pending_with_recent_send_is_warning(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            _event(occurred_at=now - timedelta(minutes=5), notified_at=None),
            _event(
                occurred_at=now - timedelta(minutes=10),
                notified_at=now - timedelta(minutes=3),
            ),
        ]
    )
    session.flush()

    status = _load_alert_status(session, settings=get_settings(), now=now)

    assert status.pending_count == 1
    assert status.sent_last_hour == 1
    assert status.health == "warning"
    assert status.health_label == "Notifier catching up"


def test_load_alert_status_pending_with_very_recent_send_is_optimal(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            _event(occurred_at=now - timedelta(minutes=5), notified_at=None),
            _event(
                occurred_at=now - timedelta(minutes=10),
                notified_at=now - timedelta(minutes=1),
            ),
        ]
    )
    session.flush()

    status = _load_alert_status(session, settings=get_settings(), now=now)

    assert status.pending_count == 1
    assert status.health == "optimal"
    assert status.health_label == "Notifier OK"


def test_load_alert_status_pending_with_stale_send_is_critical(session: Session) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            _event(occurred_at=now - timedelta(minutes=5), notified_at=None),
            _event(
                occurred_at=now - timedelta(minutes=40),
                notified_at=now - timedelta(minutes=30),
            ),
        ]
    )
    session.flush()

    status = _load_alert_status(session, settings=get_settings(), now=now)

    assert status.pending_count == 1
    assert status.sent_last_hour == 1
    assert status.health == "critical"
    assert status.health_label == "Notifier appears stuck"


def test_load_alert_status_rejects_naive_now(session: Session) -> None:
    with pytest.raises(ValueError, match="datetime must include a timezone"):
        _load_alert_status(
            session,
            settings=get_settings(),
            now=datetime(2026, 4, 25, 12, 0),
        )


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
) -> StorcliSnapshot:
    controller = sample_snapshot.controller.model_copy(update={"alarm_state": "Off"})
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


def _card(view_model: OverviewViewModel, label: str):
    for card in view_model.cards:
        if card.label == label:
            return card
    raise AssertionError(f"missing card: {label}")


def _event(
    *,
    occurred_at: datetime,
    notified_at: datetime | None,
    severity: str = "critical",
) -> Event:
    return Event(
        occurred_at=occurred_at,
        severity=severity,
        category="physical_drive",
        subject="e252:s4",
        summary="Drive state changed",
        notified_at=notified_at,
    )


@dataclass(frozen=True)
class _SchedulerWithoutJobs:
    def get_job(self, job_id: str) -> None:
        del job_id
        return None


@dataclass(frozen=True)
class _SchedulerJob:
    next_run_time: datetime


@dataclass(frozen=True)
class _SchedulerWithJob:
    next_run_time: datetime

    def get_job(self, job_id: str) -> _SchedulerJob | None:
        if job_id != "metrics_collector":
            return None
        return _SchedulerJob(next_run_time=self.next_run_time)
