from __future__ import annotations

import smtplib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts import AlertMessage
from megaraid_dashboard.alerts.telegram import (
    TelegramConfig,
    TelegramConfigError,
    TelegramSendError,
    TelegramTransport,
)
from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.dao import (
    insert_snapshot,
    record_event,
    set_maintenance_state,
    set_state,
)
from megaraid_dashboard.db.models import Event, EventDelivery
from megaraid_dashboard.services import notifier, telegram_notifier
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.services.notifier import get_notifier_health, run_notifier_cycle
from megaraid_dashboard.services.telegram_notifier import (
    TelegramCycleResult,
    build_telegram_transport,
    is_telegram_alert,
    run_telegram_cycle,
)
from megaraid_dashboard.storcli import StorcliSnapshot, VirtualDrive
from megaraid_dashboard.web.metrics import TELEGRAM_ALERTS_TOTAL

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


@dataclass
class FakeSender:
    sent: list[str] = field(default_factory=list)
    failures: list[BaseException] = field(default_factory=list)

    def send(self, text: str) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(text)


@dataclass
class FakeSmtp:
    sent: list[AlertMessage] = field(default_factory=list)
    fail: bool = False

    def send(self, message: AlertMessage, *, to: str) -> None:
        del to
        if self.fail:
            raise smtplib.SMTPException("smtp down")
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _reset_health_and_metrics() -> Iterator[None]:
    TELEGRAM_ALERTS_TOTAL._metrics.clear()
    notifier._record_telegram_health(True)
    notifier._record_notifier_health(True)
    yield
    notifier._record_telegram_health(True)
    notifier._record_notifier_health(True)


@pytest.fixture(autouse=True)
def _active_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    # These unit tests isolate delivery, backoff and dedup. Fresh-data / hardware
    # relevance is exercised without mocks in test_telegram_relevance.py.
    monkeypatch.setattr(telegram_notifier, "delivery_relevance", lambda *args, **kwargs: "active")
    monkeypatch.setattr(telegram_notifier, "monotonic", lambda: 0)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "alert_smtp_host": "smtp.example.test",
        "alert_smtp_port": 587,
        "alert_smtp_user": "alert@example.test",
        "alert_smtp_password": "test-token",
        "alert_from": "alert@example.test",
        "alert_to": "ops@example.test",
        "admin_username": "admin",
        "admin_password_hash": "hash",
        "storcli_path": "/usr/local/sbin/storcli64",
        "metrics_interval_seconds": 300,
        "log_level": "INFO",
        "telegram_enabled": True,
        "telegram_chat_id": "-1001234567890",
    }
    base.update(overrides)
    return Settings(**base)


def _add_event(
    session: Session,
    *,
    occurred_at: datetime = NOW - timedelta(minutes=1),
    severity: str = "critical",
    category: str = "vd_state",
    subject: str = "VD 0",
    summary: str = "VD 0 state changed from Optl to Dgrd",
    after: dict[str, Any] | None = None,
) -> Event:
    event = Event(
        occurred_at=occurred_at,
        severity=severity,
        category=category,
        subject=subject,
        summary=summary,
        after_json={"state": "Dgrd"} if after is None and category == "vd_state" else after,
    )
    session.add(event)
    session.commit()
    return event


def _cycle(
    session: Session,
    sender: FakeSender,
    *,
    now: datetime = NOW,
    settings: Settings | None = None,
) -> TelegramCycleResult:
    return run_telegram_cycle(
        session,
        settings=settings or _settings(),
        now=now,
        transport_factory=lambda _settings: sender,
    )


def _delivery(session: Session, event: Event) -> EventDelivery | None:
    return session.scalars(
        select(EventDelivery).where(
            EventDelivery.event_id == event.id,
            EventDelivery.channel == "telegram",
        )
    ).one_or_none()


