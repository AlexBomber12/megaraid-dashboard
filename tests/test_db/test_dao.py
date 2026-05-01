from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from megaraid_dashboard.db import (
    AuditLog,
    CacheVaultSnapshot,
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    PhysicalDriveTempState,
    VirtualDriveSnapshot,
    clear_temp_state_for_slot,
    count_events_notified_since,
    get_latest_snapshot,
    get_temp_state,
    insert_snapshot,
    iter_pending_events,
    list_recent_snapshots,
    mark_event_notified,
    record_audit,
    record_event,
    upsert_temp_state,
)
from megaraid_dashboard.storcli import StorcliSnapshot


def test_insert_snapshot_creates_expected_child_rows(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    insert_snapshot(session, sample_snapshot, store_raw=True, raw_payload={"Controllers": []})
    session.commit()

    assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(VirtualDriveSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(PhysicalDriveSnapshot)) == 8
    assert session.scalar(select(func.count()).select_from(CacheVaultSnapshot)) == 1
    assert session.scalars(select(ControllerSnapshot)).one().raw_json == {"Controllers": []}
    assert session.scalars(select(CacheVaultSnapshot)).one().capacitance_percent == 89


def test_get_latest_snapshot_returns_most_recent(session: Session) -> None:
    older = _controller_snapshot(datetime(2026, 4, 24, 12, 0, tzinfo=UTC), serial="old")
    newer = _controller_snapshot(datetime(2026, 4, 25, 12, 0, tzinfo=UTC), serial="new")
    session.add_all([older, newer])
    session.commit()

    latest = get_latest_snapshot(session)

    assert latest is not None
    assert latest.serial_number == "new"


def test_list_recent_snapshots_orders_descending(session: Session) -> None:
    snapshots = [
        _controller_snapshot(datetime(2026, 4, 23, 12, 0, tzinfo=UTC), serial="oldest"),
        _controller_snapshot(datetime(2026, 4, 25, 12, 0, tzinfo=UTC), serial="newest"),
        _controller_snapshot(datetime(2026, 4, 24, 12, 0, tzinfo=UTC), serial="middle"),
    ]
    session.add_all(snapshots)
    session.commit()

    recent = list_recent_snapshots(session, limit=2)

    assert [snapshot.serial_number for snapshot in recent] == ["newest", "middle"]


def test_record_event_writes_severity_and_category(session: Session) -> None:
    record_event(
        session,
        severity="warning",
        category="temperature",
        subject="PD e252:s4",
        summary="Drive temperature is elevated",
    )
    session.commit()

    event = session.scalars(select(Event)).one()
    assert event.severity == "warning"
    assert event.category == "temperature"


def test_temperature_state_upsert_and_clear(session: Session) -> None:
    first = upsert_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN0001",
        state="warning",
    )
    second = upsert_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN0001",
        state="critical",
    )
    session.commit()

    stored = get_temp_state(
        session,
        enclosure_id=252,
        slot_id=4,
        serial_number="SN0001",
    )

    assert second.id == first.id
    assert stored is not None
    assert stored.state == "critical"
    assert session.scalar(select(func.count()).select_from(PhysicalDriveTempState)) == 1

    deleted = clear_temp_state_for_slot(session, enclosure_id=252, slot_id=4)
    session.commit()

    assert deleted == 1
    assert (
        get_temp_state(
            session,
            enclosure_id=252,
            slot_id=4,
            serial_number="SN0001",
        )
        is None
    )


def test_record_audit_writes_command_argv_list(session: Session) -> None:
    record_audit(
        session,
        actor="admin",
        action="start_locate",
        target="PD e252:s4",
        command_argv=["storcli64", "/c0/e252/s4", "start", "locate", "J"],
        exit_code=0,
        stdout_tail="ok",
        stderr_tail="",
        duration_seconds=0.25,
        success=True,
    )
    session.commit()

    audit_log = session.scalars(select(AuditLog)).one()
    assert audit_log.command_argv == ["storcli64", "/c0/e252/s4", "start", "locate", "J"]
    assert audit_log.success is True


