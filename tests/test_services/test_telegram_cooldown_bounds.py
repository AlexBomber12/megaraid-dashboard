"""Untrusted cooldown hints cannot permanently silence the Telegram outbox."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts.telegram import TelegramConfig, TelegramTransport
from megaraid_dashboard.db.dao import get_state, set_state
from megaraid_dashboard.db.models import EventDelivery
from megaraid_dashboard.services import telegram_notifier
from megaraid_dashboard.services.notifier import get_notifier_health, run_notifier_cycle
from megaraid_dashboard.services.telegram_notifier import run_telegram_cycle
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.test_alerts.test_telegram import CHAT_ID, FAKE_TOKEN
from tests.test_services.test_telegram_notifier import (
    NOW,
    FakeSender,
    FakeSmtp,
    _add_event,
    _cycle,
    _delivery,
    _settings,
)
from tests.test_services.test_telegram_relevance import _save


@pytest.fixture(autouse=True)
def _freeze_elapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_notifier, "monotonic", lambda: 0)


@pytest.mark.parametrize("hint", [600, 3600, 3601, 315360000, 10**400])
def test_http_hint_bounds_both_durable_deadlines_and_recovers_after_restart(
    session: Session, sample_snapshot: StorcliSnapshot, hint: int
) -> None:
    event = _add_event(session)
    _save(session, sample_snapshot)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"parameters": {"retry_after": hint}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = TelegramTransport(TelegramConfig(chat_id=CHAT_ID, token=FAKE_TOKEN), client=client)
    result = run_telegram_cycle(
        session, settings=_settings(), now=NOW, transport_factory=lambda _: transport
    )
    assert result.failed == 1
    deadline = NOW + timedelta(seconds=min(hint, 3600))
    assert get_state(session, "telegram_not_before") == deadline.isoformat()
    delivery = _delivery(session, event)
    assert delivery is not None and delivery.next_attempt_at == deadline
    assert event.notified_at is None
    smtp = FakeSmtp()
    run_notifier_cycle(session, smtp, settings=_settings(), now=NOW)
    assert len(smtp.sent) == 1

    with Session(session.get_bind()) as restarted:
        second = _add_event(
            restarted,
            category="media_errors",
            subject="PD e252:s0",
            after={"media_errors": 2, "serial_number": "ORIGINAL"},
        )
        before = deadline - timedelta(seconds=1)
        _save(restarted, sample_snapshot, now=before, drive={"media_errors": 2})
        sender = FakeSender()
        assert _cycle(restarted, sender, now=before).sent == 0
        second_delivery = _delivery(restarted, second)
        assert second_delivery is not None and second_delivery.attempts == 0
        assert not get_notifier_health()
        # The original retries at the exact bounded deadline; the deferred second
        # incident follows on its own due time, without sliding the global deadline.
        _save(restarted, sample_snapshot, now=deadline, drive={"media_errors": 2})
        assert _cycle(restarted, sender, now=deadline).sent == 1
        later = deadline + timedelta(seconds=60)
        _save(restarted, sample_snapshot, now=later, drive={"media_errors": 2})
        assert _cycle(restarted, sender, now=later).sent == 1
        assert get_state(restarted, "telegram_not_before") == deadline.isoformat()
        assert len(sender.sent) == 2
        assert get_notifier_health()
    session.refresh(event)
    assert event.notified_at == NOW  # Telegram never changes the independent SMTP marker.


def test_preexisting_unbounded_deadlines_are_repaired_once_and_survive_restart(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    event = _add_event(session)
    _save(session, sample_snapshot)
    far_future = NOW + timedelta(days=3650)
    set_state(session, "telegram_not_before", far_future.isoformat())
    delivery = EventDelivery(
        event_id=event.id,
        channel="telegram",
        status="retry",
        attempts=1,
        last_attempt_at=NOW - timedelta(minutes=1),
        next_attempt_at=far_future,
        detail="rate limited",
    )
    session.add(delivery)
    session.commit()
    sender = FakeSender()
    assert _cycle(session, sender).sent == 0
    deadline = NOW + timedelta(hours=1)
    session.refresh(delivery)
    assert delivery.next_attempt_at == deadline
    assert get_state(session, "telegram_not_before") == deadline.isoformat()
    with Session(session.get_bind()) as restarted:
        before = deadline - timedelta(seconds=1)
        _save(restarted, sample_snapshot, now=before)
        assert _cycle(restarted, sender, now=before).sent == 0
        assert get_state(restarted, "telegram_not_before") == deadline.isoformat()
        _save(restarted, sample_snapshot, now=deadline)
        assert _cycle(restarted, sender, now=deadline).sent == 1
    assert len(sender.sent) == 1


@pytest.mark.parametrize(
    ("channel", "status", "attempts"),
    [("smtp", "retry", 1), ("telegram", "retry", 0), ("telegram", "sent", 1)],
)
def test_deadline_repair_leaves_other_channels_and_unattempted_or_terminal_rows_unchanged(
    session: Session, channel: str, status: str, attempts: int
) -> None:
    event = _add_event(session, severity="warning", category="disk_space")
    later = NOW + timedelta(days=3650)
    row = EventDelivery(
        event_id=event.id,
        channel=channel,
        status=status,
        attempts=attempts,
        next_attempt_at=later,
    )
    session.add(row)
    session.commit()
    assert _cycle(session, FakeSender()).sent == 0
    session.refresh(row)
    assert row.next_attempt_at == later