def _metric(result: str) -> float:
    return TELEGRAM_ALERTS_TOTAL.labels(result=result)._value.get()


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "category", "expected"),
    [
        ("critical", "vd_state", True),
        ("critical", "pd_state", True),
        ("critical", "media_errors", True),
        ("critical", "predictive_failures", True),
        ("critical", "smart_alert", True),
        ("critical", "cv_state", True),
        ("critical", "temperature", True),
        ("critical", "controller_temperature", True),
        ("critical", "system", False),
        ("warning", "temperature", True),
        ("warning", "controller_temperature", True),
        ("critical", "disk_space", False),
        ("warning", "vd_state", False),
        ("warning", "pd_state", False),
        ("warning", "other_errors", False),
        ("warning", "cv_state", False),
        ("warning", "foreign_config_detected", False),
        ("warning", "system", False),
        ("info", "vd_state", False),
        ("info", "temperature", False),
        ("info", "system", False),
    ],
)
def test_is_telegram_alert_policy(severity: str, category: str, expected: bool) -> None:
    event = Event(severity=severity, category=category, subject="x", summary="y")

    assert is_telegram_alert(event) is expected


def test_critical_event_is_sent_once_and_smtp_marker_untouched(session: Session) -> None:
    event = _add_event(session)
    sender = FakeSender()

    first = _cycle(session, sender)
    second = _cycle(session, sender, now=NOW + timedelta(minutes=1))

    assert first == TelegramCycleResult(sent=1, failed=0, skipped=0, deferred=0)
    assert second == TelegramCycleResult(sent=0, failed=0, skipped=0, deferred=0)
    assert len(sender.sent) == 1
    assert "дисковый массив потерял защиту от отказа" in sender.sent[0]
    delivery = _delivery(session, event)
    assert delivery is not None
    assert (delivery.status, delivery.attempts, delivery.delivered_at) == ("sent", 1, NOW)
    assert delivery.last_attempt_at == NOW
    assert delivery.detail is None
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at is None
    assert _metric("sent") == 1
    assert get_notifier_health() is True


def test_non_actionable_events_are_never_sent(session: Session) -> None:
    for severity, category, subject in (
        ("info", "vd_state", "VD 0"),
        ("info", "system", "Controller"),
        ("warning", "system", "Controller"),
        ("warning", "other_errors", "PD e252:s1"),
        ("warning", "foreign_config_detected", "Controller"),
        ("critical", "disk_space", "/var/lib/megaraid-dashboard"),
    ):
        _add_event(
            session,
            severity=severity,
            category=category,
            subject=subject,
            occurred_at=NOW - timedelta(hours=1),
        )
    sender = FakeSender()

    result = _cycle(session, sender)

    assert result == TelegramCycleResult(sent=0, failed=0, skipped=0, deferred=0)
    assert sender.sent == []
    assert session.scalars(select(EventDelivery)).all() == []


def test_active_events_older_than_three_hours_are_still_delivered(session: Session) -> None:
    old = _add_event(session, occurred_at=NOW - timedelta(hours=3, seconds=1))
    sender = FakeSender()

    result = _cycle(session, sender)

    assert result.sent == 1
    assert _delivery(session, old) is not None


def test_naive_now_is_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _cycle(session, FakeSender(), now=datetime(2026, 10, 25, 12, 0))


# ---------------------------------------------------------------------------
# Warning confirmation
# ---------------------------------------------------------------------------


def test_temperature_warning_waits_for_confirmation(session: Session) -> None:
    event = _add_event(
        session,
        severity="warning",
        category="temperature",
        subject="PD e252:s4",
        summary="Temperature reached warning threshold: 57 C",
        after={"state": "warning", "temperature_celsius": 57},
        occurred_at=NOW - timedelta(minutes=10),
    )
    sender = FakeSender()

    early = _cycle(session, sender)
    confirmed = _cycle(session, sender, now=NOW + timedelta(minutes=5))

    assert early == TelegramCycleResult(sent=0, failed=0, skipped=0, deferred=1)
    assert _delivery(session, event) is not None
    assert confirmed.sent == 1
    assert sender.sent[0].startswith("🟠 ВНИМАНИЕ")