def test_iter_pending_events_filters_by_severity_and_notified_state(session: Session) -> None:
    since = datetime(2026, 4, 25, 0, 0, tzinfo=UTC)
    matching = _event(
        occurred_at=since + timedelta(hours=1),
        severity="critical",
        subject="match",
    )
    wrong_severity = _event(
        occurred_at=since + timedelta(hours=2),
        severity="warning",
        subject="wrong-severity",
    )
    already_notified = _event(
        occurred_at=since + timedelta(hours=3),
        severity="critical",
        subject="already-notified",
        notified_at=since + timedelta(hours=4),
    )
    session.add_all([matching, wrong_severity, already_notified])
    session.commit()

    pending = list(iter_pending_events(session, severity_threshold="critical", since=since))

    assert [event.subject for event in pending] == ["match"]


def test_iter_pending_events_excludes_events_older_than_since(session: Session) -> None:
    since = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    older = _event(
        occurred_at=since - timedelta(seconds=1),
        severity="critical",
        subject="too-old",
    )
    same_instant = _event(
        occurred_at=since,
        severity="critical",
        subject="boundary",
    )
    newer = _event(
        occurred_at=since + timedelta(minutes=1),
        severity="critical",
        subject="fresh",
    )
    session.add_all([older, same_instant, newer])
    session.commit()

    pending = list(iter_pending_events(session, severity_threshold="critical", since=since))

    assert [event.subject for event in pending] == ["boundary", "fresh"]


def test_iter_pending_events_orders_by_occurred_at_then_id(session: Session) -> None:
    occurred_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    first = _event(occurred_at=occurred_at, severity="critical", subject="first")
    second = _event(occurred_at=occurred_at, severity="critical", subject="second")
    third = _event(occurred_at=occurred_at, severity="critical", subject="third")
    session.add_all([first, second, third])
    session.commit()

    pending = list(
        iter_pending_events(
            session,
            severity_threshold="critical",
            since=occurred_at - timedelta(hours=1),
        )
    )

    assert [event.id for event in pending] == sorted(event.id for event in pending)
    assert [event.subject for event in pending] == ["first", "second", "third"]


def test_iter_pending_events_threshold_includes_higher_severities(session: Session) -> None:
    since = datetime(2026, 4, 25, 0, 0, tzinfo=UTC)
    info_event = _event(
        occurred_at=since + timedelta(hours=1),
        severity="info",
        subject="info-below",
    )
    warning_event = _event(
        occurred_at=since + timedelta(hours=2),
        severity="warning",
        subject="warning-at",
    )
    critical_event = _event(
        occurred_at=since + timedelta(hours=3),
        severity="critical",
        subject="critical-above",
    )
    session.add_all([info_event, warning_event, critical_event])
    session.commit()

    pending = list(iter_pending_events(session, severity_threshold="warning", since=since))

    assert [event.subject for event in pending] == ["warning-at", "critical-above"]


def test_iter_pending_events_threshold_info_includes_all_severities(session: Session) -> None:
    since = datetime(2026, 4, 25, 0, 0, tzinfo=UTC)
    session.add_all(
        [
            _event(occurred_at=since + timedelta(hours=1), severity="info", subject="info"),
            _event(occurred_at=since + timedelta(hours=2), severity="warning", subject="warning"),
            _event(occurred_at=since + timedelta(hours=3), severity="critical", subject="critical"),
        ]
    )
    session.commit()

    pending = list(iter_pending_events(session, severity_threshold="info", since=since))

    assert [event.subject for event in pending] == ["info", "warning", "critical"]


def test_iter_pending_events_rejects_unknown_severity_threshold(session: Session) -> None:
    with pytest.raises(ValueError):
        list(
            iter_pending_events(
                session,
                severity_threshold="bogus",
                since=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
            )
        )


