"""RoC episode boundaries survive missed recovery events and raw-history pruning."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from megaraid_dashboard.db.dao import get_state, insert_snapshot, set_state
from megaraid_dashboard.db.models import ControllerSnapshot, Event
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.services.telegram_relevance import (
    incident_superseded,
    record_controller_temperature_clears,
)
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.test_services.test_telegram_notifier import (
    NOW,
    FakeSender,
    _add_event,
    _cycle,
    _delivery,
    _settings,
)
from tests.test_services.test_telegram_relevance import _save


@pytest.mark.parametrize("severity", ["warning", "critical"])
@pytest.mark.parametrize("first_delivered", [False, True])
@pytest.mark.parametrize("tracked_and_pruned", [False, True])
def test_sparse_controller_readings_separate_episodes_without_recovery_events(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    severity: str,
    first_delivered: bool,
    tracked_and_pruned: bool,
) -> None:
    settings = _settings()
    detector = EventDetector(
        temp_warning=100,
        temp_critical=110,
        temp_hysteresis=5,
        roc_temp_warning=95,
        roc_temp_critical=105,
        roc_temp_hysteresis=5,
    )
    shift = 10 if severity == "critical" else 0
    readings = [
        (-80, 94),
        (-70, 96),
        (-50, 96),
        (-45, 94),
        (-40, 89),
        (-35, 91),
        (-30, 96),
        (0, 96),
    ]
    previous = None
    events: list[Event] = []
    recoveries: list[object] = []
    sender = FakeSender()
    for minutes, reading in readings:
        at = NOW + timedelta(minutes=minutes)
        snapshot = sample_snapshot.model_copy(
            update={
                "captured_at": at,
                "controller": sample_snapshot.controller.model_copy(
                    update={"roc_temperature_celsius": reading + shift}
                ),
            }
        )
        detected = [] if previous is None else detector.detect(previous, snapshot)
        previous = insert_snapshot(session, snapshot)
        if tracked_and_pruned:
            record_controller_temperature_clears(
                session, temperature=reading + shift, captured_at=at, settings=settings
            )
        for item in detected:
            if item.category != "controller_temperature":
                continue
            if item.severity == "info":
                recoveries.append(item)
            if item.severity == severity:
                event = _add_event(
                    session,
                    category=item.category,
                    subject=item.subject,
                    severity=item.severity,
                    summary=item.summary,
                    occurred_at=at + timedelta(seconds=1),
                    after=item.after,
                )
                event.before_json = item.before
                events.append(event)
        session.commit()
        if minutes == -50 and first_delivered:
            assert _cycle(session, sender, now=at + timedelta(seconds=1)).sent == 1
    assert len(events) == 2
    assert recoveries == []  # Adjacent-sample detector misses the gradual hysteresis clear.
    boundary = 100 if severity == "critical" else 90
    assert events[-1].before_json["roc_temperature_celsius"] > boundary
    if tracked_and_pruned:
        session.execute(delete(ControllerSnapshot).where(ControllerSnapshot.id != previous.id))
        session.commit()
        assert (
            get_state(session, f"telegram_roc_{severity}_cleared_at")
            == (NOW - timedelta(minutes=40)).isoformat()
        )
    result = _cycle(session, sender)
    assert result.sent == 1
    assert len(sender.sent) == (2 if first_delivered else 1)
    assert f"событие #{events[-1].id}" in sender.sent[-1]
    if not first_delivered:
        assert _delivery(session, events[0]).detail == "obsolete"
    assert all(event.notified_at is None for event in events)


@pytest.mark.parametrize(("severity", "temperature"), [("warning", 90), ("critical", 100)])
def test_exact_controller_clear_boundary_resets_prior_incident(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    severity: str,
    temperature: int,
) -> None:
    event = _add_event(
        session,
        severity=severity,
        category="controller_temperature",
        subject="Controller",
        occurred_at=NOW - timedelta(minutes=40),
        after={"roc_temperature_celsius": temperature + 6},
    )
    _save(session, sample_snapshot, now=NOW - timedelta(minutes=10), roc=temperature)
    _save(session, sample_snapshot, roc=temperature + 6)
    assert incident_superseded(session, event, settings=_settings(), until=NOW)


def test_recovery_tracking_is_monotonic_and_missing_sensor_is_not_a_clear(session: Session) -> None:
    settings = _settings()
    for at, temperature in [
        (NOW, 89),
        (NOW - timedelta(minutes=1), 80),
        (NOW + timedelta(minutes=1), None),
        (NOW + timedelta(minutes=2), 94),
    ]:
        record_controller_temperature_clears(
            session, captured_at=at, temperature=temperature, settings=settings
        )
    assert get_state(session, "telegram_roc_warning_cleared_at") == NOW.isoformat()
    # 94 clears critical, while remaining inside the active warning hysteresis band.
    assert (
        get_state(session, "telegram_roc_critical_cleared_at")
        == (NOW + timedelta(minutes=2)).isoformat()
    )
    session.rollback()
    assert get_state(session, "telegram_roc_warning_cleared_at") is None


def test_missing_state_bootstraps_from_retained_history_and_repairs_invalid_bookkeeping(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    _save(session, sample_snapshot, now=NOW - timedelta(minutes=20), roc=89)
    _save(session, sample_snapshot, roc=96)
    set_state(session, "telegram_roc_warning_cleared_at", "invalid timestamp")
    record_controller_temperature_clears(
        session, captured_at=NOW, temperature=96, settings=_settings()
    )
    assert (
        get_state(session, "telegram_roc_warning_cleared_at")
        == (NOW - timedelta(minutes=20)).isoformat()
    )
    assert get_state(session, "telegram_roc_critical_cleared_at") == NOW.isoformat()


def test_missing_sensor_without_history_creates_no_recovery_proof(session: Session) -> None:
    record_controller_temperature_clears(
        session, captured_at=NOW, temperature=None, settings=_settings()
    )
    assert get_state(session, "telegram_roc_warning_cleared_at") is None
    assert get_state(session, "telegram_roc_critical_cleared_at") is None


def test_future_clear_and_snapshot_do_not_end_an_earlier_episode(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    event = _add_event(
        session, severity="warning", category="controller_temperature", subject="Controller"
    )
    later = NOW + timedelta(minutes=5)
    _save(session, sample_snapshot, now=later, roc=89)
    set_state(session, "telegram_roc_warning_cleared_at", later.isoformat())
    assert not incident_superseded(session, event, settings=_settings(), until=NOW)
    assert incident_superseded(session, event, settings=_settings(), until=later)


def test_controller_info_is_not_an_active_episode(session: Session) -> None:
    event = _add_event(
        session, severity="info", category="controller_temperature", subject="Controller"
    )
    assert not incident_superseded(session, event, settings=_settings(), until=NOW)


@pytest.mark.parametrize(
    ("severity", "hot", "in_band"), [("warning", 96, 94), ("critical", 106, 104)]
)
def test_level_specific_clear_markers_do_not_split_an_active_incident(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    severity: str,
    hot: int,
    in_band: int,
) -> None:
    settings = _settings()
    first = _add_event(
        session,
        category="controller_temperature",
        subject="Controller",
        severity=severity,
        occurred_at=NOW - timedelta(minutes=40),
        after={"roc_temperature_celsius": hot},
    )
    first_sent = NOW - timedelta(minutes=20)
    _save(session, sample_snapshot, now=first_sent, roc=hot)
    record_controller_temperature_clears(
        session, captured_at=first_sent, temperature=hot, settings=settings
    )
    session.commit()
    sender = FakeSender()
    assert _cycle(session, sender, now=first_sent).sent == 1
    middle = NOW - timedelta(minutes=10)
    _save(session, sample_snapshot, now=middle, roc=in_band)
    record_controller_temperature_clears(
        session, captured_at=middle, temperature=in_band, settings=settings
    )
    second = _add_event(
        session,
        category="controller_temperature",
        subject="Controller",
        severity=severity,
        occurred_at=NOW - timedelta(minutes=9),
        after={"roc_temperature_celsius": hot},
    )
    now = NOW + timedelta(minutes=6)
    _save(session, sample_snapshot, now=now, roc=hot)
    record_controller_temperature_clears(
        session, captured_at=now, temperature=hot, settings=settings
    )
    session.commit()
    assert _cycle(session, sender, now=now).sent == 0
    assert _delivery(session, first).status == "sent"
    assert _delivery(session, second).detail == "duplicate"
    assert len(sender.sent) == 1


async def test_collector_records_recovery_even_with_telegram_disabled(
    session: Session,
    engine: Engine,
    sample_snapshot: StorcliSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from megaraid_dashboard.db import get_sessionmaker
    from megaraid_dashboard.services import scheduler as scheduler_module
    from tests.test_services.test_scheduler_locking import _service

    service = _service(get_sessionmaker(engine))
    service.clock = lambda: NOW
    assert not service.settings.telegram_enabled
    _save(session, sample_snapshot, now=NOW - timedelta(minutes=5), roc=94)
    current = sample_snapshot.model_copy(
        update={
            "captured_at": NOW,
            "controller": sample_snapshot.controller.model_copy(
                update={"roc_temperature_celsius": 89}
            ),
        }
    )

    async def collect(**kwargs: object) -> tuple[StorcliSnapshot, dict[str, object]]:
        return current, {}

    monkeypatch.setattr(scheduler_module, "collect_storcli_snapshot", collect)
    assert await service.run_once()
    session.expire_all()
    assert get_state(session, "telegram_roc_warning_cleared_at") == NOW.isoformat()
    assert get_state(session, "telegram_roc_critical_cleared_at") == NOW.isoformat()
    assert (
        list(session.scalars(select(Event).where(Event.category == "controller_temperature"))) == []
    )


async def test_failed_collection_rolls_back_recovery_evidence(
    session: Session,
    engine: Engine,
    sample_snapshot: StorcliSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func, select

    from megaraid_dashboard.db import get_sessionmaker
    from megaraid_dashboard.services import scheduler as scheduler_module
    from tests.test_services.test_scheduler_locking import _service

    service = _service(get_sessionmaker(engine))
    service.clock = lambda: NOW
    _save(session, sample_snapshot, now=NOW - timedelta(minutes=5), roc=94)
    current = sample_snapshot.model_copy(
        update={
            "captured_at": NOW,
            "controller": sample_snapshot.controller.model_copy(
                update={"roc_temperature_celsius": 89}
            ),
        }
    )

    async def collect(**kwargs: object) -> tuple[StorcliSnapshot, dict[str, object]]:
        return current, {}

    def detect(*args: object) -> list[object]:
        raise RuntimeError("failure after snapshot and clear metadata were staged")

    monkeypatch.setattr(scheduler_module, "collect_storcli_snapshot", collect)
    monkeypatch.setattr(service.event_detector, "detect", detect)
    assert not await service.run_once()
    session.expire_all()
    assert get_state(session, "telegram_roc_warning_cleared_at") is None
    assert get_state(session, "telegram_roc_critical_cleared_at") is None
    assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 1