def test_temperature_warning_that_cleared_is_not_sent(session: Session) -> None:
    warning = _add_event(
        session,
        severity="warning",
        category="controller_temperature",
        subject="Controller",
        summary="RoC temperature 96 C crossed warning threshold (95 C)",
        occurred_at=NOW - timedelta(minutes=30),
    )
    _add_event(
        session,
        severity="info",
        category="controller_temperature",
        subject="Controller",
        summary="RoC temperature 89 C cleared warning threshold",
        occurred_at=NOW - timedelta(minutes=20),
    )
    sender = FakeSender()

    result = _cycle(session, sender)

    assert result == TelegramCycleResult(sent=0, failed=0, skipped=1, deferred=0)
    delivery = _delivery(session, warning)
    assert delivery is not None
    assert (delivery.status, delivery.detail) == ("skipped", "not_confirmed")


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_repeat_of_same_incident_within_24h_is_skipped(session: Session) -> None:
    first = _add_event(session, occurred_at=NOW - timedelta(minutes=2))
    sender = FakeSender()
    _cycle(session, sender)
    repeat = _add_event(session, occurred_at=NOW + timedelta(hours=2))

    result = _cycle(session, sender, now=NOW + timedelta(hours=2, minutes=1))

    assert result == TelegramCycleResult(sent=0, failed=0, skipped=1, deferred=0)
    assert len(sender.sent) == 1
    assert _delivery(session, first) is not None
    delivery = _delivery(session, repeat)
    assert delivery is not None
    assert (delivery.status, delivery.detail) == ("skipped", "duplicate")


def test_repeat_after_24h_is_sent_again(session: Session) -> None:
    _add_event(session, occurred_at=NOW - timedelta(minutes=2))
    sender = FakeSender()
    _cycle(session, sender)
    later = NOW + timedelta(hours=24, minutes=1)
    _add_event(session, occurred_at=later - timedelta(minutes=1))

    result = _cycle(session, sender, now=later)

    assert result.sent == 1
    assert len(sender.sent) == 2


def test_worse_state_of_same_object_is_a_new_incident(session: Session) -> None:
    _add_event(session, occurred_at=NOW - timedelta(minutes=2))
    sender = FakeSender()
    _cycle(session, sender)
    _add_event(
        session,
        summary="VD 0 state changed from Dgrd to OfLn",
        after={"state": "OfLn"},
        occurred_at=NOW + timedelta(minutes=4),
    )

    result = _cycle(session, sender, now=NOW + timedelta(minutes=5))

    assert result.sent == 1
    assert "дисковый массив RAID недоступен" in sender.sent[1]


def test_duplicates_in_same_cycle_produce_one_message(session: Session) -> None:
    for offset in (3, 2):
        _add_event(
            session,
            category="media_errors",
            subject="PD e252:s4",
            summary=f"Media error count increased to {offset}",
            after={"media_errors": 10 - offset},
            occurred_at=NOW - timedelta(minutes=offset),
        )
    sender = FakeSender()

    result = _cycle(session, sender)

    assert result == TelegramCycleResult(sent=1, failed=0, skipped=1, deferred=0)


def test_different_cachevault_failures_are_separate_incidents(session: Session) -> None:
    _add_event(
        session,
        category="cv_state",
        subject="CacheVault",
        summary="CacheVault replacement required",
        after={"replacement_required": True},
        occurred_at=NOW - timedelta(minutes=2),
    )
    sender = FakeSender()
    _cycle(session, sender)
    _add_event(
        session,
        category="cv_state",
        subject="CacheVault",
        summary="CacheVault no longer detected",
        after={"present": False},
        occurred_at=NOW + timedelta(hours=1),
    )

    result = _cycle(session, sender, now=NOW + timedelta(hours=1, minutes=1))

    assert result.sent == 1
    assert len(sender.sent) == 2


def _store_slot0_serial(
    session: Session,
    snapshot: StorcliSnapshot,
    *,
    serial: str,
    captured_at: datetime,
) -> None:
    drives = list(snapshot.physical_drives)
    slot0 = next(i for i, d in enumerate(drives) if (d.enclosure_id, d.slot_id) == (252, 0))
    drives[slot0] = drives[slot0].model_copy(update={"serial_number": serial})
    insert_snapshot(
        session,
        snapshot.model_copy(update={"physical_drives": drives, "captured_at": captured_at}),
    )
    session.commit()