def test_iter_pending_events_rejects_naive_since(session: Session) -> None:
    with pytest.raises(ValueError):
        list(
            iter_pending_events(
                session,
                severity_threshold="critical",
                since=datetime(2026, 4, 25, 12, 0),
            )
        )


def test_mark_event_notified_sets_notified_at(session: Session) -> None:
    event = record_event(
        session,
        severity="critical",
        category="smart_alert",
        subject="PD e252:s4",
        summary="Drive predicts failure",
    )
    session.commit()
    sent_at = datetime(2026, 4, 25, 12, 30, tzinfo=UTC)

    mark_event_notified(session, event.id, sent_at)
    session.commit()

    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at == sent_at


def test_mark_event_notified_raises_for_unknown_event(session: Session) -> None:
    with pytest.raises(LookupError):
        mark_event_notified(session, 12345, datetime(2026, 4, 25, 12, 0, tzinfo=UTC))


def test_mark_event_notified_rejects_naive_sent_at(session: Session) -> None:
    event = record_event(
        session,
        severity="warning",
        category="temperature",
        subject="PD e252:s4",
        summary="Drive temperature is elevated",
    )
    session.commit()

    with pytest.raises(ValueError):
        mark_event_notified(session, event.id, datetime(2026, 4, 25, 12, 0))


def test_mark_event_notified_flushes_for_subsequent_queries(session: Session) -> None:
    since = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    event = record_event(
        session,
        severity="critical",
        category="smart_alert",
        subject="PD e252:s4",
        summary="Drive predicts failure",
    )
    session.commit()
    sent_at = since + timedelta(minutes=5)

    mark_event_notified(session, event.id, sent_at)

    pending = list(iter_pending_events(session, severity_threshold="critical", since=since))
    assert pending == []
    assert count_events_notified_since(session, since=since) == 1


def test_mark_event_notified_does_not_commit(session: Session) -> None:
    event = record_event(
        session,
        severity="critical",
        category="smart_alert",
        subject="PD e252:s4",
        summary="Drive predicts failure",
    )
    session.commit()
    sent_at = datetime(2026, 4, 25, 12, 30, tzinfo=UTC)

    mark_event_notified(session, event.id, sent_at)
    session.rollback()

    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at is None


def test_count_events_notified_since_empty_table(session: Session) -> None:
    since = datetime(2026, 4, 25, 0, 0, tzinfo=UTC)

    assert count_events_notified_since(session, since=since) == 0


def test_count_events_notified_since_populated_table(session: Session) -> None:
    since = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    inside = _event(
        occurred_at=since,
        severity="critical",
        subject="inside-window",
        notified_at=since + timedelta(minutes=5),
    )
    outside = _event(
        occurred_at=since - timedelta(hours=1),
        severity="critical",
        subject="outside-window",
        notified_at=since - timedelta(minutes=5),
    )
    pending = _event(
        occurred_at=since + timedelta(minutes=1),
        severity="critical",
        subject="not-notified",
    )
    session.add_all([inside, outside, pending])
    session.commit()

    assert count_events_notified_since(session, since=since) == 1


def test_count_events_notified_since_rejects_naive_since(session: Session) -> None:
    with pytest.raises(ValueError):
        count_events_notified_since(session, since=datetime(2026, 4, 25, 12, 0))


def _event(
    *,
    occurred_at: datetime,
    severity: str,
    subject: str,
    notified_at: datetime | None = None,
) -> Event:
    return Event(
        occurred_at=occurred_at,
        severity=severity,
        category="test",
        subject=subject,
        summary=f"summary for {subject}",
        notified_at=notified_at,
    )


def _controller_snapshot(captured_at: datetime, *, serial: str) -> ControllerSnapshot:
    return ControllerSnapshot(
        captured_at=captured_at,
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number=serial,
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state="Off",
        cv_present=True,
        bbu_present=True,
    )
