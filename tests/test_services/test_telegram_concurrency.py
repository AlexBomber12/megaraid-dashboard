"""Exercise real notifier DB phases while collection continues during Telegram I/O."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts.telegram import TelegramSendError
from megaraid_dashboard.db import get_sessionmaker
from megaraid_dashboard.db.dao import get_state, set_maintenance_state
from megaraid_dashboard.db.models import ControllerSnapshot
from megaraid_dashboard.services import notifier
from megaraid_dashboard.services import scheduler as scheduler_module
from megaraid_dashboard.services.telegram_notifier import run_telegram_cycle_async
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.test_services.test_scheduler_locking import _telegram_service
from tests.test_services.test_telegram_notifier import NOW, FakeSmtp, _add_event, _delivery
from tests.test_services.test_telegram_relevance import _save


@pytest.fixture(autouse=True)
def _health() -> Iterator[None]:
    notifier._record_notifier_health(True)
    notifier._record_telegram_health(True)
    yield
    notifier._record_notifier_health(True)
    notifier._record_telegram_health(True)


class _BlockedSender:
    def __init__(self, error: TelegramSendError | None = None) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.sent: list[str] = []
        self.error = error

    def send(self, text: str) -> None:
        self.started.set()
        if not self.release.wait(5):
            raise AssertionError("test did not release Telegram sender")
        if self.error is not None:
            raise self.error
        self.sent.append(text)


def _wire_sender(
    monkeypatch: pytest.MonkeyPatch,
    sender: _BlockedSender,
    smtp: FakeSmtp,
    sessions: list[Session],
) -> None:
    async def cycle(session: Session, **kwargs: Any) -> Any:
        sessions.append(session)
        return await run_telegram_cycle_async(
            session,
            transport_factory=lambda _: sender,
            **kwargs,
        )

    monkeypatch.setattr(scheduler_module, "run_telegram_cycle_async", cycle)
    monkeypatch.setattr(scheduler_module, "SmtpAlertTransport", lambda _: smtp)


async def test_collection_disk_checks_and_retention_continue_during_telegram_send(
    session: Session,
    engine: Engine,
    sample_snapshot: StorcliSnapshot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    event = _add_event(session)
    _save(session, sample_snapshot)
    service = _telegram_service(get_sessionmaker(engine))
    service.clock = lambda: NOW
    lock_path = str(tmp_path / "notifier.lock")
    monkeypatch.setattr(scheduler_module, "_LOCK_PATH_DEFAULT", lock_path)
    sender, smtp = _BlockedSender(), FakeSmtp()
    sessions: list[Session] = []
    _wire_sender(monkeypatch, sender, smtp, sessions)
    calls: list[str] = []

    async def collect(**kwargs: Any) -> tuple[StorcliSnapshot, dict[str, Any]]:
        del kwargs
        return sample_snapshot.model_copy(
            update={
                "captured_at": NOW,
                "controller": sample_snapshot.controller.model_copy(
                    update={"roc_temperature_celsius": 96}
                ),
            }
        ), {}

    def disk_check(*args: Any, **kwargs: Any) -> list[Any]:
        del args, kwargs
        calls.append("disk")
        return []

    def retention() -> tuple[int, int, int, int]:
        calls.append("retention")
        return 0, 0, 0, 0

    monkeypatch.setattr(scheduler_module, "collect_storcli_snapshot", collect)
    monkeypatch.setattr(scheduler_module, "check_data_partition_free_space", disk_check)
    monkeypatch.setattr(service, "_run_retention_transaction", retention)
    task = asyncio.create_task(service._run_notifier_once())
    try:
        assert await asyncio.to_thread(sender.started.wait, 2)
        assert not service._write_lock.locked()
        assert not sessions[0].in_transaction()
        # Process-level overlap protection remains held across the unlocked HTTP phase.
        assert scheduler_module._try_acquire_notifier_lock(lock_path) is None
        with get_sessionmaker(engine)() as observer:
            assert _delivery(observer, event).status == "sending"
        assert await asyncio.wait_for(service.run_once(), 2)
        await asyncio.wait_for(service._run_disk_space_monitor_once(), 2)
        await asyncio.wait_for(service.run_retention_once(), 2)
        assert calls == ["disk", "retention"]
        assert session.scalar(select(func.count()).select_from(ControllerSnapshot)) == 2
    finally:
        sender.release.set()
        await task
    session.expire_all()
    assert _delivery(session, event).status == "sent"
    assert len(sender.sent) == len(smtp.sent) == 1
    assert not service._write_lock.locked()
    fd = scheduler_module._try_acquire_notifier_lock(lock_path)
    assert fd is not None
    scheduler_module._release_notifier_lock(fd)


@pytest.mark.parametrize("change", ["recovered", "maintenance", "stale"])
async def test_next_pending_alert_rechecks_current_data_and_maintenance_after_http(
    session: Session,
    engine: Engine,
    sample_snapshot: StorcliSnapshot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: str,
) -> None:
    _add_event(session, occurred_at=NOW - timedelta(minutes=2))
    second = _add_event(
        session,
        category="pd_state",
        subject="PD e252:s0",
        after={"state": "Failed", "serial_number": "ORIGINAL"},
    )
    _save(session, sample_snapshot, drive={"state": "Failed"})
    service = _telegram_service(get_sessionmaker(engine))
    clock = NOW
    service.clock = lambda: clock
    monkeypatch.setattr(scheduler_module, "_LOCK_PATH_DEFAULT", str(tmp_path / "notifier.lock"))
    sender, smtp = _BlockedSender(), FakeSmtp()
    _wire_sender(monkeypatch, sender, smtp, [])
    task = asyncio.create_task(service._run_notifier_once())
    try:
        assert await asyncio.to_thread(sender.started.wait, 2)
        # Advance past freshness: old cycle time must not hide the newer snapshot/state.
        clock = NOW + timedelta(minutes=20)
        async with service._write_lock:
            if change == "recovered":
                _save(session, sample_snapshot, now=clock, drive={"state": "Onln"})
            elif change == "maintenance":
                set_maintenance_state(
                    session,
                    active=True,
                    expires_at=clock + timedelta(hours=1),
                    started_by="operator",
                )
                session.commit()
    finally:
        sender.release.set()
        await task
    session.expire_all()
    delivery = _delivery(session, second)
    assert delivery.attempts == 0
    assert (
        delivery.detail
        == {
            "recovered": "obsolete",
            "maintenance": "awaiting_fresh_data",
            "stale": "awaiting_fresh_data",
        }[change]
    )
    assert len(sender.sent) == 1


async def test_failed_unlocked_send_records_cooldown_and_smtp_still_runs(
    session: Session,
    engine: Engine,
    sample_snapshot: StorcliSnapshot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    event = _add_event(session)
    _save(session, sample_snapshot)
    service = _telegram_service(get_sessionmaker(engine))
    clock = NOW
    service.clock = lambda: clock
    monkeypatch.setattr(scheduler_module, "_LOCK_PATH_DEFAULT", str(tmp_path / "notifier.lock"))
    sender = _BlockedSender(TelegramSendError("rate limited", retry_after_seconds=600))
    smtp = FakeSmtp()
    _wire_sender(monkeypatch, sender, smtp, [])
    task = asyncio.create_task(service._run_notifier_once())
    try:
        assert await asyncio.to_thread(sender.started.wait, 2)
        async with service._write_lock:
            clock = NOW + timedelta(seconds=8)
    finally:
        sender.release.set()
        await task
    session.expire_all()
    assert _delivery(session, event).status == "retry"
    assert get_state(session, "telegram_not_before") == (clock + timedelta(seconds=600)).isoformat()
    assert len(smtp.sent) == 1
    assert not notifier.get_notifier_health()
    assert not service._write_lock.locked()


async def test_real_http_transport_deadline_does_not_block_collection_or_smtp(
    session: Session,
    engine: Engine,
    sample_snapshot: StorcliSnapshot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import httpx

    from megaraid_dashboard.alerts import telegram as transport_module
    from megaraid_dashboard.alerts.telegram import TelegramConfig, TelegramTransport
    from tests.test_alerts.test_telegram import CHAT_ID, FAKE_TOKEN, _Stream

    event = _add_event(session)
    _save(session, sample_snapshot)
    service = _telegram_service(get_sessionmaker(engine))
    service.clock = lambda: NOW
    monkeypatch.setattr(scheduler_module, "_LOCK_PATH_DEFAULT", str(tmp_path / "notifier.lock"))
    monkeypatch.setattr(transport_module, "_TOTAL_TIMEOUT_SECONDS", 0.15)
    stream = _Stream([b" " for _ in range(100)], delay=0.01)
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        started.set()
        return httpx.Response(200, stream=stream)

    sender = TelegramTransport(
        TelegramConfig(chat_id=CHAT_ID, token=FAKE_TOKEN),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    smtp = FakeSmtp()

    async def cycle(session: Session, **kwargs: Any) -> Any:
        return await run_telegram_cycle_async(session, transport_factory=lambda _: sender, **kwargs)

    monkeypatch.setattr(scheduler_module, "run_telegram_cycle_async", cycle)
    monkeypatch.setattr(scheduler_module, "SmtpAlertTransport", lambda _: smtp)
    task = asyncio.create_task(service._run_notifier_once())
    await asyncio.wait_for(started.wait(), 2)
    async with service._write_lock:
        assert not task.done()
    await asyncio.wait_for(task, 2)
    session.expire_all()
    assert _delivery(session, event).detail == "timeout"
    assert stream.closed and stream.reads < 100
    assert len(smtp.sent) == 1
    assert not service._write_lock.locked()