@pytest.mark.parametrize(("second_serial", "expected_sent"), [("SERIAL-OLD", 1), ("SERIAL-NEW", 2)])
def test_media_error_dedup_resets_for_replacement_disk(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    second_serial: str,
    expected_sent: int,
) -> None:
    _store_slot0_serial(
        session, sample_snapshot, serial="SERIAL-OLD", captured_at=NOW - timedelta(minutes=3)
    )
    _add_event(
        session,
        category="media_errors",
        subject="PD e252:s0",
        summary="Media error count increased from 0 to 4",
        after={"media_errors": 4},
        occurred_at=NOW - timedelta(minutes=2),
    )
    sender = FakeSender()
    _cycle(session, sender)
    _store_slot0_serial(
        session, sample_snapshot, serial=second_serial, captured_at=NOW + timedelta(hours=2)
    )
    _add_event(
        session,
        category="media_errors",
        subject="PD e252:s0",
        summary="Media error count is 1",
        after={"media_errors": 1},
        occurred_at=NOW + timedelta(hours=2, seconds=1),
    )

    _cycle(session, sender, now=NOW + timedelta(hours=2, minutes=1))

    assert len(sender.sent) == expected_sent


# ---------------------------------------------------------------------------
# Maintenance mode
# ---------------------------------------------------------------------------


def test_maintenance_mode_skips_events_permanently(session: Session) -> None:
    set_maintenance_state(
        session,
        active=True,
        expires_at=NOW + timedelta(minutes=30),
        started_by="admin",
    )
    session.commit()
    event = _add_event(session)
    sender = FakeSender()

    during = _cycle(session, sender)
    after = _cycle(session, sender, now=NOW + timedelta(hours=1))

    assert during == TelegramCycleResult(sent=0, failed=0, skipped=1, deferred=0)
    assert after.sent == 0
    assert sender.sent == []
    delivery = _delivery(session, event)
    assert delivery is not None
    assert (delivery.status, delivery.detail) == ("skipped", "maintenance")


def test_pending_retry_is_skipped_when_maintenance_starts(session: Session) -> None:
    event = _add_event(session)
    sender = FakeSender(failures=[TelegramSendError("timeout")])
    _cycle(session, sender)
    set_maintenance_state(
        session,
        active=True,
        expires_at=NOW + timedelta(hours=1),
        started_by="admin",
    )
    session.commit()

    result = _cycle(session, sender, now=NOW + timedelta(minutes=2))

    assert result == TelegramCycleResult(sent=0, failed=0, skipped=1, deferred=0)
    delivery = _delivery(session, event)
    assert delivery is not None
    assert (delivery.status, delivery.attempts, delivery.detail) == ("skipped", 1, "maintenance")
    assert delivery.next_attempt_at is None


def test_event_recorded_during_maintenance_stays_skipped_after_it_ends(session: Session) -> None:
    set_maintenance_state(
        session,
        active=True,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        started_by="admin",
    )
    event = record_event(
        session,
        severity="critical",
        category="pd_state",
        subject="PD e252:s4",
        summary="PD e252:s4 state changed from Onln to Offln",
        after={"state": "Offln"},
    )
    info = record_event(
        session,
        severity="info",
        category="pd_state",
        subject="PD e252:s4",
        summary="PD e252:s4 state changed from Offln to Onln",
    )
    set_maintenance_state(session, active=False, expires_at=None, started_by=None)
    session.commit()
    sender = FakeSender()

    result = run_telegram_cycle(
        session,
        settings=_settings(),
        now=datetime.now(UTC),
        transport_factory=lambda _settings: sender,
    )

    assert result == TelegramCycleResult(sent=0, failed=0, skipped=0, deferred=0)
    assert sender.sent == []
    delivery = _delivery(session, event)
    assert delivery is not None
    assert (delivery.status, delivery.detail) == ("skipped", "maintenance")
    assert _delivery(session, info) is None


@pytest.mark.parametrize("raw_state", ['{"active": true, "expires_at": 5}', "[]", "not json"])
def test_malformed_maintenance_state_never_blocks_event_recording(
    session: Session,
    raw_state: str,
) -> None:
    set_state(session, "maintenance_mode", raw_state)

    event = record_event(
        session,
        severity="critical",
        category="vd_state",
        subject="VD 0",
        summary="VD 0 state changed from Optl to Dgrd",
    )

    assert event.id is not None
    assert _delivery(session, event) is None


# ---------------------------------------------------------------------------
# Failures and retries
# ---------------------------------------------------------------------------


