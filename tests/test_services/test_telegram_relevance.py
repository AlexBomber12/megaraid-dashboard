"""Regression tests with real saved snapshots and no relevance-policy mocks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts.telegram import TelegramSendError
from megaraid_dashboard.db.dao import get_state, insert_snapshot, record_event, upsert_temp_state
from megaraid_dashboard.db.models import ControllerSnapshot, Event, EventDelivery
from megaraid_dashboard.services import telegram_notifier
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.services.notifier import get_notifier_health, run_notifier_cycle
from megaraid_dashboard.services.telegram_notifier import is_telegram_alert, run_telegram_cycle
from megaraid_dashboard.services.telegram_relevance import delivery_relevance, drive_serial_at
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.test_services.test_telegram_notifier import (
    NOW,
    FakeSender,
    FakeSmtp,
    _add_event,
    _cycle,
    _delivery,
    _settings,
)


@pytest.fixture(autouse=True)
def _freeze_cycle_elapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_notifier, "monotonic", lambda: 0)


def _save(
    session: Session,
    sample: StorcliSnapshot,
    *,
    now: datetime = NOW,
    drive: dict[str, Any] | None = None,
    removed: bool = False,
    vd_state: str = "Dgrd",
    roc: int | None = 96,
    cv: dict[str, Any] | None = None,
    cv_missing: bool = False,
) -> ControllerSnapshot:
    drives = list(sample.physical_drives)
    original = next(d for d in drives if (d.enclosure_id, d.slot_id) == (252, 0))
    drives = [d for d in drives if d is not original]
    if not removed:
        drives.append(original.model_copy(update={"serial_number": "ORIGINAL", **(drive or {})}))
    cachevault = sample.cachevault
    assert cachevault is not None
    snapshot = sample.model_copy(
        update={
            "captured_at": now,
            "controller": sample.controller.model_copy(update={"roc_temperature_celsius": roc}),
            "physical_drives": drives,
            "virtual_drives": [
                d.model_copy(update={"state": vd_state}) for d in sample.virtual_drives
            ],
            "cachevault": None if cv_missing else cachevault.model_copy(update=cv or {}),
        }
    )
    stored = insert_snapshot(session, snapshot)
    session.commit()
    return stored


def _temperature_event(session: Session, **overrides: Any) -> Event:
    return _add_event(
        session,
        **{
            "category": "temperature",
            "severity": "warning",
            "subject": "PD e252:s0",
            "after": {"state": "warning", "temperature_celsius": 57, "serial_number": "ORIGINAL"},
            "occurred_at": NOW - timedelta(minutes=20),
            **overrides,
        },
    )


@pytest.mark.parametrize(
    ("drive", "removed", "temp_state", "expected"),
    [
        ({"temperature_celsius": 57}, False, "warning", "sent"),
        ({"temperature_celsius": 53}, False, "warning", "sent"),  # hysteresis remains active
        ({"temperature_celsius": 50}, False, "warning", "sent"),
        ({"temperature_celsius": 49}, False, "warning", "skipped"),
        ({"temperature_celsius": 40}, False, "ok", "skipped"),
        ({"temperature_celsius": 40}, False, None, "skipped"),
        ({"temperature_celsius": 57}, False, "ok", "skipped"),
        ({"temperature_celsius": 65}, False, "critical", "skipped"),
        ({"temperature_celsius": 65}, False, None, "skipped"),
        ({"temperature_celsius": None}, False, "warning", "retry"),
        ({"serial_number": "REPLACEMENT", "temperature_celsius": 40}, False, None, "skipped"),
        ({"serial_number": "REPLACEMENT", "temperature_celsius": 57}, False, None, "skipped"),
        ({"serial_number": "", "temperature_celsius": 57}, False, None, "retry"),
        ({}, True, None, "skipped"),
    ],
)
def test_delayed_warning_checks_current_disk_and_temperature(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    drive: dict[str, Any],
    removed: bool,
    temp_state: str | None,
    expected: str,
) -> None:
    warning = _temperature_event(session)
    _save(session, sample_snapshot, drive=drive, removed=removed)
    if temp_state is not None:
        upsert_temp_state(
            session, enclosure_id=252, slot_id=0, serial_number="ORIGINAL", state=temp_state
        )
        session.commit()
    sender = FakeSender()

    result = _cycle(session, sender)

    delivery = _delivery(session, warning)
    assert delivery is not None and delivery.status == expected
    assert result.sent == (1 if expected == "sent" else 0)
    assert len(sender.sent) == result.sent
    if expected == "retry":
        assert delivery.attempts == 0
        assert delivery.detail in {"awaiting_temperature", "awaiting_drive_identity"}


@pytest.mark.parametrize("snapshot_age", [None, timedelta(minutes=11)])
def test_warning_waits_for_fresh_collection_then_revalidates(
    session: Session, sample_snapshot: StorcliSnapshot, snapshot_age: timedelta | None
) -> None:
    warning = _temperature_event(session)
    if snapshot_age is not None:
        _save(session, sample_snapshot, now=NOW - snapshot_age, drive={"temperature_celsius": 57})
    sender = FakeSender()
    assert _cycle(session, sender).deferred == 1
    delivery = _delivery(session, warning)
    assert delivery is not None and delivery.detail == "awaiting_fresh_data"
    # A fresh cool reading is sufficient even if no clear event was recorded.
    later = NOW + timedelta(minutes=1)
    _save(session, sample_snapshot, now=later, drive={"temperature_celsius": 40})
    assert _cycle(session, sender, now=later).skipped == 1
    assert sender.sent == []


@pytest.mark.parametrize(("roc", "expected"), [(96, 1), (91, 1), (90, 0), (None, 0), (106, 0)])
def test_controller_warning_checks_fresh_reading(
    session: Session, sample_snapshot: StorcliSnapshot, roc: int | None, expected: int
) -> None:
    _temperature_event(
        session,
        category="controller_temperature",
        subject="Controller",
        after={"roc_temperature_celsius": 96},
    )
    _save(session, sample_snapshot, roc=roc)
    assert _cycle(session, FakeSender()).sent == expected


@pytest.mark.parametrize("recovered", [False, True])
def test_critical_survives_long_delivery_outage_but_recovered_incident_is_not_sent(
    session: Session, sample_snapshot: StorcliSnapshot, recovered: bool
) -> None:
    event = _add_event(session, occurred_at=NOW - timedelta(days=2))
    _save(session, sample_snapshot, vd_state="Optl" if recovered else "Dgrd")
    sender = FakeSender()
    assert _cycle(session, sender).sent == (0 if recovered else 1)
    delivery = _delivery(session, event)
    assert delivery is not None and delivery.status == ("skipped" if recovered else "sent")
    assert event.notified_at is None


def test_long_retry_does_not_expire_or_duplicate_smtp(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    event = _add_event(session)
    sender = FakeSender(failures=[TelegramSendError("HTTP 502") for _ in range(10)])
    smtp = FakeSmtp()
    now = NOW
    for attempt in range(11):
        _save(session, sample_snapshot, now=now)
        result = _cycle(session, sender, now=now)
        run_notifier_cycle(session, smtp, settings=_settings(), now=now)
        delivery = _delivery(session, event)
        assert delivery is not None and delivery.attempts == attempt + 1
        if attempt < 10:
            assert not get_notifier_health()
            assert delivery.next_attempt_at is not None
            assert timedelta(minutes=1) <= delivery.next_attempt_at - now <= timedelta(hours=1)
            now = delivery.next_attempt_at
        else:
            assert result.sent == 1
    assert now - NOW > timedelta(hours=3)
    assert len(sender.sent) == len(smtp.sent) == 1


def test_channel_cooldown_survives_restart_and_removal_of_rate_limited_event(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    first = _add_event(session)
    _save(session, sample_snapshot)
    sender = FakeSender(failures=[TelegramSendError("rate limited", retry_after_seconds=14400)])
    assert _cycle(session, sender).failed == 1
    assert get_state(session, "telegram_not_before") == (NOW + timedelta(hours=4)).isoformat()
    session.delete(first)
    session.commit()
    second = _add_event(session, subject="VD 1")
    # Use a different ORM session as a restarted notifier would.
    with Session(session.get_bind()) as restarted:
        _save(restarted, sample_snapshot, now=NOW + timedelta(minutes=1))
        # VD 1 is absent, so it is obsolete, even during a channel cooldown.
        assert _cycle(restarted, sender, now=NOW + timedelta(minutes=1)).skipped == 1
        third = _add_event(restarted)
        blocked = _cycle(restarted, sender, now=NOW + timedelta(minutes=2))
        assert blocked.sent == 0 and blocked.deferred == 1
        delivery = _delivery(restarted, third)
        assert delivery is not None and delivery.attempts == 0
        assert not get_notifier_health()
        almost = NOW + timedelta(hours=4, seconds=-1)
        _save(restarted, sample_snapshot, now=almost)
        assert _cycle(restarted, sender, now=almost).sent == 0
        due = NOW + timedelta(hours=4, minutes=1)
        _save(restarted, sample_snapshot, now=due)
        assert _cycle(restarted, sender, now=due).sent == 1
    assert len(sender.sent) == 1
    assert second.notified_at is None


@pytest.mark.parametrize("accepted", [False, True])
def test_interrupted_send_retries_active_critical_even_after_three_hours(
    session: Session, sample_snapshot: StorcliSnapshot, accepted: bool
) -> None:
    event = _add_event(session)
    _save(session, sample_snapshot)
    delivered: list[str] = []

    class InterruptedSender:
        def send(self, text: str) -> None:
            if accepted:
                delivered.append(text)
            raise SystemExit("simulated termination before outcome commit")

    with pytest.raises(SystemExit):
        run_telegram_cycle(
            session, settings=_settings(), now=NOW, transport_factory=lambda _: InterruptedSender()
        )
    session.rollback()
    with Session(session.get_bind()) as restarted:
        later = NOW + timedelta(hours=4)
        _save(restarted, sample_snapshot, now=later)
        sender = FakeSender()
        assert _cycle(restarted, sender, now=later).sent == 1
        delivery = restarted.get(EventDelivery, _delivery(session, event).id)
        assert delivery is not None and (delivery.status, delivery.attempts) == ("sent", 2)
        delivered.extend(sender.sent)
    assert len(delivered) == (2 if accepted else 1)  # explicitly documents the ambiguity
    assert all(f"событие #{event.id}" in text for text in delivered)


def test_interrupted_send_of_recovered_incident_is_not_replayed(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    event = _add_event(session)
    session.add(EventDelivery(event_id=event.id, channel="telegram", status="sending", attempts=1))
    session.commit()
    _save(session, sample_snapshot, vd_state="Optl")
    sender = FakeSender()
    assert _cycle(session, sender).skipped == 1
    assert sender.sent == []
    assert _delivery(session, event).detail == "obsolete"


@pytest.mark.parametrize("status", ["failed", "retry"])
def test_existing_failed_or_delayed_delivery_revalidates_replacement_disk(
    session: Session, sample_snapshot: StorcliSnapshot, status: str
) -> None:
    event = _temperature_event(session, severity="critical")
    session.add(EventDelivery(event_id=event.id, channel="telegram", status=status, attempts=8))
    session.commit()
    _save(session, sample_snapshot, drive={"serial_number": "NEW", "temperature_celsius": 65})
    assert _cycle(session, FakeSender()).skipped == 1


@pytest.mark.parametrize(
    ("subject", "after", "expected"),
    [
        ("Controller", {"monitoring_outage": True}, True),
        ("Controller", {"monitoring_outage": False}, False),
        ("Controller", {"monitoring_outage": "true"}, False),
        ("Controller", None, False),
        ("disk space", {"monitoring_outage": True}, False),
    ],
)
def test_only_explicit_monitoring_outages_send_system_message(
    session: Session, subject: str, after: dict[str, Any] | None, expected: bool
) -> None:
    event = _add_event(
        session,
        category="system",
        subject=subject,
        after=after,
        summary="Monitoring stale: arbitrary system event",
    )
    assert is_telegram_alert(event) is expected
    sender = FakeSender()
    assert _cycle(session, sender).sent == (1 if expected else 0)
    if expected:
        assert "НЕИЗВЕСТНО" in sender.sent[0]


def test_recovered_monitoring_outage_does_not_page_later(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    _add_event(session, category="system", subject="Controller", after={"monitoring_outage": True})
    _save(session, sample_snapshot)
    assert _cycle(session, FakeSender()).skipped == 1


@pytest.mark.parametrize(
    ("category", "after", "options", "expected"),
    [
        ("vd_state", {"state": "Dgrd"}, {}, "active"),
        ("vd_state", None, {}, "awaiting_incident_state"),
        (
            "pd_state",
            {"state": "Failed", "serial_number": "ORIGINAL"},
            {"drive": {"state": "Failed"}},
            "active",
        ),
        ("pd_state", {"state": "Failed", "serial_number": "ORIGINAL"}, {}, "obsolete"),
        (
            "media_errors",
            {"media_errors": 5, "serial_number": "ORIGINAL"},
            {"drive": {"media_errors": 8}},
            "active",
        ),
        ("media_errors", {"media_errors": 5, "serial_number": "ORIGINAL"}, {}, "obsolete"),
        ("media_errors", {"serial_number": "ORIGINAL"}, {}, "awaiting_incident_state"),
        (
            "smart_alert",
            {"smart_alert": True, "serial_number": "ORIGINAL"},
            {"drive": {"smart_alert": True}},
            "active",
        ),
        ("smart_alert", {"smart_alert": True, "serial_number": "ORIGINAL"}, {}, "obsolete"),
        (
            "temperature",
            {"serial_number": "ORIGINAL"},
            {"drive": {"temperature_celsius": 65}},
            "active",
        ),
        (
            "temperature",
            {"serial_number": "ORIGINAL"},
            {"drive": {"temperature_celsius": 40}},
            "obsolete",
        ),
        ("cv_state", {"present": False}, {"cv_missing": True}, "active"),
        ("cv_state", {"present": False}, {}, "obsolete"),
        ("cv_state", {"state": "Degraded"}, {"cv_missing": True}, "obsolete"),
        ("cv_state", {"state": "Degraded"}, {"cv": {"state": "Degraded"}}, "active"),
        (
            "cv_state",
            {"replacement_required": True},
            {"cv": {"replacement_required": True}},
            "active",
        ),
        ("cv_state", {"replacement_required": True}, {}, "obsolete"),
    ],
)
def test_current_incident_state(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    category: str,
    after: dict[str, Any] | None,
    options: dict[str, Any],
    expected: str,
) -> None:
    subject = "VD 0" if category == "vd_state" else "PD e252:s0"
    event = _add_event(session, category=category, subject=subject, after=after)
    # _add_event supplies a VD state by default; explicitly clear it for the unknown case.
    if after is None:
        event.after_json = None
    _save(session, sample_snapshot, **options)
    assert delivery_relevance(session, event, settings=_settings(), now=NOW) == expected


def test_unknown_identity_is_retained_and_future_snapshot_is_not_used(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    event = _temperature_event(session, after={"state": "warning"}, subject="bad subject")
    assert drive_serial_at(session, event) is None
    _save(session, sample_snapshot, now=NOW + timedelta(minutes=1))
    assert (
        delivery_relevance(session, event, settings=_settings(), now=NOW) == "awaiting_fresh_data"
    )
    _save(session, sample_snapshot)
    assert (
        delivery_relevance(session, event, settings=_settings(), now=NOW)
        == "awaiting_drive_identity"
    )
    event.subject = "PD e252:s0"
    assert drive_serial_at(session, event) is None
    assert (
        delivery_relevance(session, event, settings=_settings(), now=NOW)
        == "awaiting_drive_identity"
    )


def test_serial_lookup_does_not_resurrect_removed_disk_from_older_snapshot(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    _save(session, sample_snapshot, now=NOW - timedelta(minutes=30))
    _save(session, sample_snapshot, now=NOW - timedelta(minutes=25), removed=True)
    event = _temperature_event(session, after={"state": "warning"})
    assert drive_serial_at(session, event) is None


def test_detector_serial_survives_snapshot_pruning(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    hot = sample_snapshot.model_copy(
        update={
            "physical_drives": [
                d.model_copy(update={"temperature_celsius": 57, "serial_number": "ORIGINAL"})
                for d in sample_snapshot.physical_drives
                if (d.enclosure_id, d.slot_id) == (252, 0)
            ]
        }
    )
    detector = EventDetector(
        temp_warning=55,
        temp_critical=60,
        temp_hysteresis=5,
        roc_temp_warning=95,
        roc_temp_critical=105,
        roc_temp_hysteresis=5,
    )
    detected = next(e for e in detector.detect(None, hot) if e.category == "temperature")
    event = record_event(
        session,
        severity=detected.severity,
        category=detected.category,
        subject=detected.subject,
        summary=detected.summary,
        after=detected.after,
    )
    session.commit()
    session.execute(delete(ControllerSnapshot))
    session.commit()
    assert drive_serial_at(session, event) == "ORIGINAL"


def test_candidate_processing_is_bounded_and_does_not_starve_backlog(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    for _ in range(105):
        _add_event(session)
    sender = FakeSender()
    assert _cycle(session, sender).deferred == 100
    # Old, deferred rows must not hide the five previously unexamined rows.
    _save(session, sample_snapshot, drive={"temperature_celsius": 57})
    result = _cycle(session, sender, now=NOW + timedelta(seconds=1))
    assert result.sent == 1 and result.skipped == 4
    assert len(sender.sent) == 1


def test_429_blocks_other_pending_incidents_without_consuming_attempts(
    session: Session, sample_snapshot: StorcliSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _add_event(session)
    second = _add_event(
        session,
        category="pd_state",
        subject="PD e252:s0",
        after={"state": "Failed", "serial_number": "ORIGINAL"},
    )
    _save(session, sample_snapshot, drive={"state": "Failed"})
    sender = FakeSender(failures=[TelegramSendError("rate limited", retry_after_seconds=600)])
    times = iter([0, 7])  # response arrives seven seconds after the cycle's clock sample
    monkeypatch.setattr(telegram_notifier, "monotonic", lambda: next(times))
    assert _cycle(session, sender).failed == 1
    assert get_state(session, "telegram_not_before") == (NOW + timedelta(seconds=607)).isoformat()
    monkeypatch.setattr(telegram_notifier, "monotonic", lambda: 0)
    assert _cycle(session, sender, now=NOW + timedelta(minutes=1)).sent == 0
    assert _delivery(session, first).attempts == 1
    assert _delivery(session, second).attempts == 0
    due = NOW + timedelta(seconds=607)
    _save(session, sample_snapshot, now=due, drive={"state": "Failed"})
    assert _cycle(session, sender, now=due).sent == 2


def test_stale_critical_waits_without_sending_or_using_retry_budget(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    event = _add_event(session, occurred_at=NOW - timedelta(days=1))
    sender = FakeSender()
    assert _cycle(session, sender).deferred == 1
    assert _delivery(session, event).attempts == 0
    later = NOW + timedelta(days=1)
    _save(session, sample_snapshot, now=later)
    assert _cycle(session, sender, now=later).sent == 1


def test_new_incident_after_recovery_does_not_replay_old_incident_or_get_deduplicated(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    old = _add_event(session, occurred_at=NOW - timedelta(hours=4))
    _add_event(
        session, severity="info", after={"state": "Optl"}, occurred_at=NOW - timedelta(hours=2)
    )
    new = _add_event(session)
    _save(session, sample_snapshot)
    sender = FakeSender()
    result = _cycle(session, sender)
    assert (result.sent, result.skipped) == (1, 1)
    assert _delivery(session, old).detail == "obsolete"
    assert f"событие #{new.id}" in sender.sent[0]

    _add_event(
        session, severity="info", after={"state": "Optl"}, occurred_at=NOW + timedelta(minutes=1)
    )
    recurrence = _add_event(session, occurred_at=NOW + timedelta(minutes=2))
    _save(session, sample_snapshot, now=NOW + timedelta(minutes=2))
    assert _cycle(session, sender, now=NOW + timedelta(minutes=2)).sent == 1
    assert f"событие #{recurrence.id}" in sender.sent[-1]


def test_nonthermal_warning_in_hardware_category_stays_ineligible(session: Session) -> None:
    event = _add_event(session, severity="warning", category="vd_state")
    sender = FakeSender()
    assert _cycle(session, sender).sent == 0
    assert sender.sent == []
    assert _delivery(session, event) is None


@pytest.mark.parametrize("warning_category", ["cv_state", "pd_state", "vd_state", "media_errors"])
@pytest.mark.parametrize("retry", [False, True])
def test_ineligible_warning_backlog_cannot_hide_critical_delivery(
    session: Session, sample_snapshot: StorcliSnapshot, warning_category: str, retry: bool
) -> None:
    for _ in range(100):
        _add_event(
            session,
            severity="warning",
            category=warning_category,
            subject="unrelated warning",
            occurred_at=NOW - timedelta(minutes=2),
        )
    critical = _add_event(session)
    if retry:
        session.add(
            EventDelivery(
                event_id=critical.id,
                channel="telegram",
                status="retry",
                attempts=1,
                next_attempt_at=NOW - timedelta(seconds=1),
            )
        )
        session.commit()
    _save(session, sample_snapshot)
    sender = FakeSender()

    result = _cycle(session, sender)

    assert result.sent == 1
    assert len(sender.sent) == 1
    assert _delivery(session, critical).attempts == (2 if retry else 1)
    assert _cycle(session, sender, now=NOW + timedelta(minutes=1)).sent == 0


def test_candidate_query_matches_complete_alert_policy(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(telegram_notifier, "_MAX_CANDIDATES_PER_CYCLE", 1000)
    categories = [
        "vd_state",
        "pd_state",
        "media_errors",
        "predictive_failures",
        "smart_alert",
        "cv_state",
        "temperature",
        "controller_temperature",
        "system",
        "disk_space",
        "other_errors",
    ]
    events = [
        _add_event(session, severity=severity, category=category)
        for severity in ("info", "warning", "critical", "unknown")
        for category in categories
    ]
    for subject in ("Controller", "Other"):
        for marker in (True, False, 1, 0, "true", "false", None):
            events.append(
                _add_event(
                    session,
                    category="system",
                    subject=subject,
                    after={"monitoring_outage": marker},
                )
            )
    expected = {event.id for event in events if is_telegram_alert(event)}
    actual = {event.id for event, _ in telegram_notifier._load_candidates(session, now=NOW)}
    assert actual == expected


@pytest.mark.parametrize(
    ("category", "temperature", "expected"),
    [
        ("temperature", 54, 0),
        ("temperature", 55, 1),
        ("temperature", 56, 1),
        ("controller_temperature", 99, 0),
        ("controller_temperature", 100, 0),
        ("controller_temperature", 101, 1),
    ],
)
def test_critical_temperature_hysteresis_matches_each_detector(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    category: str,
    temperature: int,
    expected: int,
) -> None:
    _temperature_event(
        session,
        category=category,
        severity="critical",
        subject="PD e252:s0" if category == "temperature" else "Controller",
        after={"state": "critical", "serial_number": "ORIGINAL", "temperature_celsius": 65}
        if category == "temperature"
        else {"roc_temperature_celsius": 110},
    )
    _save(session, sample_snapshot, drive={"temperature_celsius": temperature}, roc=temperature)
    sender = FakeSender()
    assert _cycle(session, sender).sent == expected


def test_controller_warning_oscillations_preserve_original_confirmation_deadline(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    first = _temperature_event(
        session,
        category="controller_temperature",
        subject="Controller",
        after={"roc_temperature_celsius": 96},
    )
    for minutes in (12, 4):
        _temperature_event(
            session,
            category="controller_temperature",
            subject="Controller",
            after={"roc_temperature_celsius": 96},
            occurred_at=NOW - timedelta(minutes=minutes),
        )
    _save(session, sample_snapshot, roc=94)
    sender = FakeSender()
    result = _cycle(session, sender)
    assert result.sent == 1 and f"событие #{first.id}" in sender.sent[0]
    assert _delivery(session, first).status == "sent"

    later = NOW + timedelta(minutes=15)
    _save(session, sample_snapshot, now=later, roc=96)
    assert _cycle(session, sender, now=later).sent == 0
    assert len(sender.sent) == 1


@pytest.mark.parametrize("ending_severity", ["info", "critical"])
def test_repeated_controller_warning_still_cancels_on_clear_or_escalation(
    session: Session, sample_snapshot: StorcliSnapshot, ending_severity: str
) -> None:
    warning = _temperature_event(
        session,
        category="controller_temperature",
        subject="Controller",
        after={"roc_temperature_celsius": 96},
    )
    _temperature_event(
        session,
        category="controller_temperature",
        subject="Controller",
        after={"roc_temperature_celsius": 96},
        occurred_at=NOW - timedelta(minutes=10),
    )
    _temperature_event(
        session,
        category="controller_temperature",
        subject="Controller",
        severity=ending_severity,
        after={"roc_temperature_celsius": 90 if ending_severity == "info" else 106},
        occurred_at=NOW - timedelta(minutes=1),
    )
    _save(session, sample_snapshot, roc=90 if ending_severity == "info" else 106)
    sender = FakeSender()
    _cycle(session, sender)
    assert _delivery(session, warning).detail == "not_confirmed"
    assert all("🟠" not in text for text in sender.sent)