def test_failure_schedules_backoff_and_later_retry_succeeds(session: Session) -> None:
    event = _add_event(session)
    sender = FakeSender(failures=[TelegramSendError("HTTP 502")])

    failed = _cycle(session, sender)
    delivery = _delivery(session, event)
    assert delivery is not None
    assert (delivery.status, delivery.attempts, delivery.detail) == ("retry", 1, "HTTP 502")
    assert delivery.next_attempt_at == NOW + timedelta(seconds=60)
    assert failed == TelegramCycleResult(sent=0, failed=1, skipped=0, deferred=0)
    assert get_notifier_health() is False
    assert _metric("failed") == 1

    not_due = _cycle(session, sender, now=NOW + timedelta(seconds=59))
    assert not_due == TelegramCycleResult(sent=0, failed=0, skipped=0, deferred=0)
    assert get_notifier_health() is False

    retried = _cycle(session, sender, now=NOW + timedelta(seconds=60))
    session.refresh(delivery)
    assert retried.sent == 1
    assert (delivery.status, delivery.attempts, delivery.next_attempt_at) == ("sent", 2, None)
    assert delivery.detail is None
    assert len(sender.sent) == 1
    assert get_notifier_health() is True


def test_backoff_doubles_and_respects_rate_limit_retry_after(session: Session) -> None:
    event = _add_event(session)
    sender = FakeSender(
        failures=[
            TelegramSendError("timeout"),
            TelegramSendError("rate limited", retry_after_seconds=600),
        ]
    )

    _cycle(session, sender)
    _cycle(session, sender, now=NOW + timedelta(seconds=60))

    delivery = _delivery(session, event)
    assert delivery is not None
    assert delivery.attempts == 2
    assert delivery.next_attempt_at == NOW + timedelta(seconds=60 + 600)


def test_retries_continue_at_bounded_rate_after_eight_failures(session: Session) -> None:
    event = _add_event(session)
    sender = FakeSender(failures=[TelegramSendError("HTTP 500") for _ in range(8)])
    now = NOW

    for _ in range(8):
        _cycle(session, sender, now=now)
        delivery = _delivery(session, event)
        assert delivery is not None
        if delivery.next_attempt_at is not None:
            now = delivery.next_attempt_at

    assert delivery is not None
    assert (delivery.status, delivery.attempts, delivery.detail) == ("retry", 8, "HTTP 500")
    final = _cycle(session, sender, now=now + timedelta(hours=1))
    assert final.sent == 1
    assert len(sender.sent) == 1


def test_one_failure_stops_further_sends_in_the_cycle(session: Session) -> None:
    first = _add_event(session, occurred_at=NOW - timedelta(minutes=2))
    second = _add_event(
        session,
        category="pd_state",
        subject="PD e252:s4",
        summary="PD e252:s4 state changed from Onln to Failed",
        after={"state": "Failed"},
    )
    sender = FakeSender(failures=[TelegramSendError("timeout")])

    result = _cycle(session, sender)

    assert result == TelegramCycleResult(sent=0, failed=1, skipped=0, deferred=1)
    assert _delivery(session, second) is not None
    retried = _cycle(session, sender, now=NOW + timedelta(minutes=1))
    assert retried.sent == 2
    first_delivery = _delivery(session, first)
    assert first_delivery is not None
    assert first_delivery.status == "sent"


def test_sends_are_capped_per_cycle(session: Session) -> None:
    for slot in range(7):
        _add_event(
            session,
            category="pd_state",
            subject=f"PD e252:s{slot}",
            summary=f"PD e252:s{slot} state changed from Onln to Failed",
            after={"state": "Failed"},
        )
    sender = FakeSender()

    first = _cycle(session, sender)
    second = _cycle(session, sender, now=NOW + timedelta(minutes=1))

    assert first == TelegramCycleResult(sent=5, failed=0, skipped=0, deferred=2)
    assert second.sent == 2


def test_interrupted_send_is_retried(session: Session) -> None:
    event = _add_event(session)
    sender = FakeSender(failures=[RuntimeError("process killed mid-send")])

    with pytest.raises(RuntimeError):
        _cycle(session, sender)
    session.rollback()
    delivery = _delivery(session, event)
    assert delivery is not None
    assert (delivery.status, delivery.attempts) == ("sending", 1)

    result = _cycle(session, FakeSender(), now=NOW + timedelta(minutes=1))

    session.refresh(delivery)
    assert result == TelegramCycleResult(sent=1, failed=0, skipped=0, deferred=0)
    assert (delivery.status, delivery.attempts) == ("sent", 2)


def test_invalid_config_fails_closed_and_keeps_events_pending(session: Session) -> None:
    event = _add_event(session)

    def broken_factory(_settings: Settings) -> FakeSender:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN_FILE is not set")

    result = run_telegram_cycle(
        session,
        settings=_settings(),
        now=NOW,
        transport_factory=broken_factory,
    )

    assert result == TelegramCycleResult(sent=0, failed=0, skipped=0, deferred=0, config_error=True)
    assert _delivery(session, event) is None
    assert get_notifier_health() is False
    sender = FakeSender()
    assert _cycle(session, sender, now=NOW + timedelta(minutes=1)).sent == 1


def test_build_telegram_transport_uses_loaded_config(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TelegramConfig(chat_id="-1001234567890", token="t")
    monkeypatch.setattr(telegram_notifier, "load_telegram_config", lambda settings: config)

    transport = build_telegram_transport(_settings())

    assert isinstance(transport, TelegramTransport)
    assert transport.chat_id == "-1001234567890"


# ---------------------------------------------------------------------------
# Independence from SMTP
# ---------------------------------------------------------------------------


def test_smtp_outage_does_not_block_or_duplicate_telegram(session: Session) -> None:
    event = _add_event(session)
    smtp = FakeSmtp(fail=True)
    sender = FakeSender()
    settings = _settings()

    for minute in range(3):
        now = NOW + timedelta(minutes=minute)
        smtp_result = run_notifier_cycle(session, smtp, settings=settings, now=now)
        _cycle(session, sender, now=now)
        assert smtp_result.failed == 1

    assert len(sender.sent) == 1
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at is None

    smtp.fail = False
    run_notifier_cycle(session, smtp, settings=settings, now=NOW + timedelta(minutes=4))
    _cycle(session, sender, now=NOW + timedelta(minutes=4))
    assert len(smtp.sent) == 1
    assert len(sender.sent) == 1


def test_telegram_outage_does_not_block_or_duplicate_smtp(session: Session) -> None:
    event = _add_event(session)
    smtp = FakeSmtp()
    sender = FakeSender(failures=[TelegramSendError("timeout"), TelegramSendError("timeout")])
    settings = _settings()

    now = NOW
    for _ in range(3):
        _cycle(session, sender, now=now)
        run_notifier_cycle(session, smtp, settings=settings, now=now)
        delivery = _delivery(session, event)
        assert delivery is not None
        now = delivery.next_attempt_at or now + timedelta(minutes=1)

    assert len(smtp.sent) == 1
    assert len(sender.sent) == 1
    refreshed = session.get(Event, event.id)
    assert refreshed is not None
    assert refreshed.notified_at == NOW


# ---------------------------------------------------------------------------
# End to end: detector -> event -> Telegram
# ---------------------------------------------------------------------------


def test_degraded_array_from_detector_reaches_telegram(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    detector = EventDetector(
        temp_warning=100,
        temp_critical=110,
        temp_hysteresis=5,
        roc_temp_warning=95,
        roc_temp_critical=105,
        roc_temp_hysteresis=5,
    )
    current = sample_snapshot.model_copy(
        update={
            "virtual_drives": [
                VirtualDrive(
                    vd_id=0,
                    name="vd0",
                    raid_level="RAID5",
                    size_bytes=1,
                    state="Dgrd",
                    access="RW",
                    cache="RWBD",
                )
            ]
        }
    )
    for detected in detector.detect(None, current):
        record_event(
            session,
            severity=detected.severity,
            category=detected.category,
            subject=detected.subject,
            summary=detected.summary,
            before=detected.before,
            after=detected.after,
        )
    session.commit()
    sender = FakeSender()

    result = run_telegram_cycle(
        session,
        settings=_settings(),
        now=datetime.now(UTC),
        transport_factory=lambda _settings: sender,
    )

    assert result.sent == 1
    assert "дисковый массив потерял защиту от отказа" in sender.sent[0]
    assert "Детали: VD 0; VD 0 state is Dgrd;" in sender.sent[0]
